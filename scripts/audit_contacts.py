#!/usr/bin/env python3
"""Audit the SES contact list for spam-relay abuse and (optionally) quarantine it.

Background: the public POST /subscribe endpoint was abused to inject harvested
third-party B2B addresses from Tor exit nodes and cheap VPS hosts. SES mailed
each one a confirmation link; some recipients clicked it, so those addresses
are now OPT_IN and received the real August 2026 edition.

IMPORTANT caveat about what `signup_ip` actually proves: it is written by
handlers/subscribe.py at *subscribe* time, not confirm time. handlers/confirm.py
records no IP at all (only `confirmed_at`). So a Tor/VPS `signup_ip` proves the
address was submitted to the form via Tor/a VPS -- i.e. it was
**attacker-injected** -- it does NOT prove who clicked the confirmation link or
that the click itself was automated. Never call these contacts "bot-confirmed";
call them "attacker-injected". A privacy-conscious human using Tor/a VPN to sign
up for a real newsletter is not impossible, which is why flagged addresses that
look academic/institutional are broken out separately for human review instead
of being treated as confirmed abuse.

What this script does:
  1. Pages through every contact on the SES v2 contact list (list_contacts).
  2. Classifies each contact's `monthly-newsletter` topic status from
     TopicPreferences (already present on the list_contacts response).
  3. For every OPT_IN contact, fetches AttributesData (get_contact -- this is
     NOT returned by list_contacts) to recover signup_ip/signup_at/confirmed_at,
     using a small thread pool with retries (transient AWS/DNS failures are
     common enough here that a naive single-shot fetch silently drops results).
  4. Classifies each OPT_IN contact's signup_ip as tor_exit (Tor DNS exit
     list), vps_datacenter (reverse-DNS hostname pattern match), or
     residential_institutional (neither).
  5. Prints a flagged-contact review table, summary counts, and a separate
     "needs human review" list for flagged addresses with academic-looking
     domains.
  6. With --apply (and --yes), flips flagged, non-excluded contacts to OPT_OUT
     on the topic. Default is always dry-run: no AWS writes. Never deletes a
     contact -- quarantine is OPT_OUT only, records are retained as evidence
     and to keep the 24h resend-cooldown working.

Usage:
    python scripts/audit_contacts.py                        # dry-run (default)
    python scripts/audit_contacts.py --exclude-file keep.txt
    python scripts/audit_contacts.py --apply --yes --exclude-file keep.txt
    python scripts/audit_contacts.py --selftest             # offline logic check
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import boto3
from botocore.exceptions import ClientError

OPT_IN = "OPT_IN"
OPT_OUT = "OPT_OUT"

ATTR_SIGNUP_IP = "signup_ip"
ATTR_SIGNUP_AT = "signup_at"
ATTR_CONFIRMED_AT = "confirmed_at"

# Hostname substrings that mark a reverse-DNS result as a datacenter/VPS host.
_VPS_PATTERNS = [
    "contaboserver",
    r"vmi\d+",
    "ovh",
    "digitalocean",
    "linode",
    "vultr",
    "hetzner",
    "netcup",
    "frantech",
    "ponynet",
    "buyvm",
    "choopa",
    "scaleway",
    "m247",
    "serverion",
    # Hyperscalers. A sign-up from one of these is still a datacenter sign-up,
    # but unlike Tor/Contabo it is weaker evidence: corporate VPN egress is
    # commonly hosted on AWS/Azure/GCP, so treat these as needing human eyes.
    "googleusercontent",
    "amazonaws",
    "cloudapp",
    "oraclecloud",
]
_VPS_RE = re.compile("|".join(_VPS_PATTERNS), re.IGNORECASE)

# Domain tokens/suffixes that mark a flagged address as plausibly
# academic/bioinformatics and worth a human's eyes before anything is changed.
_ACADEMIC_SUFFIX_RE = re.compile(r"\.edu$|\.ac\.[a-z]{2,3}$|\.edu\.[a-z]{2,3}$", re.IGNORECASE)
_ACADEMIC_TOKENS = [
    "ebi",
    "embl",
    "sanger",
    "broad",
    "nih",
    "crg",
    "scilifelab",
    "dkfz",
    "pasteur",
    "mpg",
    "univ",
    "uni-",
]


# ── Pure logic (no network/AWS) -- covered by --selftest ────────────────────


def topic_subscription_status(contact: dict[str, Any], topic_name: str) -> str:
    """Replicates the subscribe/confirm handlers' notion of topic status.

    list_contacts already returns TopicPreferences, so this needs no extra
    API call. A global unsubscribe wins; an explicit preference wins next;
    anything else defaults to OPT_OUT (contacts are always created OPT_OUT
    per upsert_unconfirmed).
    """
    if contact.get("UnsubscribeAll"):
        return OPT_OUT
    for pref in contact.get("TopicPreferences") or []:
        if pref.get("TopicName") == topic_name:
            return str(pref.get("SubscriptionStatus", OPT_OUT))
    for pref in contact.get("TopicDefaultPreferences") or []:
        if pref.get("TopicName") == topic_name:
            return str(pref.get("SubscriptionStatus", OPT_OUT))
    return OPT_OUT


def looks_academic(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    if not domain:
        return False
    if _ACADEMIC_SUFFIX_RE.search(domain):
        return True
    return any(token in domain for token in _ACADEMIC_TOKENS)


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else email.lower()


def _is_ipv4(ip: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address)
    except ValueError:
        return False


_tor_cache: dict[str, bool] = {}
_rdns_cache: dict[str, str | None] = {}


def is_tor_exit(ip: str) -> bool:
    """Query the Tor DNSEL exit list. Cached per distinct IP."""
    if ip in _tor_cache:
        return _tor_cache[ip]
    result = False
    if _is_ipv4(ip):
        reversed_octets = ".".join(reversed(ip.split(".")))
        query = f"{reversed_octets}.dnsel.torproject.org"
        try:
            answer = socket.gethostbyname(query)
            result = answer == "127.0.0.2"
        except OSError:
            result = False
    _tor_cache[ip] = result
    return result


def reverse_dns(ip: str) -> str | None:
    """Best-effort PTR lookup. Cached per distinct IP."""
    if ip in _rdns_cache:
        return _rdns_cache[ip]
    host: str | None
    try:
        host, _aliases, _addrs = socket.gethostbyaddr(ip)
    except OSError:
        host = None
    _rdns_cache[ip] = host
    return host


def classify_ip(ip: str) -> tuple[str, str | None]:
    """Returns (classification, reverse_dns_hostname_or_None)."""
    if not ip:
        return "unknown", None
    if is_tor_exit(ip):
        return "tor_exit", reverse_dns(ip)
    host = reverse_dns(ip)
    if host and _VPS_RE.search(host):
        return "vps_datacenter", host
    return "residential_institutional", host


# ── AWS I/O ───────────────────────────────────────────────────────────────


def list_all_contacts(client: Any, list_name: str) -> list[dict[str, Any]]:
    """Pages through every contact via list_contacts. Does not assume one page."""
    contacts: list[dict[str, Any]] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"ContactListName": list_name, "PageSize": 1000}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = client.list_contacts(**kwargs)
        contacts.extend(resp.get("Contacts", []))
        next_token = resp.get("NextToken")
        if not next_token:
            return contacts


def fetch_contact_attributes(client: Any, list_name: str, email: str, max_attempts: int = 8) -> dict[str, Any]:
    """get_contact + parse AttributesData, with retries. Never raises.

    list_contacts doesn't return AttributesData, so this is a separate call
    per OPT_IN contact. GetContact's per-account rate limit is tight enough
    that a handful of ThreadPoolExecutor workers routinely trip
    TooManyRequestsException -- a single-shot fetch (or a short retry budget)
    measurably loses results here, hence the longer backoff and higher
    attempt count specifically for throttling.
    """
    delay = 0.5
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = client.get_contact(ContactListName=list_name, EmailAddress=email)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "NotFoundException":
                return {}
            last_err = exc
            if code in {"TooManyRequestsException", "ThrottlingException"}:
                delay = min(delay * 2, 10.0)  # back off harder on throttling than on other errors
        except Exception as exc:  # noqa: BLE001 - keep the run alive on any transient failure
            last_err = exc
        else:
            raw = resp.get("AttributesData") or "{}"
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                return {}
            return data if isinstance(data, dict) else {}
        if attempt < max_attempts:
            time.sleep(delay)
            delay = min(delay * 1.5, 10.0)
    print(f"WARNING: could not fetch attributes for {email} after {max_attempts} attempts: {last_err}", file=sys.stderr)
    return {}


# ── Report building ─────────────────────────────────────────────────────────


def build_report(client: Any, list_name: str, topic_name: str, workers: int) -> dict[str, Any]:
    all_contacts = list_all_contacts(client, list_name)
    opt_in_emails = [c["EmailAddress"] for c in all_contacts if topic_subscription_status(c, topic_name) == OPT_IN]
    opt_out_count = len(all_contacts) - len(opt_in_emails)

    attrs_by_email: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_contact_attributes, client, list_name, email): email for email in opt_in_emails}
        for fut in as_completed(futures):
            attrs_by_email[futures[fut]] = fut.result()

    rows = []
    counts = {"tor_exit": 0, "vps_datacenter": 0, "residential_institutional": 0, "unknown": 0}
    for email in opt_in_emails:
        attrs = attrs_by_email.get(email, {})
        ip = str(attrs.get(ATTR_SIGNUP_IP) or "")
        classification, host = classify_ip(ip)
        counts[classification] += 1
        rows.append(
            {
                "email": email,
                "signup_ip": ip,
                "classification": classification,
                "rdns": host or "",
                "signup_at": attrs.get(ATTR_SIGNUP_AT) or "",
                "confirmed_at": attrs.get(ATTR_CONFIRMED_AT) or "",
            }
        )

    return {
        "total_contacts": len(all_contacts),
        "opt_in_count": len(opt_in_emails),
        "opt_out_count": opt_out_count,
        "counts": counts,
        "rows": rows,
    }


def print_report(report: dict[str, Any]) -> None:
    rows = report["rows"]
    flag_order = {"tor_exit": 0, "vps_datacenter": 1}
    flagged = sorted(
        (r for r in rows if r["classification"] in flag_order),
        key=lambda r: (flag_order[r["classification"]], r["email"]),
    )

    print(f"Total contacts on list: {report['total_contacts']}")
    print(f"  OPT_IN:  {report['opt_in_count']}")
    print(f"  OPT_OUT: {report['opt_out_count']}")
    print()
    print("OPT_IN signup_ip classification:")
    for key in ("tor_exit", "vps_datacenter", "residential_institutional", "unknown"):
        print(f"  {key:28s} {report['counts'][key]}")
    print()

    print(f"Flagged OPT_IN contacts ({len(flagged)}) -- signup_ip is Tor exit and/or datacenter/VPS:")
    header = (
        f"{'EMAIL':40s} {'SIGNUP_IP':16s} {'CLASS':22s} {'RDNS_HOSTNAME':35s} {'SIGNUP_AT':26s} {'CONFIRMED_AT':26s}"
    )
    print(header)
    print("-" * len(header))
    for r in flagged:
        print(
            f"{r['email'][:40]:40s} {r['signup_ip']:16s} {r['classification']:22s} "
            f"{(r['rdns'] or '-')[:35]:35s} {r['signup_at']:26s} {r['confirmed_at']:26s}"
        )
    print()

    borderline = [r for r in flagged if looks_academic(r["email"])]
    print(f"NEEDS HUMAN REVIEW -- flagged but academic/institutional-looking domain ({len(borderline)}):")
    if borderline:
        for r in borderline:
            print(f"  {r['email']:40s} {r['classification']:22s} {r['signup_ip']:16s} rdns={r['rdns'] or '-'}")
    else:
        print("  (none)")
    print(
        "  Note: a Tor/VPS signup_ip proves the address was INJECTED via that network at "
        "subscribe time, not that a human didn't later click the real confirmation link. "
        "Treat these as evidence, not proof -- confirm manually before excluding or quarantining."
    )
    print()

    unflagged = [r for r in rows if r["classification"] not in flag_order]
    _print_domain_profile("Flagged cohort", flagged)
    _print_domain_profile("Unflagged (residential/institutional) cohort", unflagged)


def _print_domain_profile(label: str, rows: list[dict[str, Any]], top: int = 8) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        d = email_domain(r["email"])
        counts[d] = counts.get(d, 0) + 1
    top_domains = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    print(f"{label} -- top domains ({len(rows)} contacts, {len(counts)} distinct domains):")
    for domain, n in top_domains:
        print(f"  {n:3d}  {domain}")
    print()


# ── Apply (quarantine) ──────────────────────────────────────────────────────


def apply_quarantine(
    client: Any,
    list_name: str,
    topic_name: str,
    report: dict[str, Any],
    exclude: set[str],
    dry_run: bool,
) -> None:
    flag_order = {"tor_exit", "vps_datacenter"}
    targets = [r for r in report["rows"] if r["classification"] in flag_order and r["email"] not in exclude]
    skipped = [r for r in report["rows"] if r["classification"] in flag_order and r["email"] in exclude]

    action = "[DRY RUN] Would set" if dry_run else "Setting"
    for r in targets:
        print(f"{action} {r['email']} -> OPT_OUT on '{topic_name}' (was {r['classification']}, ip={r['signup_ip']})")
        if not dry_run:
            contact = client.get_contact(ContactListName=list_name, EmailAddress=r["email"])
            attributes_data = contact.get("AttributesData")
            kwargs: dict[str, Any] = {
                "ContactListName": list_name,
                "EmailAddress": r["email"],
                "TopicPreferences": [{"TopicName": topic_name, "SubscriptionStatus": OPT_OUT}],
            }
            if attributes_data is not None:
                kwargs["AttributesData"] = attributes_data
            client.update_contact(**kwargs)

    for r in skipped:
        print(f"[EXCLUDED] leaving {r['email']} untouched (in exclude file)")

    print()
    print(f"{'Would quarantine' if dry_run else 'Quarantined'}: {len(targets)}; excluded/spared: {len(skipped)}")


# ── Self-test (offline, no AWS/network) ──────────────────────────────────────


def _selftest() -> None:
    assert topic_subscription_status({"UnsubscribeAll": True}, "t") == OPT_OUT
    assert (
        topic_subscription_status({"TopicPreferences": [{"TopicName": "t", "SubscriptionStatus": "OPT_IN"}]}, "t")
        == OPT_IN
    )
    assert topic_subscription_status({}, "t") == OPT_OUT

    assert looks_academic("thain@ebi.ac.uk") is True
    assert looks_academic("someone@sanger.ac.uk") is True
    assert looks_academic("questions@valvesoftware.com") is False
    assert looks_academic("student@harvard.edu") is True

    assert _VPS_RE.search("vmi123456.contaboserver.net") is not None
    assert _VPS_RE.search("mail.google.com") is None

    assert email_domain("foo@Bar.COM") == "bar.com"
    assert email_domain("not-an-email") == "not-an-email"

    assert _is_ipv4("1.2.3.4") is True
    assert _is_ipv4("::1") is False

    print("selftest OK")


# ── CLI ──────────────────────────────────────────────────────────────────────


def _load_exclude_file(path: str | None) -> set[str]:
    if not path:
        return set()
    with open(path, encoding="utf-8") as fh:
        return {line.strip().lower() for line in fh if line.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="nf-core", help="AWS CLI profile (default: nf-core)")
    parser.add_argument("--region", default="eu-west-1", help="AWS region (default: eu-west-1)")
    parser.add_argument("--contact-list", default="nf-core-newsletter", help="SES contact list name")
    parser.add_argument("--topic", default="monthly-newsletter", help="SES topic name")
    parser.add_argument("--workers", type=int, default=4, help="Thread pool size for get_contact fetches")
    parser.add_argument("--apply", action="store_true", help="Actually flip flagged contacts to OPT_OUT")
    parser.add_argument("--yes", action="store_true", help="Required alongside --apply to confirm intent")
    parser.add_argument("--exclude-file", help="Newline-separated emails to spare from quarantine")
    parser.add_argument("--selftest", action="store_true", help="Run offline logic checks and exit")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return 0

    if args.apply and not args.yes:
        print("ERROR: --apply requires --yes to confirm you intend to write to production.", file=sys.stderr)
        return 2

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    client = session.client("sesv2")

    report = build_report(client, args.contact_list, args.topic, args.workers)
    print_report(report)

    exclude = _load_exclude_file(args.exclude_file)
    apply_quarantine(client, args.contact_list, args.topic, report, exclude, dry_run=not args.apply)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
