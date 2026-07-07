"""Render hindcast_residuals.jsonl into a static, self-contained HTML report.

Pure stdlib + inline SVG — no JS libraries, no external assets, so the
output is a single file you can open anywhere. This is the "visualize +
understand" surface for the validation loop: predicted vs observed
clarity over the backfilled history, per spot, with calibration coverage
and an error timeline.

Run:  python -m validation.hindcast_report
Out:  data/hindcast_report.html
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
RESID_PATH = DATA_DIR / "hindcast_residuals.jsonl"
OUT_PATH = DATA_DIR / "hindcast_report.html"

GOOD = "#16a34a"   # inside band / over-predict bar
BAD = "#dc2626"    # outside band / over-predict
UNDER = "#2563eb"  # under-predict
INK = "#1f2937"
MUTE = "#6b7280"
GRID = "#e5e7eb"
CAL_TARGET = 0.80


def _load() -> list[dict]:
    if not RESID_PATH.exists():
        raise SystemExit(f"no residuals at {RESID_PATH} — run `python -m validation.hindcast` first")
    rows = []
    for line in RESID_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    res = [r["residual_ft"] for r in rows]
    obs = [r["observed_ft"] for r in rows]
    pred = [r["predicted_p50_ft"] for r in rows]
    inb = [1.0 if r["in_p10_p90"] else 0.0 for r in rows]
    rmse = math.sqrt(sum(x * x for x in res) / n)
    bias = sum(res) / n
    mae = sum(abs(x) for x in res) / n
    cal = sum(inb) / n
    r_p = _pearson(pred, obs)
    return {"n": n, "rmse": rmse, "bias": bias, "mae": mae, "cal": cal, "r": r_p}


def _pearson(a: list[float], b: list[float]):
    n = len(a)
    if n < 2:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sa = sum((x - ma) ** 2 for x in a)
    sb = sum((x - mb) ** 2 for x in b)
    if sa <= 0 or sb <= 0:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(sa * sb)


# ---- SVG builders ----------------------------------------------------

def scatter_svg(rows: list[dict], hi: float = 45.0) -> str:
    W, H, pad = 420, 420, 54
    x0, y0, x1, y1 = pad, 16, W - 16, H - pad
    def px(v): return x0 + (v / hi) * (x1 - x0)
    def py(v): return y1 - (v / hi) * (y1 - y0)
    parts = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="system-ui,sans-serif">']
    # grid + ticks
    for t in range(0, int(hi) + 1, 10):
        parts.append(f'<line x1="{px(t):.1f}" y1="{y0}" x2="{px(t):.1f}" y2="{y1}" stroke="{GRID}"/>')
        parts.append(f'<line x1="{x0}" y1="{py(t):.1f}" x2="{x1}" y2="{py(t):.1f}" stroke="{GRID}"/>')
        parts.append(f'<text x="{px(t):.1f}" y="{y1+16}" font-size="11" fill="{MUTE}" text-anchor="middle">{t}</text>')
        parts.append(f'<text x="{x0-8}" y="{py(t)+4:.1f}" font-size="11" fill="{MUTE}" text-anchor="end">{t}</text>')
    # y = x reference (perfect prediction)
    parts.append(f'<line x1="{px(0):.1f}" y1="{py(0):.1f}" x2="{px(hi):.1f}" y2="{py(hi):.1f}" '
                 f'stroke="{INK}" stroke-dasharray="4 4" stroke-width="1.2"/>')
    parts.append(f'<text x="{px(hi)-6:.1f}" y="{py(hi)+14:.1f}" font-size="10" fill="{MUTE}" text-anchor="end">perfect</text>')
    # points
    for r in rows:
        cx, cy = px(min(r["observed_ft"], hi)), py(min(r["predicted_p50_ft"], hi))
        col = GOOD if r["in_p10_p90"] else BAD
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.2" fill="{col}" fill-opacity="0.5"/>')
    # axis labels
    parts.append(f'<text x="{(x0+x1)/2:.0f}" y="{H-6}" font-size="12" fill="{INK}" text-anchor="middle">Observed clarity (ft)</text>')
    parts.append(f'<text x="14" y="{(y0+y1)/2:.0f}" font-size="12" fill="{INK}" text-anchor="middle" '
                 f'transform="rotate(-90 14 {(y0+y1)/2:.0f})">Predicted p50 (ft)</text>')
    parts.append("</svg>")
    return "".join(parts)


def timeline_svg(rows: list[dict]) -> str:
    W, H, padL, padB = 860, 230, 50, 30
    x0, y0, x1, y1 = padL, 14, W - 14, H - padB
    ds = sorted({r["date"] for r in rows})
    if not ds:
        return ""
    d_min = date.fromisoformat(ds[0]); d_max = date.fromisoformat(ds[-1])
    span = max(1, (d_max - d_min).days)
    cap = 30.0
    def px(d): return x0 + ((date.fromisoformat(d) - d_min).days / span) * (x1 - x0)
    def py(v): return (y0 + y1) / 2 - (max(-cap, min(cap, v)) / cap) * ((y1 - y0) / 2)
    parts = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="system-ui,sans-serif">']
    # zero line + +/- guides
    for g in (-20, -10, 0, 10, 20):
        col = INK if g == 0 else GRID
        dash = "" if g == 0 else ' stroke-dasharray="3 3"'
        parts.append(f'<line x1="{x0}" y1="{py(g):.1f}" x2="{x1}" y2="{py(g):.1f}" stroke="{col}"{dash}/>')
        parts.append(f'<text x="{x0-6}" y="{py(g)+4:.1f}" font-size="10" fill="{MUTE}" text-anchor="end">{g:+d}</text>')
    # month ticks
    cur = date(d_min.year, d_min.month, 1)
    while cur <= d_max:
        if cur >= d_min:
            xx = px(cur.isoformat())
            parts.append(f'<line x1="{xx:.1f}" y1="{y0}" x2="{xx:.1f}" y2="{y1}" stroke="{GRID}"/>')
            parts.append(f'<text x="{xx:.1f}" y="{y1+16}" font-size="10" fill="{MUTE}" text-anchor="middle">{cur.strftime("%b %d")}</text>')
        cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
    for r in rows:
        col = GOOD if r["in_p10_p90"] else BAD
        parts.append(f'<circle cx="{px(r["date"]):.1f}" cy="{py(r["residual_ft"]):.1f}" r="2.8" fill="{col}" fill-opacity="0.45"/>')
    parts.append(f'<text x="14" y="{(y0+y1)/2:.0f}" font-size="12" fill="{INK}" text-anchor="middle" '
                 f'transform="rotate(-90 14 {(y0+y1)/2:.0f})">Residual: pred − obs (ft)</text>')
    parts.append("</svg>")
    return "".join(parts)


def bias_bar(bias: float, scale: float = 15.0, w: int = 120) -> str:
    half = w / 2
    mag = min(abs(bias), scale) / scale * half
    col = BAD if bias >= 0 else UNDER
    x = w / 2 if bias >= 0 else w / 2 - mag
    return (f'<svg width="{w}" height="16" viewBox="0 0 {w} 16">'
            f'<line x1="{half}" y1="0" x2="{half}" y2="16" stroke="{GRID}"/>'
            f'<rect x="{x:.1f}" y="4" width="{mag:.1f}" height="8" fill="{col}" rx="1"/></svg>')


def cal_bar(pct: float, w: int = 240) -> str:
    fill = min(max(pct, 0), 1) * w
    tx = CAL_TARGET * w
    col = GOOD if abs(pct - CAL_TARGET) <= 0.1 else (BAD if pct < CAL_TARGET else UNDER)
    return (f'<svg width="{w}" height="18" viewBox="0 0 {w} 18">'
            f'<rect x="0" y="3" width="{w}" height="12" fill="#f3f4f6" rx="2"/>'
            f'<rect x="0" y="3" width="{fill:.1f}" height="12" fill="{col}" rx="2"/>'
            f'<line x1="{tx:.1f}" y1="0" x2="{tx:.1f}" y2="18" stroke="{INK}" stroke-width="1.5"/></svg>')


def _fmt_r(r):
    return "—" if r is None else f"{r:+.2f}"


# ---- assemble --------------------------------------------------------

def build_html(rows: list[dict]) -> str:
    ov = _stats(rows)
    by_spot = defaultdict(list)
    for r in rows:
        by_spot[r["spot_name"]].append(r)
    spot_stats = []
    for s, rs in by_spot.items():
        st = _stats(rs)
        st["spot"] = s
        spot_stats.append(st)
    spot_stats.sort(key=lambda s: -s["n"])

    ds = sorted({r["date"] for r in rows})
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    spot_rows = []
    for s in spot_stats:
        sparse = ' style="opacity:.55"' if s["n"] < 30 else ""
        flag = ' <span title="bias exceeds the ±5 ft watchdog threshold" style="color:#b45309">⚠</span>' if abs(s["bias"]) > 5 else ""
        spot_rows.append(
            f'<tr{sparse}><td>{escape(s["spot"])}{flag}</td><td class="num">{s["n"]}</td>'
            f'<td class="num">{s["rmse"]:.1f}</td>'
            f'<td class="num">{s["bias"]:+.1f}</td><td>{bias_bar(s["bias"])}</td>'
            f'<td class="num">{s["mae"]:.1f}</td>'
            f'<td class="num">{s["cal"]*100:.0f}%</td></tr>'
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visibility hindcast — predicted vs observed</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; color: {INK}; margin: 0; background:#fff; }}
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 28px 22px 60px; }}
  h1 {{ font-size: 22px; margin: 0 0 2px; }}
  .sub {{ color: {MUTE}; font-size: 13px; margin-bottom: 22px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px,1fr)); gap: 12px; margin-bottom: 26px; }}
  .card {{ background: #f9fafb; border: 1px solid {GRID}; border-radius: 10px; padding: 12px 14px; }}
  .card .v {{ font-size: 24px; font-weight: 650; }}
  .card .k {{ font-size: 12px; color: {MUTE}; margin-top: 2px; }}
  h2 {{ font-size: 15px; margin: 30px 0 10px; border-bottom: 1px solid {GRID}; padding-bottom: 6px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid {GRID}; }}
  th {{ color: {MUTE}; font-weight: 600; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .legend {{ font-size: 12px; color: {MUTE}; margin: 8px 0 0; }}
  .dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; vertical-align:middle; margin:0 3px 0 10px; }}
  .panel {{ display:flex; flex-wrap:wrap; gap:24px; align-items:flex-start; }}
  .note {{ background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:12px 14px; font-size:12.5px; color:#92400e; margin-top:24px; }}
  .note b {{ color:#78350f; }}
</style></head>
<body><div class="wrap">
  <h1>Visibility hindcast — predicted vs observed</h1>
  <div class="sub">{ov['n']} scored clarity observations · {ds[0] if ds else '—'} → {ds[-1] if ds else '—'} · generated {gen}</div>

  <div class="cards">
    <div class="card"><div class="v">{ov['n']}</div><div class="k">observations scored</div></div>
    <div class="card"><div class="v">{ov['rmse']:.1f} ft</div><div class="k">RMSE</div></div>
    <div class="card"><div class="v">{ov['bias']:+.1f} ft</div><div class="k">bias (pred − obs)</div></div>
    <div class="card"><div class="v">{ov['mae']:.1f} ft</div><div class="k">MAE</div></div>
    <div class="card"><div class="v">{ov['cal']*100:.0f}%</div><div class="k">in p10–p90 (target 80%)</div></div>
    <div class="card"><div class="v">{_fmt_r(ov['r'])}</div><div class="k">pearson r</div></div>
  </div>

  <div class="panel">
    <div>
      <h2>Predicted vs observed</h2>
      {scatter_svg(rows)}
      <div class="legend"><span class="dot" style="background:{GOOD}"></span>inside p10–p90
        <span class="dot" style="background:{BAD}"></span>outside · dashed = perfect prediction</div>
    </div>
    <div style="flex:1; min-width:280px">
      <h2>Calibration — coverage of the p10–p90 band</h2>
      <div style="margin:6px 0 4px">{cal_bar(ov['cal'])}</div>
      <div class="legend" style="margin-left:0">{ov['cal']*100:.0f}% of observations land inside the predicted band
        (black tick = 80% target). Below target ⇒ the band is too narrow (overconfident).</div>
    </div>
  </div>

  <h2>By spot</h2>
  <table>
    <thead><tr><th>Spot</th><th class="num">n</th><th class="num">RMSE</th>
      <th class="num">bias</th><th>over ▸ / ◂ under</th><th class="num">MAE</th><th class="num">cal</th></tr></thead>
    <tbody>{''.join(spot_rows)}</tbody>
  </table>
  <div class="legend" style="margin-left:0">Rows with n &lt; 30 are dimmed (too sparse to act on).
    <span style="color:{BAD}">red bar</span> = model over-predicts (says clearer than reality);
    <span style="color:{UNDER}">blue bar</span> = under-predicts. ⚠ = |bias| &gt; 5 ft.</div>

  <h2>Residual over time</h2>
  {timeline_svg(rows)}

  <div class="note">
    <b>How to read this.</b> Predictions are the ones we <b>actually published each day</b>, reconstructed
    from git-history PNGs — so this is real-time historical skill, not a current-model refit (predictions
    span a mix of coefficient versions). Clarity ground truth is <b>nearshore SoCal only</b> (dive-shop +
    pier-turbidity sources); offshore, island, and NorCal zones have no clarity truth and are not scored
    here. Spots with n &lt; 30 are too sparse to act on. This report reads only
    <code>hindcast_residuals.jsonl</code> and never touches the live daily loop's files.
  </div>
</div></body></html>"""


def main():
    rows = _load()
    OUT_PATH.write_text(build_html(rows), encoding="utf-8")
    print(f"wrote {OUT_PATH} ({len(rows)} obs)")


if __name__ == "__main__":
    main()
