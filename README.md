# nf-core newsletter

Distribution service for the nf-core monthly newsletter. The newsletter
_content_ is built by [nf-core/website](https://github.com/nf-core/website)
(`/newsletter/<year>/<month>/email`); this service takes that rendered HTML and
emails it to a self-service mailing list.

**Phase 1 (email) only.** Phase 2 (LinkedIn) is not yet implemented.

## Architecture

Fully serverless on AWS (`eu-west-1`), deployed with AWS CDK (Python). Amazon
**SES list management** is the backbone — it owns the contact list, the hosted
unsubscribe page, suppression, and bounce/complaint handling, so there is **no
database and no bounce Lambda**. We build only the double-opt-in flow SES lacks.

```
sign-up form (nf-co.re)
        │  POST { email }
        ▼
API Gateway HTTP API ──► subscribe Lambda ─► SES CreateContact (OPT_OUT)
        │                                     + SES SendEmail (confirm link)
        │  GET /confirm?token=…
        └──────────────► confirm  Lambda ─► SES UpdateContact (topic OPT_IN)

EventBridge Scheduler (monthly) ─► send Lambda
        ├─ fetch /newsletter/<y>/<m>/email HTML from nf-co.re
        ├─ SES ListContacts (subscribed to the newsletter topic)
        └─ SES SendEmail per recipient (ListManagementOptions → unsubscribe +
           suppression handled by SES automatically)
```

| Component       | Resource                                                          |
| --------------- | ----------------------------------------------------------------- |
| Contact storage | SES contact list + `monthly-newsletter` topic                     |
| Sign-up API     | API Gateway HTTP API + `subscribe` / `confirm` Lambdas            |
| Monthly send    | EventBridge Scheduler → `send` Lambda                             |
| Email sending   | SES (API v2) + a configuration set                                |
| Secrets         | Pre-created SSM SecureString params under `/nf-core-newsletter/*` |

The double opt-in (pending → confirmed) keeps the list GDPR-compliant; consent
timestamp and source IP are stored on the SES contact, and every send carries a
working one-click unsubscribe.

## Layout

```
infra/                       CDK (Python) app — one stack
  app.py                     account/region + stack instantiation
  stacks/newsletter_stack.py the whole stack
src/nf_core_newsletter/
  config.py                  env-var config (injected by the stack)
  handlers/                  Lambda entry points (subscribe, confirm, send)
tests/                       pytest
.github/workflows/           ci.yml (lint/type/test) + deploy.yml (CDK on merge)
```

## Development

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check src/ tests/ && ruff format --check src/ tests/
mypy src/
pytest -q
```

CDK:

```bash
pip install -r infra/requirements.txt
cd infra && cdk synth
```

## Deployment

Push/merge to `main` → `.github/workflows/deploy.yml` runs CI, assumes the AWS
deploy role via GitHub Actions OIDC (repo secret `AWS_ROLE_ARN`), and runs
`cdk deploy --require-approval never`. Same pattern as
[nf-core/slackbot](https://github.com/nf-core/slackbot), minus the Docker/GHCR
build and ECS restart (compute here is Lambda, deployed by CDK).

## Prerequisites (provisioned out-of-band, not by `cdk deploy`)

- A **verified SES sending identity** for the From domain, with the required DNS
  records (DKIM, SPF, DMARC; optionally a custom MAIL FROM subdomain).
- **SES production access** on the account (sandbox only sends to verified
  addresses).
- The **SSM SecureString** `/nf-core-newsletter/CONFIRM_TOKEN_SECRET` — the HMAC
  key used to sign confirmation tokens.
- The **`AWS_ROLE_ARN` repo secret**, pointing at the GitHub Actions OIDC deploy
  role.

The SES contact list, its `monthly-newsletter` topic, and the configuration set
are created by CDK (the contact list is retained on stack deletion). SES allows
one contact list per account, so this stack owns it.

## Sending

The `send` Lambda sends one SES `SendEmail` per subscribed contact, paced to the
account's SES send rate, with `ListManagementOptions` set so SES injects the
unsubscribe link and applies suppression. A single monthly invocation covers the
expected list size; very large lists would need an SES rate increase and/or
fanning the send across invocations.

The fetched `/email` HTML is sent whole; `content.absolutize_urls` rewrites the
website's root-relative image and asset URLs to absolute `https://nf-co.re/…` so
they render in email clients.
