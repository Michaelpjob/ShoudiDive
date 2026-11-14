"""Daily written reports: the facts layer behind shouldidive.com/reports/.

The report answers "can I go?" (shore dive, boat by range, boat dive), "what
is the water doing?" and "what do divers need to know?" from official
forecasts, live buoys and our own published layers. Design and evidence base:
Projects/ShouldIDive/fish-reports-research.md (2026-10-15).

Layout:
  nws_text.py      parsers for NWS/NHC text products (CWF, SRF, OFF)
  sources.py       fetchers; every input is a Source record with its time
  thresholds.py    the threshold tables (provisional, see knobs_registry)
  sites.py         shore-dive sites and boat ranges
  physics.py       wave orbital velocity, sun, moon
  season.py        regulation calendar (verified against CDFW pages)
  verdicts.py      threshold tables -> within / marginal / outside / unknown
  render.py        deterministic sentences from the facts
  build_report.py  orchestration; writes public/data/reports/

Honesty rules (docs/STRICT-SCIENCE.md S1/S6, applied to text): every number
in the report carries its source and time; missing or stale inputs show as
"Not enough data", never as within thresholds; the word "safe" never appears.
"""
