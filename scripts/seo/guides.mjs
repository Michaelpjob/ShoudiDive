// Audience guides, the spots index, the 404 page, and the home-page link
// block. Copy rules: first person singular (one person builds this), no em
// dashes, every claim matches what production does today. Counts that drift
// (fleet trips, MPA polygons, model skill) are read from the published data
// at build time instead of typed in.

import { esc, page, SITE } from "./layout.mjs";
import { appLink } from "./spotPage.mjs";

const n = (v) => (Number.isFinite(v) ? v.toLocaleString("en-US") : "?");
const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];
const monthYear = (ymd) => (ymd ? `${MONTHS[Number(ymd.slice(5, 7)) - 1]} ${ymd.slice(0, 4)}` : "");
const longDate = (ymd) => (ymd ? `${MONTHS[Number(ymd.slice(5, 7)) - 1]} ${Number(ymd.slice(8, 10))}, ${ymd.slice(0, 4)}` : "");
const signed = (v, d = 1) => (Number.isFinite(v) ? `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}` : "?");

function chartSpotList(ctx) {
  const items = ctx.roster.filter((s) => ctx.chartIds.has(`${s.region}:${s.id}`));
  return `<ul class="spotlist">${items.map((s) => `<li><a href="/spots/${s.slug}/">${esc(s.name)}</a></li>`).join("")}</ul>`;
}

function skillNumbers(ctx) {
  const k = ctx.vizSkill;
  const ca = k?.regions?.ca || k?.overall;
  if (!ca) return null;
  return {
    n: ca.n, r: ca.pearson_r, rmse: ca.rmse_ft, bias: ca.bias_ft, inBand: ca.in_band_pct,
    start: k.date_span?.[0], end: k.date_span?.[1], verdict: ca.verdict,
  };
}

function skillSentence(ctx) {
  const s = skillNumbers(ctx);
  if (!s) return "I score its published predictions against dive reports and show the result in the app.";
  return `I scored the predictions it published at the time against ${n(s.n)} dive-shop and buoy visibility reports from California between ${longDate(s.start)} and ${longDate(s.end)}. They did not track day-to-day conditions (a correlation of ${signed(s.r, 2)}), they ran about ${Math.abs(s.bias).toFixed(0)} ft ${s.bias >= 0 ? "optimistic" : "pessimistic"} on average, and the typical miss was ${s.rmse.toFixed(1)} ft.`;
}

// --- Guides -----------------------------------------------------------------

