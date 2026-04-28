#!/usr/bin/env node

const path = require("path");
const { spawn } = require("child_process");

const ROOT = path.resolve(__dirname, "..");

function divider() {
  console.log("-".repeat(64));
}

function run(bin, args, options = {}) {
  const {
    cwd = ROOT,
    shell = process.platform === "win32" && /\.cmd$/i.test(bin),
  } = options;

  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, {
      cwd,
      env: process.env,
      shell,
      stdio: "inherit",
    });

    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`${bin} ${args.join(" ")} exited with code ${code}`));
    });
  });
}

async function main() {
  console.log("");
  console.log(">> Strict gate 1/2 - full local validate");
  divider();
  await run(process.execPath, [path.join("scripts", "validate.js")]);

  console.log("");
  console.log(">> Strict gate 2/2 - live data contract");
  divider();
  await run(process.execPath, [path.join("scripts", "live-data-contract.js")]);

  console.log("");
  divider();
  console.log("OK Strict validation passed.");
  console.log("   Local behavior, native contracts, and live data all checked.");
  divider();
}

main().catch((error) => {
  console.error("");
  console.error(`Strict validation failed: ${error.message}`);
  process.exit(1);
});
