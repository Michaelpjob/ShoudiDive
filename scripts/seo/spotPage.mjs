// Renders one spot page from the sampled data + detailed-chart facts.
// Honesty rules follow docs/STRICT-SCIENCE.md: visibility is labelled
// Modeled with its measured skill shown in the open, observed-only layers
// say "no reading" instead of borrowing a neighbour, and every number
// carries its source and time.

import { esc, page, SITE } from "./layout.mjs";
import { compass } from "./sample.mjs";
import { REGION_LABEL, haversineKm } from "./spots.mjs";

const FT_PER_M = 3.28084;
const cToF = (c) => (c * 9) / 5 + 32;

const fmtNum = (v, d = 0) => (Number.isFinite(v) ? v.toFixed(d) : null);

function fmtDate(iso, tz, opts) {
  if (!iso) return null;
  const d = new Date(/T/.test(iso) ? iso : `${iso}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat("en-US", { timeZone: tz, ...opts }).format(d);
}
const fmtDay = (iso, tz) => fmtDate(iso, tz, { weekday: "short", month: "short", day: "numeric" });
const fmtStamp = (iso, tz) =>
  fmtDate(iso, tz, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
// Plain YYYY-MM-DD dates are calendar days: format them in UTC so the label
// never slides a day.
const fmtCalDay = (ymd) => fmtDate(ymd, "UTC", { month: "short", day: "numeric" });

const SWELL_PREF = ["morning", "midday", "predawn", "afternoon", "evening"];
const AM = ["morning", "midday", "predawn"];
const PM = ["afternoon", "evening"];

function pick(buckets, order, lng, lat) {
  for (const b of order) {
    const fn = buckets?.[b];
    if (!fn) continue;
    const v = fn(lng, lat);
    if (v && (Number.isFinite(v.hs) || Number.isFinite(v.kt))) return { bucket: b, ...v };
  }
  return null;
}

export function computeSpotConditions(spot, R, now = new Date()) {
  const { lng, lat } = spot;
  const out = { spot };
  if (R.sst) {
    out.sstC = R.sst.at(lng, lat);
    out.sstDate = R.sst.date;
    out.sstSource = R.sst.source;
    out.sstTrendC = R.sst.trend ? R.sst.trend(lng, lat, 3) : NaN;
  }
  if (R.chl) out.chl = R.chl.at(lng, lat);
  if (R.viz) { out.vizFt = R.viz.at(lng, lat); out.vizValidAt = R.viz.validAt; }

  const days = new Map();
  for (const d of R.swell?.days || []) days.set(d.date, { date: d.date, swell: pick(d.buckets, SWELL_PREF, lng, lat) });
  for (const d of R.wind?.days || []) {
    const row = days.get(d.date) || { date: d.date };
    row.windAm = pick(d.buckets, AM, lng, lat);
    row.windPm = pick(d.buckets, PM, lng, lat);
    days.set(d.date, row);
  }
  out.forecast = [...days.values()]
    .filter((r) => r.swell || r.windAm || r.windPm)
    .sort((a, b) => a.date.localeCompare(b.date));

  // Nearest NOAA tide station within 80 km, next four events.
  const stations = R.tides?.stations || [];
  let best = null;
  for (const st of stations) {
    if (!st.has_real_data || !st.events?.length) continue;
    const km = haversineKm(spot, st);
    if (km <= 80 && (!best || km < best.km)) best = { ...st, km };
  }
  if (best) {
    out.tide = {
      station: best.name, stationId: best.id, km: best.km,
      events: best.events.filter((e) => new Date(e.t) >= now).slice(0, 4),
    };
  }

  // Measured skill for the visibility model, per spot when the hindcast has
  // that spot by name, otherwise the region. Missing = no ground truth.
  const skill = R.vizSkill;
  out.skill = skill?.spots?.[spot.appName] || skill?.spots?.[spot.name] || skill?.regions?.[spot.region] || null;
  out.skillScope = skill?.spots?.[spot.appName] || skill?.spots?.[spot.name] ? "spot" : out.skill ? "region" : null;
  return out;
}

// Where WaveWatch III does not reach (Sea of Cortez interior, south of its
// West Coast grid), fetch_swell_5day.py fills the grid with a fetch-limited
// wind-sea estimate, whose period can drop to 1 to 2 s in light wind. Below
// this period the reading is local chop, not swell, and is labelled that way.
const CHOP_MAX_PERIOD_S = 4;
const isChop = (s) => Number.isFinite(s?.tp) && s.tp < CHOP_MAX_PERIOD_S;

function swellTxt(s) {
  if (!s || !Number.isFinite(s.hs)) return null;
  const ft = s.hs * FT_PER_M;
  if (isChop(s)) return `${ft.toFixed(1)} ft wind chop from ${compass(s.dp)}`;
  return `${ft.toFixed(1)} ft at ${fmtNum(s.tp)} s from ${compass(s.dp)}`;
}

const BUCKET_PHRASE = {
  predawn: "before dawn", morning: "the morning", midday: "midday",
  afternoon: "the afternoon", evening: "the evening",
};
const bucketPhrase = (b) => BUCKET_PHRASE[b] || b;
const titleCase = (s) => s.replace(/-/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
function windTxt(w) {
  if (!w || !Number.isFinite(w.kt)) return null;
  return `${fmtNum(w.kt)} kt ${compass(w.fromDeg)}`;
}

// Same thresholds the app uses to cap the confidence marker
// (src/lib/confidence.js measuredVizBase, viz_skill.json thresholds).
const SKILL_MIN_N = 30;
const SKILL_MIN_R = 0.3;

function skillSentence(c) {
  const k = c.skill;
  if (!k || !Number.isFinite(k.n)) {
    return "There are no dive reports in this area to score the model against yet, so its error here is unknown.";
  }
  const where = c.skillScope === "spot" ? "here" : "across California";
  if (k.n < SKILL_MIN_N) {
    return `Only ${k.n} dive reports exist ${where} so far, too few to judge the model.`;
  }
  const r = Number.isFinite(k.pearson_r) ? k.pearson_r : NaN;
  const r2 = Number.isFinite(r) ? `${r < 0 ? "−" : ""}${Math.abs(r).toFixed(2)}` : "n/a";
  const miss = Number.isFinite(k.rmse_ft) ? `${Math.round(k.rmse_ft)} ft` : "unknown";
  if (r >= SKILL_MIN_R) {
    return `Scored against ${k.n} dive reports ${where}, the model tracks conditions with a correlation of ${r2} and a typical miss of ${miss}.`;
  }
  const bias = Number.isFinite(k.bias_ft) && Math.abs(k.bias_ft) >= 1
    ? ` and runs about ${Math.round(Math.abs(k.bias_ft))} ft ${k.bias_ft > 0 ? "optimistic" : "pessimistic"}`
    : "";
  return `Scored against ${k.n} dive reports ${where}, the model has not tracked day-to-day conditions (correlation ${r2})${bias}, with a typical miss of ${miss}.`;
}

export function appLink(spot) {
  const q = new URLSearchParams();
  if (spot.region !== "ca") q.set("region", spot.region);
  q.set("spot", spot.id);
  return `/?${q.toString()}`;
}

function conditionsCards(c, tz) {
  const cards = [];
  const sstF = cToF(c.sstC);
  if (Number.isFinite(c.sstC)) {
    const trend = Number.isFinite(c.sstTrendC)
      ? ` Change over the last 3 days: ${c.sstTrendC >= 0 ? "+" : ""}${(c.sstTrendC * 9 / 5).toFixed(1)} °F.`
      : "";
    cards.push(`<div class="card"><h3>Sea surface temperature</h3>
<div class="num">${sstF.toFixed(0)} °F <small>${c.sstC.toFixed(1)} °C</small></div>
<p>Satellite analysis for ${esc(fmtCalDay(c.sstDate) || "the latest day")}${c.sstSource ? ` from ${esc(c.sstSource)}` : ""}.${trend} This is the surface only, and a thermocline can sit much colder below.</p></div>`);
  } else {
    cards.push(`<div class="card"><h3>Sea surface temperature</h3><div class="num">No reading</div>
<p>The latest satellite analysis has no value at this point.</p></div>`);
  }

  const today = c.forecast[0];
  const dayLbl = today ? `, ${esc(fmtDay(today.date, tz))}` : "";
  const s = today?.swell;
  if (s && Number.isFinite(s.hs) && isChop(s)) {
    cards.push(`<div class="card"><h3>Waves${dayLbl}</h3>
<div class="num">${(s.hs * FT_PER_M).toFixed(1)} ft <small>wind chop, ${esc(compass(s.dp))}</small></div>
<p>NOAA's WaveWatch III model does not cover this water, so this is an estimate of local wind chop from the wind forecast, for ${esc(bucketPhrase(s.bucket))}. It says nothing about distant swell.</p></div>`);
  } else if (s && Number.isFinite(s.hs)) {
    cards.push(`<div class="card"><h3>Swell${dayLbl}</h3>
<div class="num">${(s.hs * FT_PER_M).toFixed(1)} ft <small>${fmtNum(s.tp)} s ${esc(compass(s.dp))}</small></div>
<p>Significant height, peak period, and the direction it comes from, forecast for ${esc(bucketPhrase(s.bucket))} in open water off the spot. From NOAA's WaveWatch III model${c.spot.region === "ca" ? "" : ", blended with a wind-chop estimate near the edge of its coverage"}.</p></div>`);
  } else {
    cards.push(`<div class="card"><h3>Swell${dayLbl}</h3><div class="num">No data</div>
<p>The wave model has no value near this spot today.</p></div>`);
  }

  const wa = today?.windAm, wp = today?.windPm;
  const lead = wa && Number.isFinite(wa.kt) ? wa : wp;
  if (lead && Number.isFinite(lead.kt)) {
    const other = lead === wa && wp && Number.isFinite(wp.kt)
      ? ` Afternoon: ${esc(windTxt(wp))}.`
      : "";
    cards.push(`<div class="card"><h3>Wind${dayLbl}</h3>
<div class="num">${fmtNum(lead.kt)} kt <small>from ${esc(compass(lead.fromDeg))}</small></div>
<p>Forecast for ${esc(bucketPhrase(lead.bucket))}.${other} NOAA HRRR model for the first two days, GFS after that.</p></div>`);
  } else {
    cards.push(`<div class="card"><h3>Wind${dayLbl}</h3><div class="num">No data</div>
<p>The wind models have no value near this spot today.</p></div>`);
  }

  const skillLine = skillSentence(c);
  if (Number.isFinite(c.vizFt)) {
    cards.push(`<div class="card"><h3>Visibility <span class="tag modeled">Modeled</span></h3>
<div class="num">~${Math.round(c.vizFt)} ft</div>
<p>An estimate from satellite water color, swell, wind, and rain, not a measurement. ${skillLine}</p></div>`);
  } else {
    cards.push(`<div class="card"><h3>Visibility <span class="tag modeled">Modeled</span></h3>
<div class="num">No estimate today</div>
<p>The visibility model is only shown where it rests on a direct satellite reading of the water from the last few days. There is none for this spot right now, so I leave it blank rather than guess. ${skillLine}</p></div>`);
  }

  if (Number.isFinite(c.chl)) {
    cards.push(`<div class="card"><h3>Chlorophyll</h3>
<div class="num">${c.chl.toFixed(c.chl < 1 ? 2 : 1)} <small>mg/m³</small></div>
<p>Direct satellite observation from the last 3 days. Higher values usually mean greener water and less light at depth.</p></div>`);
  }

  if (c.tide?.events?.length) {
    const rows = c.tide.events.map((e) =>
      `${e.type === "H" ? "High" : "Low"} ${esc(fmtDate(e.t, tz, { weekday: "short", hour: "numeric", minute: "2-digit" }))} <span class="mono">${(e.v_m * FT_PER_M).toFixed(1)} ft</span>`
    ).join("<br>");
    cards.push(`<div class="card"><h3>Tides</h3><p style="margin-top:0;font-size:14.5px;color:var(--ink)">${rows}</p>
<p>NOAA predictions for the ${esc(titleCase(c.tide.station))} station (${esc(c.tide.stationId)}), ${Math.max(1, Math.round(c.tide.km * 0.621371))} mi away.</p></div>`);
  }
  return `<div class="grid">${cards.join("\n")}</div>`;
}