function diving(ctx) {
  const path = "/diving/";
  const body = `
<h1>Dive conditions for California and Baja</h1>
<p class="lede">ShouldIDive puts the numbers I check before a shore or boat dive on one map: water temperature, swell height and period, wind, surface current, and a visibility estimate, for ${ctx.roster.length} spots from Mendocino to Cabo San Lucas.</p>
<a class="cta" href="/">Open the live map</a>

<h2>Water temperature and upwelling</h2>
<p>The temperature layer is NASA's MUR satellite analysis at about 1 km, corrected against NOAA buoys in California and refreshed twice a day. On that layer, tap the map and the readout includes a wetsuit suggestion. Each saved spot shows its change over the last three days and a seven-day sparkline. A spot that dropped several degrees in a few days is often seeing upwelling, which brings cold water up from depth and tends to cloud it with plankton a few days later. The satellite only sees the surface, so a warm reading can sit on top of a cold layer at 30 ft.</p>

<h2>Swell and surge</h2>
<p>Swell comes from NOAA's WaveWatch III model: height, peak period, and direction, hour by hour for five days. Period matters as much as height for surge at depth. A 3 ft swell at 16 seconds moves far more water along the bottom than 3 ft at 8 seconds, so check both before a shallow reef dive or a beach entry. The model describes open water. A cove sheltered from the swell direction usually runs smaller than the map shows.</p>

<h2>Visibility, and how much to trust it</h2>
<p>The Vis layer is a model, and the app labels it that way. It starts from satellite chlorophyll, which tracks how green the water is, and adjusts for swell, wind, rain, river flow, and tides. ${skillSentence(ctx)} The confidence marker on the map stays at Modeled until that score improves. The layer also only draws where the model rests on a direct satellite reading from the last few days, so after cloudy or foggy stretches it can be blank across the whole coast.</p>
<p>Until the model earns more trust, the temperature trend, swell period, and the last few days of wind are often better clues to visibility than the estimate.</p>

<h2>Detailed charts for ${ctx.chartIds.size} spots</h2>
<p>These spots have a detailed chart in the app with shaded depth bands from 10 to 300+ ft, contour lines, and depths read from NOAA's coastal elevation models. The charts also draw the kelp canopy mapped from Landsat, the bed's largest recorded extent as a dashed outline, marine protected area boundaries, and named sites where I have them. Tap the chart to copy a GPS position. They are for planning, not navigation.</p>
${chartSpotList(ctx)}

<h2>Wind, current, and tides</h2>
<p>Wind comes from NOAA's 3 km HRRR model for the first two days and GFS out to seven, updated every hour, which is enough to see whether the morning glass will hold. Surface current is a beta layer built from the coastal HF radar network, with a tide and wind estimate where the radar does not reach. In Baja the current layer is an estimate only. Tide predictions from the nearest NOAA station are on each spot page.</p>

<h2>A page for every spot</h2>
<p>Every saved spot has its own page with today's water temperature, a swell and wind outlook, upcoming tides, and its chart details. The pages rebuild about once an hour from the same data as the map.</p>
<p><a href="/spots/">Browse all ${ctx.roster.length} spots</a></p>`;
  return {
    path,
    html: page({
      path,
      title: "California and Baja dive conditions: water temp, swell, visibility | ShouldIDive",
      description: "Daily dive conditions for California and Baja: satellite water temperature, swell and surge, wind, currents, detailed kelp and depth charts, and a visibility model with its error published.",
      body,
      crumbs: [{ name: "Home", href: "/" }, { name: "Diving", href: path }],
    }),
  };
}

function spearfishing(ctx) {
  const path = "/spearfishing/";
  const body = `
<h1>Spearfishing conditions, MPA boundaries, and kelp charts</h1>
<p class="lede">A spearfishing day in California starts with knowing where you are allowed to shoot, then finding clean water, kelp, and bait. ShouldIDive maps the protected areas, the kelp, the temperature breaks, and the swell from Mendocino to Cabo San Lucas.</p>
<a class="cta" href="/">Open the live map</a>

<h2>Where you can and cannot take fish</h2>
<p>The MPA layer draws ${n(ctx.mpaCountCa)} California marine protected areas from California Department of Fish and Wildlife boundaries. Tap one and the popup says whether it is a reserve with no take, a conservation area with limited take, or open to take. In Baja the map shows the boundaries of Mexico's federal protected areas for orientation only, so check each park's rules locally. Regulations change, and the map is a starting point: confirm on the <a href="https://wildlife.ca.gov/Conservation/Marine/MPAs" rel="noopener">CDFW MPA pages</a> before you hunt a new area.</p>

<h2>Kelp, structure, and depth</h2>
<p>A detailed chart exists for ${ctx.chartIds.size} spots, with depth bands, contours, and the kelp canopy mapped from Landsat imagery by the Santa Barbara Coastal LTER. The chart draws the canopy at its peak over the last three years and outlines the largest extent on record, so you can see where a bed has thinned out. In California, the Bottom layer on the main map marks ${n(ctx.bottomCount)} named banks, reefs, seamounts, and canyons with their minimum depths.</p>

<h2>Temperature breaks for pelagics</h2>
<p>Yellowtail, tuna, and dorado often hold along temperature fronts. The Breaks layer (beta) traces fronts from the satellite temperature map wherever the water changes by at least 0.06 °C per km over 30 km or more, and it draws the sharpest ones in bold. Tap a break to copy its waypoints into a plotter. Breaks only draw while the temperature layer is on its main satellite source, because the backup products are too coarse to trust for edges.</p>

<h2>Kelp paddies</h2>
<p>Kelp Paddy Finder (beta) estimates where drifting kelp is likely to collect in the Southern California Bight, from three days back to two days ahead. It combines HF radar currents, wind, and the Landsat kelp beds that paddies break off from. Track a paddy takes the GPS mark of a paddy you found and drifts it forward for up to a week.</p>
<p><a class="cta" href="/paddies/">Open Kelp Paddy Finder</a></p>

<h2>Visibility</h2>
<p>Visibility is the number spearfishers ask about most and the one I trust least. The Vis layer is labelled Modeled, its measured error is shown in the app, and it stays blank wherever it would rest on gap-filled data. The <a href="/science/">science page</a> has the full score.</p>

<h2>Spearfishing in Mexico</h2>
<p>Fishing from a boat in Mexican waters requires a fishing license from CONAPESCA. Mexico allows spearfishing only on a breath hold, so scuba and hookah are out. Many Baja islands also sit inside federal protected areas with their own rules.</p>
<p><a href="/spots/">Browse all spots</a></p>`;
  return {
    path,
    html: page({
      path,
      title: "Spearfishing conditions and MPA maps for California and Baja | ShouldIDive",
      description: "Plan a freedive hunt with MPA boundaries, kelp canopy charts, temperature breaks, swell, current, and kelp paddy drift for California and Baja.",
      body,
      crumbs: [{ name: "Home", href: "/" }, { name: "Spearfishing", href: path }],
    }),
  };
}

