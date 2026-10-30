"""Runtime configuration: business-rule thresholds and secret lookup."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("finpipe.config")

DEV_SALT = "dev-only-salt-do-not-use-with-real-data"


@dataclass(frozen=True)
class Thresholds:
    """Business-rule thresholds. Passed explicitly so tests can override them."""

    # fraud heuristics
    high_amount_usd: float = 5000.0
    velocity_window_seconds: int = 600
    velocity_count: int = 5
    night_start_hour: int = 1  # inclusive
    night_end_hour: int = 5  # exclusive
    night_min_usd: float = 1000.0
    # validation
    stale_days: int = 7
    # quality gate: a run with a higher quarantine rate is reported as degraded
    max_quarantine_rate: float = 0.15


def get_pii_salt(ssm_param: str | None = None) -> str:
    """Resolve the secret used to key account-number hashes.

    Order: SSM parameter (AWS) > PII_SALT environment variable > development default.
    An SSM lookup failure is raised, never silently replaced by the default.
    """
    if ssm_param:
        import boto3

        response = boto3.client("ssm").get_parameter(Name=ssm_param, WithDecryption=True)
        return response["Parameter"]["Value"]
    from_env = os.environ.get("PII_SALT")
    if from_env:
        return from_env
    logger.warning("PII_SALT is not set; using a development salt. Do not use real data.")
    return DEV_SALT
