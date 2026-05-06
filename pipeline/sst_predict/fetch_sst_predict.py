"""CLI orchestrator for sst_predict — NOT WIRED TO CI YET.

Run manually for development:
  python -m pipeline.sst_predict.fetch_sst_predict

When phase 2 lands, this gets added to refresh-data.yml as a soft-fail
step right after the existing SST fetch. When phase 3 lands, the same
script writes the 5d forecast outputs.

Status: framework. Implementation in phase 2.
"""
from __future__ import annotations

import sys
from datetime import date

from . import predict


def main() -> int:
    """Run predict.predict_all() and write all outputs to public/data.

    Phase-2 minimal implementation:
      result = predict.predict_all(today=date.today())
      print(f"coverage={result['now']['coverage_frac']:.0%} "
            f"mean_age={result['now']['mean_age_days']:.1f}d")
      return 0 unless something critical errored

    The CI step will use ``continue-on-error: true`` until the
    validation runway has shown the predictor is reliable enough to
    block deploys on. Same playbook as viz_predict's introduction.
    """
    raise NotImplementedError(
        "phase-2: wire predict.predict_all() and write outputs")


if __name__ == "__main__":
    sys.exit(main())