function fishing(ctx) {
  const path = "/fishing/";
  const f = ctx.fleet;
  const fleetPara = f
    ? `Fleet Tracks (beta) maps where the Southern California sportfishing fleet stopped to fish, built from NOAA and Coast Guard AIS position data: ${n(f.n_boats)} boats, ${n(f.n_trips)} trips, and ${n(f.n_stops)} fishing stops since ${monthYear(f.start)}, on a grid of 1.4 km hexagons. Where a trip matches a landing's dock count, the map can color cells by fish per angler. You can filter by boat and week or switch to the live fleet view.`
    : "Fleet Tracks (beta) maps where the Southern California sportfishing fleet stopped to fish, built from NOAA and Coast Guard AIS position data on a grid of 1.4 km hexagons.";
  const body = `
<h1>Sea temperature breaks, paddies, and fleet tracks for anglers</h1>
<p class="lede">The offshore bite in Southern California and Baja follows warm water, bait, and structure. ShouldIDive maps all three from satellite and model data, along with where the sportfishing fleet has been stopping.</p>
<a class="cta" href="/">Open the live map</a>

<h2>Sea surface temperature and breaks</h2>
<p>The temperature layer is NASA's MUR satellite analysis at about 1 km. It keeps seven days of history, so you can watch a warm tongue push up the coast, plus a short forecast (beta) that decays today's anomaly toward the seasonal average. The Breaks layer traces fronts where the water changes by at least 0.06 °C per km over 30 km or more. Tap a break and copy its start, end, and waypoints straight into a plotter.</p>

<h2>Chlorophyll and color edges</h2>
<p>The Chl layer shows satellite chlorophyll, which marks the line between blue and green water that bait often stacks along. It draws only direct satellite readings from the last three days and leaves everything else blank, so after a run of cloud cover it can be empty.</p>

<h2>Currents</h2>
<p>Surface current (beta) comes from the coastal HF radar network at 6 km resolution where the radar reaches, and from a tide and wind estimate elsewhere, with a five-day outlook. In Baja the layer is an estimate only.</p>

<h2>Banks and seamounts</h2>
<p>In California, the Bottom layer marks ${n(ctx.bottomCount)} named banks, reefs, seamounts, and canyons from NOAA charts, GEBCO, and local knowledge. Each popup gives a minimum depth and a short description.</p>

<h2>Kelp paddies</h2>
<p>Kelp Paddy Finder (beta) estimates where drifting kelp is likely to collect in the Southern California Bight, from three days back to two days ahead, using HF radar currents, wind, and the Landsat kelp beds that paddies break off from. If you find one, Track a paddy drifts your GPS mark forward for up to a week.</p>
<p><a class="cta" href="/paddies/">Open Kelp Paddy Finder</a></p>

<h2>Fleet Tracks</h2>
<p>${fleetPara}</p>
<p><a class="cta" href="/fleet/">Open Fleet Tracks</a></p>

<h2>Moon and wind</h2>
<p>The moon widget follows the time slider and shows phase, percent lit, and days to the next full or new moon. Wind comes from NOAA's HRRR model for two days and GFS out to seven, updated every hour.</p>
<p><a href="/spots/">Browse all spots</a></p>`;
  return {
    path,
    html: page({
      path,
      title: "Fishing conditions: SST breaks, kelp paddies, and fleet tracks | ShouldIDive",
      description: "Sea surface temperature with breaks and GPS waypoints, chlorophyll edges, HF radar currents, kelp paddy drift, and where the sportfishing fleet has been stopping, for California and Baja.",
      body,
      crumbs: [{ name: "Home", href: "/" }, { name: "Fishing", href: path }],
    }),
  };
}

