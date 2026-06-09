#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const packageRoot = path.resolve(__dirname, "..", "..");
const packageJson = JSON.parse(fs.readFileSync(path.join(packageRoot, "package.json"), "utf8"));
const args = process.argv.slice(2);

function findPython() {
  const candidates = [
    process.env.PYTHON,
    process.env.PYTHON3,
    process.platform === "win32" ? "py" : undefined,
    "python3",
    "python",
  ].filter(Boolean);

  for (const candidate of candidates) {
    const probeArgs = candidate === "py" ? ["-3", "--version"] : ["--version"];
    const probe = spawnSync(candidate, probeArgs, { stdio: "ignore" });
    if (!probe.error && probe.status === 0) {
      return candidate;
    }
  }
  return null;
}

function runPython(python, pythonArgs, options = {}) {
  const result = spawnSync(python, pythonArgs, {
    stdio: "inherit",
    env: options.env || process.env,
  });
  if (result.error) {
    console.error(`Unable to run Python command with ${python}: ${result.error.message}`);
    process.exit(1);
  }
  process.exit(result.status === null ? 1 : result.status);
}

if (args.includes("--node-wrapper-version")) {
  console.log(`grimoire-kit npm wrapper ${packageJson.version}`);
  process.exit(0);
}

const python = findPython();
if (!python) {
  console.error("Python 3.12+ is required to run grimoire-kit.");
  console.error("Install Python, then retry this command.");
  process.exit(1);
}

if (args[0] === "install-python") {
  runPython(python, ["-m", "pip", "install", `grimoire-kit==${packageJson.version}`]);
}

const pythonPath = path.join(packageRoot, "src");
const env = {
  ...process.env,
  PYTHONPATH: process.env.PYTHONPATH
    ? `${pythonPath}${path.delimiter}${process.env.PYTHONPATH}`
    : pythonPath,
};

const pythonArgs = python === "py"
  ? ["-3", "-m", "grimoire.cli.app", ...args]
  : ["-m", "grimoire.cli.app", ...args];

runPython(python, pythonArgs, { env });
