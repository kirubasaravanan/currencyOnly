// Spawns and supervises the Python uvicorn trading engine on port 8001.
// Ported from the existing Forex/Forex app's supervisor (Explore-confirmed
// clean, cross-platform-ish) — this repo intentionally keeps only this one
// restart mechanism, not also a duplicate loop in a dev.sh script.

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const ENGINE_DIR = path.resolve(__dirname, "../../trading-engine");
const LOG_FILE = path.join(ENGINE_DIR, "engine.log");
const PYTHON = path.join(ENGINE_DIR, ".venv", "Scripts", "python.exe");

function log(msg) {
  const line = `[${new Date().toISOString()}] [spawn-engine] ${msg}`;
  console.log(line);
  try {
    fs.appendFileSync(LOG_FILE, line + "\n");
  } catch (_) {
    // ignore
  }
}

function startEngine() {
  const pythonExe = fs.existsSync(PYTHON) ? PYTHON : "python";
  log(`Starting uvicorn trading engine via ${pythonExe}...`);
  const proc = spawn(
    pythonExe,
    ["-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"],
    {
      cwd: ENGINE_DIR,
      stdio: ["ignore", "ignore", "ignore"],
      detached: false,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    }
  );

  proc.on("error", (err) => {
    log(`spawn error: ${err.message}`);
  });

  proc.on("exit", (code, signal) => {
    log(`uvicorn exited (code=${code}, signal=${signal}). Restarting in 5s...`);
    setTimeout(startEngine, 5000);
  });

  return proc;
}

setInterval(() => {
  try {
    require("http").get("http://localhost:8001/health", (res) => {
      log(res.statusCode === 200 ? "health OK" : `health check returned ${res.statusCode}`);
    }).on("error", (e) => log(`health check error: ${e.message}`));
  } catch (e) {
    // ignore
  }
}, 60000);

startEngine();

process.on("SIGTERM", () => {
  log("got SIGTERM, exiting");
  process.exit(0);
});
process.on("SIGINT", () => {
  log("got SIGINT, exiting");
  process.exit(0);
});

setInterval(() => {}, 1 << 30);
