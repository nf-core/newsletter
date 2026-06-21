"""Fetch SSM SecureString secrets, cached for the life of the Lambda container."""

from __future__ import annotations

import boto3

from nf_core_newsletter import config

_cache: dict[str, str] = {}


def get_secret(param_name: str) -> str:
    """Return the (decrypted) value of an SSM SecureString parameter.

    Cached per parameter name so repeated calls within one warm Lambda container
    don't re-hit SSM.
    """
    if param_name not in _cache:
        ssm = boto3.client("ssm", region_name=config.require("AWS_REGION"))
        resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _cache[param_name] = resp["Parameter"]["Value"]
    return _cache[param_name]
