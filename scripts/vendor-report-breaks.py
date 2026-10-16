"""Vendor the main app's break tracer for the report page (a classic script, no
bundler): src/lib/sstBreaks.js + src/lib/breaksGps.js -> public/reports/breaks.js,
exposing window.SDBreaks. tests/reportBreaks.test.js checks the copy matches."""
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..") + "/"
a = open(ROOT + "src/lib/sstBreaks.js", encoding="utf-8").read()
b = open(ROOT + "src/lib/breaksGps.js", encoding="utf-8").read()


def strip(src):
    return re.sub(r"^export (?=(?:const|function|async function) )", "", src, flags=re.M)


# The copy keeps the logic as is; only the em dashes (in comments and label
# strings the report does not use) become plain hyphens, the page's house style.
body = (strip(a) + "\n" + strip(b)).replace("—", "-")
out = (
    "/* Temperature-break tracer for the Ocean Report: a copy of src/lib/sstBreaks.js and\n"
    "   src/lib/breaksGps.js (the main map's traced fronts), made a classic script so the\n"
    "   report page can use it without the app bundle. Regenerate with\n"
    "   scripts/vendor-report-breaks.py; tests/reportBreaks.test.js fails if it drifts. */\n"
    "(function () {\n"
    + body
    + "\nwindow.SDBreaks = { computeBreakMask: computeBreakMask, BREAK_STRONG_C_PER_KM: BREAK_STRONG_C_PER_KM,\n"
    "  gridToLngLat: gridToLngLat, pickWaypoints: pickWaypoints, formatWaypoint: formatWaypoint, toDDM: toDDM };\n"
    "})();\n"
)
open(ROOT + "public/reports/breaks.js", "w", encoding="utf-8", newline="\n").write(out)
print(len(out))
