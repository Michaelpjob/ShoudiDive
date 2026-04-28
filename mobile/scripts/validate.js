#!/usr/bin/env node

const fs = require("fs/promises");
const path = require("path");
const { spawn } = require("child_process");

const ROOT = path.resolve(__dirname, "..");

function commandName(bin) {
  return process.platform === "win32" ? `${bin}.cmd` : bin;
}

function divider() {
  console.log("-".repeat(64));
}

async function removeIfPresent(relPath) {
  await fs.rm(path.join(ROOT, relPath), { recursive: true, force: true });
}

function run(bin, args, options = {}) {
  const {
    cwd = ROOT,
    capture = false,
    allowNonZero = false,
    shell = process.platform === "win32" && /\.cmd$/i.test(bin),
  } = options;

  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, {
      cwd,
      env: process.env,
      shell,
      stdio: capture ? ["inherit", "pipe", "pipe"] : "inherit",
    });

    let stdout = "";
    let stderr = "";

    if (capture) {
      child.stdout.on("data", (chunk) => {
        const text = chunk.toString();
        stdout += text;
        process.stdout.write(text);
      });
      child.stderr.on("data", (chunk) => {
        const text = chunk.toString();
        stderr += text;
        process.stderr.write(text);
      });
    }

    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0 || allowNonZero) {
        resolve({ code, stdout, stderr });
        return;
      }
      reject(new Error(`${bin} ${args.join(" ")} exited with code ${code}`));
    });
  });
}

async function main() {
  console.log("");
  console.log(">> Layer 1/7 - expo install --check (SDK alignment)");
  divider();
  await run(commandName("npx"), ["expo", "install", "--check"]);

  console.log("");
  console.log(">> Layer 2/7 - expo-doctor (project health)");
  divider();
  const doctor = await run(
    commandName("npx"),
    ["--yes", "expo-doctor"],
    { capture: true, allowNonZero: true }
  );
  const doctorOutput = `${doctor.stdout}\n${doctor.stderr}`;
  if (doctor.code !== 0 || /critical|fatal|error/i.test(doctorOutput)) {
    throw new Error("expo-doctor reported critical issue(s).");
  }

  console.log("");
  console.log(">> Layer 3/7 - expo export ios (Hermes bundle compile)");
  divider();
  await removeIfPresent("dist");
  await removeIfPresent(path.join(".expo", "cache"));
  await run(commandName("npx"), [
    "expo",
    "export",
    "--platform",
    "ios",
    "--output-dir",
    "dist",
    "--clear",
  ]);

  console.log("");
  console.log(">> Layer 4/7 - expo export web (fallback compile)");
  divider();
  await removeIfPresent("dist");
  await run(commandName("npx"), [
    "expo",
    "export",
    "--platform",
    "web",
    "--output-dir",
    "dist",
  ]);

  console.log("");
  console.log(">> Layer 5/7 - jest (unit + native screen tests)");
  divider();
  await run(commandName("npx"), ["jest", "--silent", "--colors"]);

  await removeIfPresent("dist");

  console.log("");
  console.log(">> Layer 6/7 - runtime smoke test");
  divider();
  await run(process.execPath, [path.join("scripts", "smoke-web.js")]);

  console.log("");
  console.log(">> Layer 7/7 - visual test suite");
  divider();
  await run(process.execPath, [path.join("scripts", "visual-tests.js")]);

  console.log("");
  divider();
  console.log("OK All 7 validation layers passed.");
  console.log("   Mobile bundle compiles, boots, and meets visual criteria.");
  divider();
}

main().catch(async (error) => {
  await removeIfPresent("dist");
  console.error("");
  console.error(`Validation failed: ${error.message}`);
  process.exit(1);
});