function sailing(ctx) {
  const path = "/sailing/";
  const body = `
<h1>Wind, swell, and current for sailing and boating</h1>
<p class="lede">Before a channel crossing or a day on the water you want to know when the wind fills in, how big and how long-period the swell is, and which way the current is setting. ShouldIDive shows all three on one map for the California coast and Baja.</p>
<a class="cta" href="/">Open the live map</a>

<h2>Wind</h2>
<p>Wind comes from NOAA's 3 km HRRR model for the first 48 hours and from GFS out to seven days, and it updates every hour. The current hour is blended with the European ECMWF model and NOAA buoy readings. The timeline splits each day into morning, midday, afternoon, and evening, and a scrubber steps through it hour by hour, so you can see when the afternoon breeze builds. Confidence drops after the first two days and the app shows that on the layer.</p>

<h2>Swell</h2>
<p>Swell comes from NOAA's WaveWatch III model: significant height, peak period, and direction, hourly for five days. A long-period groundswell and a short wind swell of the same height behave differently at a harbor entrance or on a beam reach, so the period is shown next to every height.</p>

<h2>Surface current</h2>
<p>Surface current (beta) uses the coastal HF radar network where it reaches, at 6 km resolution, and a tide and wind estimate elsewhere. It includes a five-day outlook and flags when the current is likely to reverse. In Baja it is an estimate only.</p>

<h2>Spot pages</h2>
<p>Each of the ${ctx.roster.length} spot pages has a swell and wind table for the coming days. California spot pages also list upcoming tides from the nearest NOAA station.</p>
<p><a href="/spots/">Browse all spots</a></p>

<h2>Before you leave the dock</h2>
<p>ShouldIDive is a planning view built from public model data. It is not an official marine forecast, and its charts are not for navigation. Check the National Weather Service <a href="https://www.weather.gov/marine/" rel="noopener">marine forecasts</a> and the Coast Guard's notices to mariners before you go.</p>`;
  return {
    path,
    html: page({
      path,
      title: "Sailing and boating conditions: wind, swell, and currents | ShouldIDive",
      description: "Hourly-updated wind forecast out to seven days, swell height, period, and direction, and HF radar surface currents for the California and Baja coast.",
      body,
      crumbs: [{ name: "Home", href: "/" }, { name: "Sailing", href: path }],
    }),
  };
}

