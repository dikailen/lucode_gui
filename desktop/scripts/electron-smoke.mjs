import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const electronBin = require("electron");

const child = spawn(electronBin, ["."], {
  cwd: desktopRoot,
  env: {
    ...process.env,
    LUCODE_RUNTIME_BASE_URL: process.env.LUCODE_RUNTIME_BASE_URL || "http://127.0.0.1:43217",
    LUCODE_RUNTIME_TOKEN: process.env.LUCODE_RUNTIME_TOKEN || "dev-runtime-token",
    LUCODE_ELECTRON_SMOKE: "1",
    LUCODE_ELECTRON_SMOKE_EXIT_MS: "1200",
  },
  stdio: "inherit",
  windowsHide: true,
});

child.on("exit", (code, signal) => {
  if (signal) {
    console.error(`Electron smoke exited by signal ${signal}`);
    process.exit(1);
  }
  process.exit(code ?? 0);
});
