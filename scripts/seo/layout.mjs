// Shared HTML shell for the static, crawlable pages that sit beside the map
// app (spot pages, audience guides, about, science, 404). Plain HTML + inline
// CSS: no client JS, so the pages satisfy the site CSP (script-src 'self')
// and render fully for crawlers that never execute JavaScript.

export const SITE = "https://shouldidive.com";
export const SITE_NAME = "ShouldIDive";
export const OG_IMAGE = `${SITE}/og-image.png`;

export const NAV = [
  { href: "/", label: "Live map" },
  { href: "/spots/", label: "Spots" },
  { href: "/diving/", label: "Diving" },
  { href: "/spearfishing/", label: "Spearfishing" },
  { href: "/fishing/", label: "Fishing" },
  { href: "/sailing/", label: "Sailing" },
  { href: "/science/", label: "Science" },
  { href: "/about/", label: "About" },
];

export function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// JSON-LD goes inside <script>; escape "<" so a value can never close the tag.
function ldJson(obj) {
  return JSON.stringify(obj).replace(/</g, "\\u003c");
}

export function breadcrumbLd(crumbs) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: crumbs.map((c, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: c.name,
      item: `${SITE}${c.href}`,
    })),
  };
}

const CSS = `
:root{--bg:oklch(0.985 0.005 85);--panel:oklch(0.995 0.003 85);--sunken:oklch(0.96 0.006 85);
--ink:oklch(0.22 0.02 250);--ink-2:oklch(0.42 0.015 250);--ink-3:oklch(0.55 0.012 250);
--line:oklch(0.88 0.008 250);--accent:oklch(0.48 0.13 230);--accent-ink:oklch(0.98 0.005 230);
--warn:oklch(0.55 0.16 35);--r:10px}
@media (prefers-color-scheme:dark){:root{--bg:oklch(0.16 0.018 250);--panel:oklch(0.21 0.02 250);
--sunken:oklch(0.13 0.015 250);--ink:oklch(0.96 0.005 230);--ink-2:oklch(0.8 0.012 230);
--ink-3:oklch(0.66 0.015 230);--line:oklch(0.32 0.02 250);--accent:oklch(0.78 0.14 220);
--accent-ink:oklch(0.16 0.02 250);--warn:oklch(0.75 0.15 45)}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent)}
a:hover{text-decoration-thickness:2px}
.wrap{max-width:980px;margin:0 auto;padding:0 16px}
header.site{border-bottom:1px solid var(--line);background:var(--panel)}
header.site .wrap{display:flex;flex-wrap:wrap;align-items:center;gap:6px 18px;padding-top:12px;padding-bottom:12px}
.brand{font-weight:700;font-size:18px;color:var(--ink);text-decoration:none;letter-spacing:-0.01em}
.brand span{color:var(--accent)}
nav.top{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:14px}
nav.top a{color:var(--ink-2);text-decoration:none}
nav.top a:hover,nav.top a[aria-current]{color:var(--accent)}
main{padding:28px 0 48px}
.crumbs{font-size:13px;color:var(--ink-3);margin:0 0 10px}
.crumbs a{color:var(--ink-3)}
h1{font-size:clamp(26px,5vw,38px);line-height:1.15;letter-spacing:-0.02em;margin:0 0 12px}
h2{font-size:22px;line-height:1.25;margin:40px 0 10px;letter-spacing:-0.01em}
h3{font-size:17px;margin:24px 0 6px}
p,li{color:var(--ink-2);max-width:68ch}
.lede{font-size:18px;color:var(--ink-2);max-width:62ch}
.mono,.num{font-family:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace}
.cta{display:inline-block;margin:14px 0 4px;padding:10px 16px;border-radius:var(--r);background:var(--accent);
color:var(--accent-ink);font-weight:600;text-decoration:none}
.cta:hover{filter:brightness(1.08)}
.stamp{font-size:13px;color:var(--ink-3)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:18px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:14px 16px}
.card h3{margin:0 0 4px;font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3)}
.card .num{font-size:26px;font-weight:500;color:var(--ink);line-height:1.2}
.card .num small{font-size:14px;color:var(--ink-3)}
.card p{font-size:13.5px;margin:6px 0 0;line-height:1.5}
.tag{display:inline-block;font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
padding:1px 7px;border-radius:999px;border:1px solid var(--line);color:var(--ink-3);margin-left:6px;vertical-align:2px}
.tag.modeled{color:var(--warn);border-color:var(--warn)}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:var(--r);background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--ink-3);font-weight:600}
tr:last-child td{border-bottom:0}
td.num{font-family:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace}
@media (max-width:520px){th,td{padding:7px 7px;font-size:12.5px}th{font-size:11px}}
.spotlist{columns:3 200px;column-gap:24px;padding:0;list-style:none}
.spotlist li{break-inside:avoid;margin:0 0 6px}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px 28px}
.note{font-size:14px;border-left:3px solid var(--line);padding:2px 0 2px 12px;color:var(--ink-3)}
footer.site{border-top:1px solid var(--line);padding:24px 0 40px;font-size:13.5px;color:var(--ink-3)}
footer.site p{color:var(--ink-3);max-width:none}
footer.site nav{display:flex;flex-wrap:wrap;gap:4px 14px;margin-bottom:10px}
footer.site nav a{color:var(--ink-2)}
`;

export function page({ path, title, description, body, jsonld = [], crumbs = null, robots = null, canonical = true }) {
  const url = `${SITE}${path}`;
  const navHtml = NAV.map((n) =>
    `<a href="${n.href}"${n.href === path ? ' aria-current="page"' : ""}>${esc(n.label)}</a>`
  ).join("");
  const crumbHtml = crumbs
    ? `<p class="crumbs">${crumbs
        .map((c, i) => (i === crumbs.length - 1 ? esc(c.name) : `<a href="${c.href}">${esc(c.name)}</a>`))
        .join(" &rsaquo; ")}</p>`
    : "";
  const lds = [...jsonld, ...(crumbs ? [breadcrumbLd(crumbs)] : [])];
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<meta name="description" content="${esc(description)}">
${canonical ? `<link rel="canonical" href="${url}">\n` : ""}${robots ?`<meta name="robots" content="${esc(robots)}">\n` : ""}<meta property="og:type" content="website">
<meta property="og:site_name" content="${SITE_NAME}">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(description)}">
<meta property="og:url" content="${url}">
<meta property="og:image" content="${OG_IMAGE}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="ShouldIDive map of sea surface temperature along the California and Baja coast">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0369a1">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon-32.png" type="image/png" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>${CSS.trim()}</style>
${lds.map((o) => `<script type="application/ld+json">${ldJson(o)}</script>`).join("\n")}
</head>
<body>
<header class="site"><div class="wrap">
<a class="brand" href="/">Should<span>I</span>Dive</a>
<nav class="top" aria-label="Site">${navHtml}</nav>
</div></header>
<main><div class="wrap">
${crumbHtml}
${body}
</div></main>
<footer class="site"><div class="wrap">
<nav aria-label="Footer">${navHtml}</nav>
<p>ShouldIDive is a free ocean-conditions map for California and Baja, built and run by one person. The data comes from NOAA, NASA, the IOOS HF radar network, and Copernicus Marine. Wind updates hourly and most other layers twice a day. Conditions change fast and any number here can be wrong, so look at the water yourself before you go in.</p>
</div></footer>
</body>
</html>
`;
}
