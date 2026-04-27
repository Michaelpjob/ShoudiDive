"""Entry point so ``python -m pipeline.validation.ingest`` works.

The orchestrator logic lives in ``__init__.py`` (where it can also be
imported by other tooling), but ``python -m`` against a package
specifically looks for ``__main__.py``. This is the thinnest possible
shim — anything more would split the orchestrator definition.
"""
from __future__ import annotations

import sys

from . import run_all


def main() -> int:
    new = run_all()
    # The ingest cron is healthy whether or not new obs landed in this
    # tick — empty cycles are normal (e.g. all sources still inside
    # their host_rate_limit_s window from the previous run). Exit 0
    # so GitHub Actions doesn't mark the workflow as failed for what
    # is actually success.
    return 0 if new is not None else 1


if __name__ == "__main__":
    sys.exit(main())
