"""CLI entrypoint that validates the published manifest.json against
the contract in pipeline/lib/layer_spec.py.

Usage:
    python -m pipeline.validate_manifest                   # validates public/data/manifest.json
    python -m pipeline.validate_manifest path/to/other.json

Exit codes:
    0  every layer matches the LayerSpec contract
    1  at least one violation (printed to stdout)
    2  manifest file missing / unreadable / not JSON

Wired into:
    .github/workflows/dev-checks.yml — manifest-validate job runs this
    on every PR. Catches range/scale drift between fetchers and the
    frontend BEFORE the manifest reaches the deployed bundle.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.lib.layer_spec import validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a published manifest.json against the LayerSpec contract."
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        default="public/data/manifest.json",
        help="Path to the manifest.json (default: public/data/manifest.json)",
    )
    args = parser.parse_args(argv)

    path = Path(args.manifest)
    if not path.exists():
        print(f"validate_manifest: file not found: {path}", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"validate_manifest: {path} is not valid JSON: {e}", file=sys.stderr)
        return 2

    issues = validate(manifest)
    if not issues:
        print(f"validate_manifest: {path} matches the LayerSpec contract.")
        return 0

    print(f"validate_manifest: {len(issues)} violation(s) in {path}:")
    for issue in issues:
        print(f"  - {issue}")
    print()
    print(
        "Each violation indicates the encoder side (a fetch_*.py script) and "
        "the contract in pipeline/lib/layer_spec.py have drifted. Fix the script "
        "OR update the LayerSpec entry — whichever is wrong."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
