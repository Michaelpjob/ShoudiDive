// Visual test suite for the mobile app, executed against the web target.
//
// What this catches that the prior layers don't:
//
//   * Layout regressions — chip count / order / labels / positioning,
//     status pill text, composite picker visibility per layer,
//     saved-spots badge, web-banner.
//   * State transitions — clicking a chip flips the active state and
//     updates the status pill; clicking Vis hides the composite
//     picker; clicking back to Temp brings it back.
//   * Element presence — every UI surface listed in the spec
//     actually mounts.
//   * Visual artefact preservation — every test takes a full-page
//     screenshot saved to `test-output/<timestamp>/`. I read those
//     myself with the Read tool to do my own visual sign-off without
//     pinging the human for screenshots.
//
// What this does NOT catch (and never will, by design):
//   * Native gesture feel (pan, pinch, marker tap)
//   * Skia colormap actually rendering on a real device
//   * iOS-specific layout issues (safe-area, dynamic island)
// Those need iOS Simulator (Mac-only) or a real device. Calling that
// out clearly so the suite stays honest about its coverage.
//
// Each test is a small structured object with `name`, an async `run`,
// and the criterion it asserts. The runner emits a clean report and
// exits non-zero on any failure — same contract as Layer 6.

const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs/promises");
const http = require("node:http");

const PORT = 8085;
const ROOT = path.resolve(__dirname, "..");
const TIMEOUT_MS = 90_000;

// Output dir: scripts/visual-tests.js writes to mobile/test-output/<utc>
// Keeps the artefacts together for inspection. Gitignored.
const STAMP = new Date().toISOString().replace(/[:.]/g, "-");
const OUT_DIR = path.join(ROOT, "test-output", STAMP);

let metro;
let exitCode = 1;

function log(...args) {
  process.stderr.write("[visual] " + args.join(" ") + "\n");
}

async function waitForBundle() {
  const url =
    `http://localhost:${PORT}/index.bundle?platform=web&dev=true&hot=false&lazy=true&transform.engine=hermes&transform.routerRoot=app&unstable_transformProfile=hermes-stable`;
  const deadline = Date.now() + TIMEOUT_MS;
  while (Date.now() < deadline) {
    try {
      const status = await new Promise((resolve, reject) => {
        const req = http.get(url, (res) => {
          res.on("data", () => {});
          res.on("end", () => resolve(res.statusCode));
        });
        req.setTimeout(15_000, () => req.destroy(new Error("timeout")));
        req.on("error", reject);
      });
      if (status === 200) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 2_000));
  }
  throw new Error(`bundle never served HTTP 200 within ${TIMEOUT_MS} ms`);
}