function forecastTable(c, tz) {
  if (!c.forecast.length) return "";
  // Compact cells so four columns fit a 375 px phone without scrolling.
  const waveCell = (s) => {
    if (!s || !Number.isFinite(s.hs)) return "no data";
    const ft = (s.hs * FT_PER_M).toFixed(1);
    return isChop(s) ? `${ft} ft chop ${compass(s.dp)}` : `${ft} ft ${fmtNum(s.tp)} s ${compass(s.dp)}`;
  };
  const dayCell = (ymd) => `${fmtDate(ymd, tz, { weekday: "short" })} ${Number(ymd.slice(8, 10))}`;
  const rows = c.forecast.map((r) => `<tr><td>${esc(dayCell(r.date))}</td>
<td class="num">${esc(waveCell(r.swell))}</td>
<td class="num">${esc(windTxt(r.windAm) || "no data")}</td>
<td class="num">${esc(windTxt(r.windPm) || "no data")}</td></tr>`).join("\n");
  return `<h2>Swell and wind outlook</h2>
<p>Waves are the morning forecast where the model has one: height, period, and the direction they come from. Morning wind covers roughly 6 to 10 am and afternoon wind 2 to 7 pm, local time, with the direction it blows from. Days past the wind model's horizon show no data.</p>
<div class="tablewrap"><table><thead><tr><th>Day</th><th>Waves</th><th>Wind AM</th><th>Wind PM</th></tr></thead>
<tbody>${rows}</tbody></table></div>`;
}

