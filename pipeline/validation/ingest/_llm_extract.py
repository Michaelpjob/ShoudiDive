"""LLM-assisted observation extractor.

Uses Anthropic's Haiku model to turn unstructured CA fishing/dive
report prose into a list of observation dicts. The system prompt
hard-locks the output to a JSON array; we still validate per-record
shape before any insertion.

Cost: Haiku is ~$0.001 per report. With ~30 prose-source pages
ingested per day, that's roughly $11/year — well under the
infrastructure floor.

Disabled gracefully if no ``ANTHROPIC_API_KEY`` is set: the helper
returns an empty list and the orchestrator just sees zero
observations from the LLM-extracted sources, while the
regex-extracted sources (Just Get Wet, CDIP) keep working. That's
the right behaviour for local dev without an API key.
"""
from __future__ import annotations

import json
import os


EXTRACTION_PROMPT = """You extract water-condition observations from California
fishing and dive reports. Given a single report's text, return a JSON array
of observations.

Each observation must have these exact fields (omit a field by setting
it to null, but always include the key):

  {
    "spot_name":            "string — exact spot name as written, e.g. 'Pt Loma kelp', 'Cortes Bank', 'La Jolla Cove'",
    "observed_secchi_ft":   number or null — visibility in feet (NOT meters; convert if needed),
    "observed_sst_f":       number or null — water temperature in °F (convert from °C if needed),
    "observed_swell_ft":    number or null — significant wave height in feet,
    "raw_excerpt":          "the sentence or two it came from, max 280 chars"
  }

Rules:
- If the report covers multiple spots, return one observation per spot.
- Convert m to ft (× 3.281); convert °C to °F (× 9/5 + 32).
- If a number is given as a range like "25-30 ft", use the midpoint (27.5).
- If you can't tell what spot was meant, skip it. Do not guess coordinates.
- Skip observations older than 24 hours (only include today / this trip).
- Visibility values must be plausible: 1-100 ft. Reject anything outside that.
- Water temp must be plausible: 50-80 °F. Reject anything outside that.
- A spot must be paired with at least one quantitative observation
  (visibility, water temp, or swell). If a post only mentions a spot
  in passing — "I caught a fish at the cove", "we saw kelp at the wall" —
  do NOT emit a row. The downstream scoring only uses rows with at least
  one observed_* number; spot-only rows are pure noise.
- If no quantitative observations exist, return an empty array [].
- Output ONLY the JSON array, no preamble or markdown fences.
"""


_MODEL = "claude-haiku-4-5"
_MAX_INPUT_CHARS = 8000  # cost cap; reports rarely exceed this anyway
_MAX_TOKENS = 1024


def is_enabled() -> bool:
    """True iff we have an API key — every prose scraper checks this
    before fetching to avoid wasting bandwidth on a request whose
    extraction will be skipped."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def extract_from_prose(report_text: str) -> list[dict]:
    """Send prose to Haiku, parse the JSON array, return validated obs dicts.

    Returns an empty list on any failure (no API key, network error,
    JSON parse error, validation failure). The orchestrator's per-
    scraper try/except already swallows exceptions, but failing
    silently here is cleaner — it lets a single bad report not poison
    the rest of the source's output.
    """
    if not is_enabled():
        return []

    text = (report_text or "").strip()
    if not text:
        return []
    text = text[:_MAX_INPUT_CHARS]

    # Lazy import — anthropic isn't a hard dependency for the loop;
    # missing package just means LLM-sourced obs are 0 this run.
    try:
        from anthropic import Anthropic  # noqa: PLC0415
    except ImportError:
        print("  llm: anthropic package not installed, skipping")
        return []

    client = Anthropic()

    try:
        msg = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  llm: API call failed: {exc.__class__.__name__}: {exc}")
        return []

    # Take the first text block; sometimes Haiku returns thinking blocks
    # or tool blocks alongside, so iterate to be safe.
    raw = ""
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            raw = block.text.strip()
            break
    if not raw:
        return []

    # Strip any markdown fence the model might have added despite the
    # instruction. Anthropic tends to be obedient on this but
    # belt-and-suspenders is cheap.
    #
    # 2026-05-21: also handle the case where the model returns a valid
    # JSON array followed by prose explanation (markdown fence at end
    # then a paragraph). Observed failure: raw was `[]\n```\n\nThe
    # report contains water condition observations but no identifiable
    # spot name. Without knowing which California location...`. The
    # original `json.loads(raw)` choked on the trailing text. Extract
    # just the first JSON array via bracket-matching so prose around
    # it doesn't break the parser.
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    # Locate the first JSON array via bracket matching. Tolerates prose
    # before or after the array, plus stray closing fences.
    json_text = raw
    arr_start = raw.find("[")
    if arr_start >= 0:
        depth = 0
        for i in range(arr_start, len(raw)):
            ch = raw[i]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    json_text = raw[arr_start : i + 1]
                    break

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        print(f"  llm: JSON parse failed ({exc}); raw={raw[:200]!r}")
        return []

    if not isinstance(parsed, list):
        return []
    return [o for o in parsed if validate_extraction(o)]


def validate_extraction(obs) -> bool:
    """Reject anything that doesn't match the prompt's contract.

    A row passes only if (a) the spot name is non-empty, (b) every
    populated observed_* field is in plausible range, and (c) at
    least one observed_* field is populated. Without (c), forum
    posts that *mention* a spot in passing — "fly-lining sardines
    in front of the cove" — generate spot-only rows that pollute
    the obs table without producing any score-able signal.
    """
    if not isinstance(obs, dict):
        return False
    name = obs.get("spot_name")
    if not isinstance(name, str) or not name.strip():
        return False
    populated = 0
    for field, lo, hi in (
        ("observed_secchi_ft", 1.0, 100.0),
        ("observed_sst_f",     50.0, 80.0),
        ("observed_swell_ft",  0.0,  30.0),
    ):
        v = obs.get(field)
        if v is None:
            continue
        if not isinstance(v, (int, float)):
            return False
        if not (lo <= v <= hi):
            return False
        populated += 1
    if populated == 0:
        return False
    excerpt = obs.get("raw_excerpt")
    if excerpt is not None and not isinstance(excerpt, str):
        return False
    return True
