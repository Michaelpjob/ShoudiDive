# pipeline/lib — shared infrastructure for the data pipeline.
#
# Created 2026-05-09 as part of the Tier-2 architecture work. The goal
# is to centralize patterns currently re-implemented across every
# fetch_*.py script (HTTP retry, ERDDAP request shape, PNG encoding,
# range/scale contracts) into a single library with one source of truth.
#
# Today this package only carries `layer_spec.py` — the contract that
# both the pipeline encoder side and the frontend decoder side need to
# agree on. Migrations of the existing fetchers will land in follow-up
# PRs; this is just the contract.