const MPA_TYPE = {
  SMR: "State Marine Reserve: no take",
  SMCA: "State Marine Conservation Area: some take allowed, rules vary by area",
  SMP: "State Marine Park: no commercial take, some recreational take may be allowed",
  SMRMA: "State Marine Recreational Management Area: rules vary by area",
};

function chartSection(spot, facts) {
  if (!facts) return "";
  const parts = [];
  if (facts.depthFt) {
    parts.push(`<li>Depths from ${facts.depthFt.min} ft to ${facts.depthFt.max} ft across the chart area, read from NOAA's coastal elevation models rather than surveyed soundings.</li>`);
  }
  if (facts.kelp && facts.kelp.canopyAcres > 0) {
    const q = facts.kelp.quarter ? `, latest quarter ${esc(fmtDate(facts.kelp.quarter, "UTC", { month: "short", year: "numeric" }))}` : "";
    const former = facts.kelp.formerAcres > 0
      ? ` The chart also outlines the largest extent on record, about ${Math.round(facts.kelp.formerAcres).toLocaleString("en-US")} acres.`
      : "";
    parts.push(`<li>About ${Math.round(facts.kelp.canopyAcres).toLocaleString("en-US")} acres of kelp canopy at its peak over the last three years of Landsat mapping${q}.${former}</li>`);
  }
  if (facts.landmarks?.length) {
    parts.push(`<li>Named sites marked on the chart: ${facts.landmarks.map(esc).join(", ")}.</li>`);
  }
  let mpaHtml = "";
  if (facts.mpas?.length) {
    const items = facts.mpas.map((m) => {
      const t = spot.region === "ca" && MPA_TYPE[m.type] ? ` (${MPA_TYPE[m.type]})` : m.type ? ` (${esc(m.type)})` : "";
      return `<li>${esc(m.name)}${t}</li>`;
    }).join("");
    const rules = spot.region === "ca"
      ? `<p class="note">Boundaries from the California Department of Fish and Wildlife. Check the current regulations on the <a href="https://wildlife.ca.gov/Conservation/Marine/MPAs" rel="noopener">CDFW MPA pages</a> before you fish or spear here.</p>`
      : `<p class="note">Protected-area boundaries are shown for orientation only. Check current rules with CONANP and local operators before you fish or spear here.</p>`;
    mpaHtml = `<h3>Marine protected areas in the chart area</h3><ul>${items}</ul>${rules}`;
  }
  if (!parts.length && !mpaHtml) return "";
  return `<h2>Detailed chart</h2>
<p>${esc(spot.name)} has a detailed chart in the app with shaded depth bands, contours, and overlays at higher resolution than the main map. Open the spot on the map and choose "View detailed map". You can tap the chart to copy a GPS position. It is for planning, not navigation.</p>
${parts.length ? `<ul>${parts.join("")}</ul>` : ""}${mpaHtml}`;
}

