// Runtime smoke test for the web bundle.
//
// Layers 1–5 of validate.sh catch compile-time problems. Layer 6
// (this script) catches RUNTIME problems that compile cleanly but
// throw the moment a component mounts — e.g. Skia v2's Canvas, which
// uses a lazy proxy that only requires reanimated when its first
// property is accessed. That class of bug passed every prior layer
// and only surfaced when the app booted on a real device.
//
// Strategy:
//   1. Boot Metro on the web target (port 8083).
//   2. Wait for the bundle to be served (HTTP 200).
//   3. Use Puppeteer to actually load the page in headless Chromium.
//   4. Listen for `pageerror` / console.error events. Any uncaught
//      JS exception during the first 5 s = a runtime regression.
//      Specifically watches for "react-native-reanimated is not
//      installed" — the exact error we previously shipped to a real
//      device.
//   5. Tear everything down and exit 0 on clean / 1 on any error.
//
// This is the closest we can get to "did the app actually run?"
// without a real device or simulator.

const { spawn } = require("node:child_process");
const path = require("node:path");
const http = require("node:http");

const PORT = 8084; // distinct from dev ports so we don't clash
const ROOT = path.resolve(__dirname, "..");
const TIMEOUT_MS = 90_000;
const PAGE_OBSERVE_MS = 5_000;

let metro;
let exitCode = 1;

function log(...args) {
  process.stderr.write("[smoke-web] " + args.join(" ") + "\n");
}

async function waitForBundle() {
  const url =
    `http://localhost:${PORT}/index.bundle?platform=web&dev=true&hot=false&lazy=true&transform.engine=hermes&transform.routerRoot=app&unstable_transformProfile=hermes-stable`;
  const deadline = Date.now() + TIMEOUT_MS;
  while (Date.now() < deadline) {
    try {
      const status = await new Promise((resolve, reject) => {
        const req = http.get(url, (res) => {
          // Drain the body so the connection closes cleanly.
          res.on("data", () => {});
          res.on("end", () => resolve(res.statusCode));
        });
        req.setTimeout(15_000, () => req.destroy(new Error("timeout")));
        req.on("error", reject);
      });
      if (status === 200) return;
      log(`bundle status ${status}, retrying...`);
    } catch (e) {
      log(`bundle fetch retry: ${e.message}`);
    }
    await new Promise((r) => setTimeout(r, 2_000));
  }
  throw new Error(`bundle never served HTTP 200 within ${TIMEOUT_MS} ms`);
}

async function startMetro() {
  log(`booting metro on :${PORT}`);
  // Windows needs shell: true to invoke .cmd shims; without it the
  // spawn fails immediately with EINVAL. shell: true is fine on
  // POSIX too, just slightly less direct.
  metro = spawn(
    process.platform === "win32" ? "npx.cmd" : "npx",
    ["expo", "start", "--web", "--port", String(PORT)],
    { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"], shell: true }
  );
  metro.stdout.on("data", (b) => process.stderr.write("[metro] " + b));
  metro.stderr.on("data", (b) => process.stderr.write("[metro] " + b));
  metro.on("exit", (code) => log(`metro exited (code=${code})`));
  await waitForBundle();
  log("bundle ready");
}

async function checkRuntime() {
  let puppeteer;
  try {
    // Lazy require so the script's import phase doesn't hard-fail
    // on machines without puppeteer yet — we install it only when
    // Layer 6 runs.
    puppeteer = require("puppeteer");
  } catch (e) {
    log("puppeteer not installed — install with: npm i -D puppeteer");
    throw e;
  }

  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  const errors = [];
  try {
    const page = await browser.newPage();
    page.on("pageerror", (e) => {
      errors.push(`pageerror: ${e.message}`);
    });
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        errors.push(`console.error: ${msg.text()}`);
      }
    });
    page.on("requestfailed", (req) => {
      errors.push(`requestfailed: ${req.url()} (${req.failure()?.errorText})`);
    });

    log(`navigating to http://localhost:${PORT}/`);
    await page.goto(`http://localhost:${PORT}/`, {
      waitUntil: "networkidle2",
      timeout: 60_000,
    });

    log(`page loaded, observing for ${PAGE_OBSERVE_MS} ms...`);
    await new Promise((r) => setTimeout(r, PAGE_OBSERVE_MS));

    // Did the React tree actually mount? If Canvas threw during
    // render, the root div would be empty.
    const rootSize = await page.evaluate(() => {
      const root = document.getElementById("root");
      return root ? root.innerHTML.length : 0;
    });
    log(`root content length = ${rootSize} chars`);
    if (rootSize < 100) {
      errors.push(
        `root container is empty (${rootSize} chars) — React tree didn't mount`
      );
    }
  } finally {
    await browser.close();
  }

  // Filter false positives (favicons / SourceMap-not-found are noise).
  const real = errors.filter(
    (e) =>
      !/\/favicon\.ico/.test(e) &&
      !/SourceMap/.test(e) &&
      !/Failed to load resource/.test(e) &&
      !/net::ERR_FAILED.*\.map/.test(e)
  );
  return real;
}

async function main() {
  try {
    await startMetro();
    const errors = await checkRuntime();
    if (errors.length > 0) {
      log(`✖ ${errors.length} runtime error(s):`);
      for (const e of errors) log("  " + e);
      exitCode = 1;
    } else {
      log("✓ app mounted, no runtime errors observed");
      exitCode = 0;
    }
  } catch (e) {
    log(`✖ smoke test failed: ${e.message}`);
    exitCode = 1;
  } finally {
    if (metro) {
      log("stopping metro");
      metro.kill("SIGTERM");
      // Force-kill on Windows where SIGTERM is best-effort.
      setTimeout(() => metro && metro.kill("SIGKILL"), 5_000).unref();
    }
    setTimeout(() => process.exit(exitCode), 2_000).unref();
  }
}

main();