const SOURCES = [
  ["Sea surface temperature", "NASA JPL MUR L4 (GHRSST) via NOAA CoastWatch ERDDAP, corrected against NOAA buoys in California. Backups: NOAA Geo-polar Blended and OISST.", "Twice a day", "Satellite analysis"],
  ["Temperature forecast (beta)", "Today's anomaly decayed toward climatology, about four days ahead.", "Twice a day", "Statistical forecast"],
  ["Chlorophyll", "NASA OB.DAAC near-real-time MODIS-Aqua, VIIRS, and OLCI; NOAA DINEOF; Copernicus Marine GlobColour. Only direct readings from the last 3 days are drawn.", "Twice a day", "Satellite observation"],
  ["Wind", "NOAA HRRR 3 km (0 to 48 h) and GFS (to 7 days). The current hour is blended with ECMWF IFS and NOAA NDBC buoys.", "Hourly", "Model"],
  ["Swell", "NOAA WaveWatch III (gfswave), West Coast grid.", "Twice a day", "Model"],
  ["Surface current (beta)", "IOOS HF radar network (6 km), plus tide range, moon phase, and wind drift where radar does not reach.", "Twice a day", "Observed and estimated"],
  ["Visibility (beta)", "My model: chlorophyll, sea temperature, wind, swell, NOAA CPC rainfall, USGS river flow, NOAA tides, and climatology.", "Twice a day", "Model"],
  ["Kelp canopy", "Santa Barbara Coastal LTER kelp canopy from Landsat, 30 m.", "Quarterly", "Satellite observation"],
  ["Depth", "NOAA NCEI coastal elevation models (CUDEM) and GMRT.", "Static", "Survey compilation"],
  ["Protected areas", "California Department of Fish and Wildlife; CONANP in Mexico.", "Static", "Regulatory boundaries"],
  ["Tides (spot pages)", "NOAA CO-OPS tide predictions.", "Twice a day", "Prediction"],
  ["Fleet Tracks", "NOAA Office for Coastal Management and US Coast Guard AIS, plus landing dock counts.", "Daily", "Observed"],
];

function science(ctx) {
  const path = "/science/";
  const s = skillNumbers(ctx);
  const spots = Object.entries(ctx.vizSkill?.spots || {})
    .filter(([, v]) => v && Number.isFinite(v.n))
    .sort((a, b) => b[1].n - a[1].n);
  const spotRows = spots.map(([name, v]) => `<tr><td>${esc(name)}</td><td class="num">${n(v.n)}</td>
<td class="num">${Number.isFinite(v.rmse_ft) ? v.rmse_ft.toFixed(1) + " ft" : "n/a"}</td>
<td class="num">${Number.isFinite(v.bias_ft) ? signed(v.bias_ft) + " ft" : "n/a"}</td>
<td class="num">${Number.isFinite(v.pearson_r) ? signed(v.pearson_r, 2) : "n/a"}</td></tr>`).join("\n");
  const scoreBlock = s
    ? `<p>${skillSentence(ctx)} The reported visibility fell inside the model's 10th to 90th percentile range ${s.inBand}% of the time, where a correctly sized range would catch about 80%. The app reads these numbers from the same scoring file and caps the visibility confidence at Modeled until a zone reaches at least ${ctx.vizSkill?.thresholds?.min_n ?? 30} reports and a correlation of ${ctx.vizSkill?.thresholds?.min_r ?? 0.3} or better.</p>
<p>A negative correlation means the structure of the model is wrong, not just its tuning, so rescaling the coefficients would only hide the problem. The fix has to come from better inputs and more ground truth. Baja has no dive reports to score against yet, so its visibility error is unknown.</p>
${spotRows ? `<h3>Score by spot</h3><div class="tablewrap"><table><thead><tr><th>Spot</th><th>Reports</th><th>Typical error</th><th>Bias</th><th>Correlation</th></tr></thead><tbody>${spotRows}</tbody></table></div>
<p class="stamp">Positive bias means the model predicted clearer water than divers reported.</p>` : ""}`
    : "<p>The scoring file was not available at build time.</p>";

  const body = `
<h1>Data sources, methods, and measured accuracy</h1>
<p class="lede">Every layer on ShouldIDive comes from public ocean data. This page lists the sources, explains what I do with each one, and shows how the one model I built myself scores against reality.</p>
<a class="cta" href="/">Open the live map</a>

<h2>Rules the map follows</h2>
<ul>
<li>Blank means no data. Observed layers such as chlorophyll are never smoothed, interpolated, or filled from a neighboring cell, so a cloudy day leaves holes.</li>
<li>Each layer carries a confidence score on the map. It drops when the data goes stale, when you scrub further into a forecast, and when a backup source is filling in.</li>
<li>The visibility model cannot be shown as anything better than Modeled until its measured skill clears a published threshold.</li>
<li>Every coefficient that moves a number you see is registered as either fitted to data or provisional.</li>
</ul>

<h2>Sources</h2>
<div class="tablewrap"><table><thead><tr><th>Layer</th><th>Source</th><th>Refresh</th><th>Type</th></tr></thead><tbody>
${SOURCES.map((r) => `<tr><td>${esc(r[0])}</td><td style="white-space:normal;min-width:260px">${esc(r[1])}</td><td>${esc(r[2])}</td><td>${esc(r[3])}</td></tr>`).join("\n")}
</tbody></table></div>

<h2>The visibility model and its score</h2>
<p>Visibility is the one number on the map that no agency publishes. The model starts from satellite chlorophyll, adds sea temperature, wind, swell, rain, river flow, and tides, and converts the result to feet of horizontal visibility with a range around it. Below the surface, a beta water-column view splits the estimate above and below a modeled thermocline. That part is uncalibrated, and the app says so.</p>
${scoreBlock}

<h2>Temperature breaks</h2>
<p>Breaks are traced in your browser from the temperature grid: a front needs a gradient of at least 0.06 °C per km sustained over 30 km, and gradients of 0.1 °C per km or more draw bold. They are suppressed when the temperature layer is on a backup product, because the coarser grids smear real edges into gentle slopes.</p>

<h2>Spot pages</h2>
<p>The <a href="/spots/">spot pages</a> are rebuilt about once an hour from the published grids, using the same decoding and the same blank-means-no-data rules as the map, so a spot page and the map agree for the same point.</p>`;
  return {
    path,
    html: page({
      path,
      title: "Data sources, methods, and measured accuracy | ShouldIDive",
      description: "The satellite, model, and observation sources behind ShouldIDive, how each layer is processed, and the published accuracy of its visibility model.",
      body,
      crumbs: [{ name: "Home", href: "/" }, { name: "Science", href: path }],
      jsonld: [{
        "@context": "https://schema.org",
        "@type": "TechArticle",
        headline: "Data sources, methods, and measured accuracy",
        url: `${SITE}${path}`,
        dateModified: ctx.now.toISOString(),
        about: "Ocean conditions data for California and Baja: sea surface temperature, chlorophyll, wind, swell, currents, and modeled visibility",
      }],
    }),
  };
}