function nearbySection(spot, roster) {
  const near = roster
    .filter((s) => s.region === spot.region && s.id !== spot.id)
    .map((s) => ({ s, km: haversineKm(spot, s) }))
    .sort((a, b) => a.km - b.km)
    .slice(0, 5);
  if (!near.length) return "";
  return `<h2>Nearby spots</h2><ul class="spotlist">${near
    .map(({ s, km }) => `<li><a href="/spots/${s.slug}/">${esc(s.name)}</a> <span class="stamp">${Math.round(km * 0.621371)} mi</span></li>`)
    .join("")}</ul>`;
}

export function spotSummaryLine(c) {
  const bits = [];
  if (Number.isFinite(c.sstC)) bits.push(`water ${cToF(c.sstC).toFixed(0)} °F`);
  const sw = c.forecast[0]?.swell;
  if (sw && Number.isFinite(sw.hs)) {
    bits.push(isChop(sw)
      ? `wind chop ${(sw.hs * FT_PER_M).toFixed(1)} ft`
      : `swell ${(sw.hs * FT_PER_M).toFixed(1)} ft at ${fmtNum(sw.tp)} s`);
  }
  const w = c.forecast[0]?.windPm || c.forecast[0]?.windAm;
  if (w && Number.isFinite(w.kt)) bits.push(`wind ${fmtNum(w.kt)} kt ${compass(w.fromDeg)}`);
  return bits.join(", ");
}