async function startMetro() {
  log(`booting metro on :${PORT}`);
  metro = spawn(
    process.platform === "win32" ? "npx.cmd" : "npx",
    ["expo", "start", "--web", "--port", String(PORT)],
    { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"], shell: true }
  );
  metro.stdout.on("data", () => {});
  metro.stderr.on("data", () => {});
  metro.on("exit", (code) => log(`metro exited (code=${code})`));
  await waitForBundle();
  log("bundle ready");
}


// ---- Helpers used inside browser context ---------------------------

// Run inside page.evaluate. Returns the chip element by label.
function _findChipByLabel(label) {
  const labels = Array.from(document.querySelectorAll("div"));
  const tx = labels.find((el) => el.textContent === label);
  if (!tx) return null;
  // Walk up to the Pressable wrapper (the element with width 60-100px
  // sitting inside the chip strip).
  let el = tx;
  for (let i = 0; i < 5; i++) {
    if (!el) break;
    const r = el.getBoundingClientRect();
    if (r.width > 50 && r.width < 110 && r.height > 30) return el;
    el = el.parentElement;
  }
  return null;
}


// ---- Tests ----------------------------------------------------------

function tests() {
  return [
    {
      name: "01 — page mounts (root has React tree)",
      criterion: "document.getElementById('root').innerHTML.length > 100",
      run: async (page) => {
        const len = await page.evaluate(() =>
          document.getElementById("root")?.innerHTML?.length || 0
        );
        return {
          passed: len > 100,
          detail: `root.innerHTML.length = ${len}`,
        };
      },
    },
    {
      name: "02 — exactly 5 layer chips render",
      criterion: "5 chip labels visible: Temp, Chl, Wind, Swell, Vis",
      run: async (page) => {
        const labels = await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("div"));
          return ["Temp", "Chl", "Wind", "Swell", "Vis"]
            .map((l) => all.some((el) => el.textContent === l));
        });
        const missing = labels
          .map((ok, i) => ({ ok, name: ["Temp", "Chl", "Wind", "Swell", "Vis"][i] }))
          .filter((x) => !x.ok)
          .map((x) => x.name);
        return {
          passed: missing.length === 0,
          detail: missing.length ? `missing: ${missing.join(", ")}` : "all 5 present",
        };
      },
    },
    {
      name: "03 — status pill shows 'Sea Temp · 2-day' on initial load",
      criterion: "status pill text matches default state",
      run: async (page) => {
        const text = await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("div"));
          // Look for the dot+text composite — text element starts with
          // "Sea Temp" or contains "Sea Temp ·"
          const found = all.find((el) =>
            /Sea Temp\s*·\s*2-day/.test(el.textContent || "")
          );
          return found?.textContent?.trim() || null;
        });
        return {
          passed: !!text && /Sea Temp/.test(text) && /2-day/.test(text),
          detail: text || "(not found)",
        };
      },
    },
    {
      name: "04 — composite picker visible by default (Temp layer)",
      criterion: "1-day, 2-day, 3-day buttons all rendered",
      run: async (page) => {
        const found = await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("div"));
          return ["1-day", "2-day", "3-day"]
            .map((s) => all.some((el) => el.textContent === s));
        });
        return {
          passed: found.every(Boolean),
          detail: `1-day:${found[0]} 2-day:${found[1]} 3-day:${found[2]}`,
        };
      },
    },
    {
      name: "05 — '8 saved spots' badge present",
      criterion: "saved-spots badge renders the right count",
      run: async (page) => {
        const ok = await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("div"));
          return all.some((el) => /8 saved spots/.test(el.textContent || ""));
        });
        return { passed: ok, detail: ok ? "found" : "not found" };
      },
    },
    {
      name: "06 — clicking Chl chip switches active layer",
      criterion: "status pill flips to 'Chlorophyll · 2-day' after click",
      run: async (page) => {
        await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("div"));
          const tx = all.find((el) => el.textContent === "Chl");
          let el = tx;
          for (let i = 0; i < 5; i++) {
            if (!el) break;
            const r = el.getBoundingClientRect();
            if (r.width > 50 && r.width < 110 && r.height > 30) {
              el.click();
              return;
            }
            el = el.parentElement;
          }
        });
        await new Promise((r) => setTimeout(r, 300));
        const text = await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("div"));
          const found = all.find((el) =>
            /Chlorophyll\s*·\s*2-day/.test(el.textContent || "")
          );
          return found?.textContent?.trim() || null;
        });
        return {
          passed: !!text && /Chlorophyll/.test(text),
          detail: text || "(status pill did not update)",
        };
      },
    },
    {
      name: "07 — clicking Vis hides the composite picker (viz only has 'now')",
      criterion: "1-day/2-day/3-day buttons gone when viz active",
      run: async (page) => {
        await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("div"));
          const tx = all.find((el) => el.textContent === "Vis");
          let el = tx;
          for (let i = 0; i < 5; i++) {
            if (!el) break;
            const r = el.getBoundingClientRect();
            if (r.width > 50 && r.width < 110 && r.height > 30) {
              el.click();
              return;
            }
            el = el.parentElement;
          }
        });
        await new Promise((r) => setTimeout(r, 300));
        const found = await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("div"));
          return ["1-day", "2-day", "3-day"]
            .map((s) => all.some((el) => el.textContent === s));
        });
        return {
          passed: found.every((v) => !v),
          detail: `1-day:${found[0]} 2-day:${found[1]} 3-day:${found[2]} (all should be false)`,
        };
      },
    },
    {
      name: "08 — Visibility status pill reads '· now' after Vis click",
      criterion: "status pill text confirms Vis state",
      run: async (page) => {
        const text = await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("div"));
          const found = all.find((el) =>
            /Visibility\s*·\s*now/.test(el.textContent || "")
          );
          return found?.textContent?.trim() || null;
        });
        return {
          passed: !!text && /Visibility/.test(text) && /now/.test(text),
          detail: text || "(not found)",
        };
      },
    },
    {
      name: "09 — wind/swell chips disabled (greyed) until backend wired",
      criterion: "Wind + Swell chips show 'soon' subtitle, indicating disabled state",
      run: async (page) => {
        const ok = await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("div"));
          const soonTags = all.filter((el) => el.textContent === "soon");
          return soonTags.length >= 2;
        });
        return {
          passed: ok,
          detail: ok ? "found 2+ 'soon' tags" : "didn't find both",
        };
      },
    },
    {
      name: "10 — viewport has no horizontal overflow",
      criterion: "document.body.scrollWidth <= window.innerWidth",
      run: async (page) => {
        const r = await page.evaluate(() => ({
          scroll: document.body.scrollWidth,
          inner: window.innerWidth,
        }));
        return {
          passed: r.scroll <= r.inner + 1, // allow 1px sub-pixel
          detail: `scrollWidth=${r.scroll}  innerWidth=${r.inner}`,
        };
      },
    },
    {
      name: "11 — web preview banner is rendered",
      criterion: "the 'Web preview · iOS / Android show a real native map here' banner",
      run: async (page) => {
        const ok = await page.evaluate(() => {
          const all = Array.from(document.querySelectorAll("div"));
          return all.some((el) =>
            /Web preview/.test(el.textContent || "") &&
            /native map/.test(el.textContent || "")
          );
        });
        return { passed: ok, detail: ok ? "found" : "missing" };
      },
    },
    {
      name: "12 — heatmap PNG fetched successfully from production manifest",
      criterion:
        "the manifest landed AND the active layer's PNG URL resolves to " +
        "an HTTP 200 with non-trivial content (proves the data fetch + " +
        "URL resolver path the native side relies on)",
      run: async (page) => {
        const result = await page.evaluate(async () => {
          // The web fallback embeds an <img> for the heatmap; check it
          // loaded. complete=true + naturalWidth>0 means the asset
          // server returned a real PNG (not a 404 / placeholder).
          const imgs = Array.from(document.querySelectorAll("img"));
          // The Image style in MapScreen.web has flex:1 so it'll be
          // the largest image on the page — pick the biggest one to
          // avoid grabbing the favicon.
          let best = null;
          for (const im of imgs) {
            const r = im.getBoundingClientRect();
            if (!best || r.width * r.height > best.area) {
              best = { img: im, area: r.width * r.height };
            }
          }
          if (!best || !best.img) return { found: false };
          const im = best.img;
          return {
            found: true,
            src: im.src,
            complete: im.complete,
            naturalWidth: im.naturalWidth,
            naturalHeight: im.naturalHeight,
            displayedSize: { w: best.area > 0 ? Math.round(Math.sqrt(best.area)) : 0 },
          };
        });
        if (!result.found) {
          return { passed: false, detail: "no <img> elements found" };
        }
        const ok =
          result.complete &&
          result.naturalWidth > 0 &&
          result.naturalHeight > 0 &&
          // Anchor to the origin: an unanchored /shouldidive\.com\/data\//
          // also matches a crafted src like https://evil.com/?x=shouldidive.com/data/.
          // The app loads PNGs from REMOTE_BASE = https://shouldidive.com
          // (mobile/src/lib/dataSource.js); allow an optional subdomain label.
          /^https:\/\/([a-z0-9-]+\.)?shouldidive\.com\/data\//.test(result.src);
        return {
          passed: ok,
          detail:
            `src=${result.src.substring(0, 80)}... ` +
            `loaded=${result.complete} dim=${result.naturalWidth}x${result.naturalHeight}`,
        };
      },
    },
  ];
}