function about(ctx) {
  const path = "/about/";
  const body = `
<h1>About ShouldIDive</h1>
<p class="lede">I built ShouldIDive to answer one question before a dive: is it worth driving to the water today? The answer depends on more than a dozen public data sets spread across government servers, in formats made for scientists. ShouldIDive pulls them together several times a day, decodes them, and draws them on one map of the California and Baja coast.</p>
<a class="cta" href="/">Open the live map</a>

<h2>Who it is for</h2>
<p>It started as a dive tool and grew. The <a href="/diving/">diving</a> guide covers temperature, swell, and visibility. The MPA boundaries and kelp charts serve <a href="/spearfishing/">spearfishing</a>. Temperature breaks, paddy drift, and fleet tracks serve <a href="/fishing/">fishing</a>. The wind and swell forecasts serve anyone taking a boat out, which the <a href="/sailing/">sailing</a> guide covers. The <a href="/science/">science page</a> lists every source and how the numbers are checked.</p>

<h2>Coverage</h2>
<p>California from Mendocino to the Coronado Islands, and Baja from the border to Cabo San Lucas and up the Sea of Cortez to San Felipe. Each of the ${ctx.roster.length} saved spots has its own page, and ${ctx.chartIds.size} of them also have detailed depth and kelp charts in the app.</p>
<p><a href="/spots/">Browse all spots</a></p>

<h2>What it costs</h2>
<p>Nothing. There is no account, no ads, and no tracking cookies. Usage counts are first-party and anonymous, and the app honors Do Not Track. If it saves you a wasted drive, there is a tip link in the app.</p>

<h2>Install it on your phone</h2>
<p>On iPhone, open shouldidive.com in Safari, tap Share, then Add to Home Screen. On Android, Chrome offers Install app from its menu. Installed, it opens full screen and keeps the last data it loaded for when you lose signal at the beach.</p>

<h2>How far to trust it</h2>
<p>Most layers come straight from NOAA, NASA, and the HF radar network, and their limits are the limits of those products. The visibility estimate is my own model and it is labelled Modeled because it has not yet shown it can track what divers report. When I do not have a direct reading, the map stays blank rather than guess. Look at the water yourself before you go in.</p>`;
  return {
    path,
    html: page({
      path,
      title: "About ShouldIDive: a free ocean conditions map for California and Baja",
      description: "ShouldIDive is a free, one-person ocean conditions map for divers, spearfishers, anglers, and boaters on the California and Baja coast. No account and no ads.",
      body,
      crumbs: [{ name: "Home", href: "/" }, { name: "About", href: path }],
      jsonld: [{
        "@context": "https://schema.org",
        "@type": "AboutPage",
        name: "About ShouldIDive",
        url: `${SITE}${path}`,
      }],
    }),
  };
}

