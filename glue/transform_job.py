"""AWS Glue entry point (Glue Python shell job).

Glue passes job arguments as ``--name value`` pairs. The finpipe package is delivered to the
job as a zip through the ``--extra-py-files`` job parameter, so this file stays tiny and all
the logic lives in (and is unit tested as) the finpipe package.

Why a Python shell job and not Spark: a daily batch of a few MB to a few hundred MB fits in
pandas on one worker, and a Python shell job costs a fraction of a Spark job. The transform
code only touches storage through finpipe.storage, so moving to Spark later is a contained
change (see README, "Scaling beyond one worker").
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from finpipe.config import Thresholds, get_pii_salt
from finpipe.logging_setup import configure_logging
from finpipe.pipeline import run_transform
from finpipe.storage import storage_from_uri

logger = logging.getLogger("finpipe.glue")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Transform one batch date")
    parser.add_argument("--dt", required=True, help="batch date, YYYY-MM-DD")
    parser.add_argument("--run_id", required=True, help="unique id of this run (the Step Functions execution name)")
    parser.add_argument("--lake_uri", required=True, help="s3://bucket[/prefix]")
    parser.add_argument("--pii_salt_ssm_param", default="", help="SSM parameter holding the PII hashing key")
    parser.add_argument("--max_quarantine_rate", type=float, default=Thresholds().max_quarantine_rate)
    # Glue adds its own arguments (--JOB_NAME, --TempDir, ...); ignore anything we do not know.
    args, _unknown = parser.parse_known_args(argv)

    configure_logging(json_logs=True)
    summary = run_transform(
        storage_from_uri(args.lake_uri),
        date.fromisoformat(args.dt),
        run_id=args.run_id,
        salt=get_pii_salt(args.pii_salt_ssm_param or None),
        thresholds=Thresholds(max_quarantine_rate=args.max_quarantine_rate),
    )
    logger.info("job complete", extra={"summary": json.dumps(summary.to_dict())})
    return 0


if __name__ == "__main__":
    sys.exit(main())