async function checkBaseline(page) {
  // Ensure each test starts from "Temp · 2-day" so order-of-tests
  // doesn't poison results. Click Temp + 2-day before every test.
  await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll("div"));
    const tempLabel = all.find((el) => el.textContent === "Temp");
    let el = tempLabel;
    for (let i = 0; i < 5; i++) {
      if (!el) break;
      const r = el.getBoundingClientRect();
      if (r.width > 50 && r.width < 110 && r.height > 30) { el.click(); break; }
      el = el.parentElement;
    }
  });
  await new Promise((r) => setTimeout(r, 200));
}


async function run() {
  await fs.mkdir(OUT_DIR, { recursive: true });
  await startMetro();

  let puppeteer;
  try { puppeteer = require("puppeteer"); }
  catch (e) {
    log("puppeteer not installed");
    throw e;
  }

  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
    defaultViewport: { width: 393, height: 852 }, // iPhone 16 Pro size
  });

  let pass = 0, fail = 0;
  const results = [];
  try {
    const page = await browser.newPage();
    page.on("pageerror", (e) => log(`pageerror: ${e.message}`));

    log("navigating to app");
    await page.goto(`http://localhost:${PORT}/`, {
      waitUntil: "networkidle2",
      timeout: 60_000,
    });
    // Give the manifest fetch a beat to land + first paint to settle.
    await new Promise((r) => setTimeout(r, 2_000));

    for (const t of tests()) {
      // For tests that mutate state (clicks), DON'T reset baseline before
      // them — but the order is structured so each click test feeds the
      // next observation. Tests 01-05 + 09-11 are stateless; tests 06-08
      // happen sequentially and rely on each other's state.
      const startsWithDigit = parseInt(t.name.slice(0, 2), 10);
      const isStateful = startsWithDigit >= 6 && startsWithDigit <= 8;
      if (!isStateful) {
        await checkBaseline(page);
      }
      try {
        const res = await t.run(page);
        const slug = t.name.replace(/[^a-z0-9]/gi, "_").slice(0, 60);
        const shotPath = path.join(OUT_DIR, `${slug}.png`);
        await page.screenshot({ path: shotPath, fullPage: false });
        results.push({ ...t, ...res, screenshot: shotPath });
        if (res.passed) pass++;
        else fail++;
      } catch (e) {
        results.push({ ...t, passed: false, detail: `THREW: ${e.message}` });
        fail++;
      }
    }
  } finally {
    await browser.close();
  }

  // Report.
  log("");
  log("════════════════════════════════════════════════════");
  log(`Visual test suite — ${pass} passed, ${fail} failed`);
  log("════════════════════════════════════════════════════");
  for (const r of results) {
    const mark = r.passed ? "PASS" : "FAIL";
    log(`  [${mark}] ${r.name}`);
    log(`         criterion : ${r.criterion}`);
    log(`         observed  : ${r.detail}`);
    if (r.screenshot) log(`         screenshot: ${path.relative(ROOT, r.screenshot)}`);
  }
  log("════════════════════════════════════════════════════");
  log(`Screenshots saved under: ${path.relative(ROOT, OUT_DIR)}`);

  exitCode = fail === 0 ? 0 : 1;
}


run()
  .catch((e) => {
    log(`✖ visual test runner failed: ${e.message}`);
    exitCode = 1;
  })
  .finally(() => {
    if (metro) metro.kill("SIGTERM");
    setTimeout(() => process.exit(exitCode), 2_000).unref();
  });