export function renderGuidePages(ctx) {
  return [diving, spearfishing, fishing, sailing, science, about].map((fn) => fn(ctx));
}

// --- Spots index, 404, home block ----------------------------------------------

function groupBy(arr, key) {
  const m = new Map();
  for (const x of arr) {
    const k = key(x);
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(x);
  }
  return m;
}

export function renderSpotsIndex(rows, regions, now) {
  const path = "/spots/";
  const sections = regions.map(({ id, label }) => {
    const mine = rows.filter((r) => r.spot.region === id);
    if (!mine.length) return "";
    const groups = groupBy(mine, (r) => r.spot.group);
    return `<h2 id="${id}">${esc(label)}</h2>
${[...groups.entries()].map(([g, list]) => `<h3>${esc(g)}</h3>
<div class="tablewrap"><table><tbody>
${list.map((r) => `<tr><td><a href="/spots/${r.spot.slug}/">${esc(r.spot.name)}</a>${r.hasChart ? ' <span class="tag">chart</span>' : ""}</td><td class="num" style="white-space:normal">${esc(r.summary || "no data right now")}</td></tr>`).join("\n")}
</tbody></table></div>`).join("\n")}`;
  }).join("\n");
  const body = `
<h1>Ocean conditions by spot</h1>
<p class="lede">Water temperature, swell, and wind for ${rows.length} dive, fishing, and surf spots in California and Baja, rebuilt about once an hour. Spots marked chart have a detailed depth and kelp chart in the app.</p>
<a class="cta" href="/">Open the live map</a>
${sections}`;
  return {
    path,
    html: page({
      path,
      title: "Ocean conditions by spot: California and Baja | ShouldIDive",
      description: `Today's water temperature, swell, and wind for ${rows.length} spots in California and Baja, from La Jolla and Catalina to Cabo Pulmo and the Sea of Cortez.`,
      body,
      crumbs: [{ name: "Home", href: "/" }, { name: "Spots", href: path }],
      jsonld: [{
        "@context": "https://schema.org",
        "@type": "ItemList",
        name: "ShouldIDive spots",
        numberOfItems: rows.length,
        itemListElement: rows.map((r, i) => ({
          "@type": "ListItem", position: i + 1, name: r.spot.name, url: `${SITE}/spots/${r.spot.slug}/`,
        })),
      }],
    }),
  };
}

export function render404(roster) {
  const body = `
<h1>Page not found</h1>
<p class="lede">That address does not exist on ShouldIDive. The live map, the guides, and every spot page are linked below.</p>
<a class="cta" href="/">Open the live map</a>
<h2>Spots</h2>
<ul class="spotlist">${roster.map((s) => `<li><a href="/spots/${s.slug}/">${esc(s.name)}</a></li>`).join("")}</ul>`;
  return page({
    path: "/404",
    title: "Page not found | ShouldIDive",
    description: "This page does not exist on ShouldIDive.",
    body,
    robots: "noindex",
    canonical: false,
  });
}

export function homeInjection(roster) {
  const groups = groupBy(roster, (s) => s.region);
  const label = { ca: "California", baja: "Baja" };
  return `<details><summary>All ${roster.length} spots</summary>
${[...groups.entries()].map(([r, list]) => `<p><strong>${esc(label[r] || r)}:</strong> ${list
    .map((s) => `<a href="/spots/${s.slug}/">${esc(s.name)}</a>`).join(", ")}</p>`).join("\n")}
</details>`;
}

export { appLink };