export function renderSpotPage(spot, c, facts, roster, builtAt) {
  const tz = spot.tz;
  const regionLabel = REGION_LABEL[spot.region] || spot.region;
  const isMexico = spot.region === "baja" || spot.id === "coronados";
  const where = spot.id === "coronados"
    ? "Islas Coronado, off Tijuana in Mexican waters, south of Point Loma"
    : `${spot.group}, ${regionLabel}`;
  const summary = spotSummaryLine(c);
  const title = `${spot.name} ocean conditions today: water temp, swell, wind | ShouldIDive`;
  const description = `${spot.name} conditions updated several times a day${summary ? `: ${summary}` : ""}. Sea temperature, swell and wind outlook, tides, kelp, and modeled visibility for divers, spearfishers, anglers, and boaters.`;
  const path = `/spots/${spot.slug}/`;
  const crumbs = [
    { name: "Spots", href: "/spots/" },
    { name: regionLabel, href: `/spots/#${spot.region}` },
    { name: spot.name, href: path },
  ];
  const body = `
<h1>${esc(spot.name)} ocean conditions</h1>
<p class="lede">Sea temperature, swell, wind, tides, and a modeled visibility estimate for ${esc(spot.name)} (${esc(where)}), pulled from the same NOAA and NASA data as the live map.</p>
<p class="stamp">Page built ${esc(fmtStamp(builtAt.toISOString(), tz))}. Location ${spot.lat.toFixed(3)}° ${spot.lat >= 0 ? "N" : "S"}, ${Math.abs(spot.lng).toFixed(3)}° W.</p>
<a class="cta" href="${appLink(spot)}">Open ${esc(spot.name)} on the live map</a>
${conditionsCards(c, tz)}
${forecastTable(c, tz)}
${chartSection(spot, facts)}
<h2>How to read this page</h2>
<p>Sea temperature is a satellite product, so it describes the top few feet of water and can miss a cold layer at depth. Swell and wind are model forecasts for open water near the spot. A point or a kelp bed can block or bend swell, so a protected cove often runs smaller than the number above. The visibility estimate is the least certain number on the page. It is labelled Modeled because I have not been able to show that it tracks what divers report day to day. The <a href="/science/">science page</a> has the details.</p>
${isMexico ? `<p class="note">This spot is in Mexican waters. Fishing there requires a Mexican fishing license from CONAPESCA, and many of the islands are federal protected areas with their own rules.</p>` : ""}
${nearbySection(spot, roster)}
<p><a href="/spots/">All spots</a> · <a href="${appLink(spot)}">Live map</a></p>`;

  const jsonld = [
    {
      "@context": "https://schema.org",
      "@type": "Place",
      name: spot.name,
      url: `${SITE}${path}`,
      geo: { "@type": "GeoCoordinates", latitude: spot.lat, longitude: spot.lng },
      containedInPlace: { "@type": "Place", name: isMexico ? "Mexico" : "California" },
    },
    {
      "@context": "https://schema.org",
      "@type": "WebPage",
      name: title,
      url: `${SITE}${path}`,
      dateModified: builtAt.toISOString(),
      isPartOf: { "@type": "WebSite", name: "ShouldIDive", url: SITE },
    },
  ];
  return { path, html: page({ path, title, description, body, jsonld, crumbs }) };
}
