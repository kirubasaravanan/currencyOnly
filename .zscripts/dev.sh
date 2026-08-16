#!/bin/bash
# Local dev orchestrator. Single engine-restart mechanism only
# (mini-services/trading-engine/spawn-engine.cjs) — deliberately not
# duplicated here, unlike the existing Forex/Forex app's dev.sh which runs
# two independent restart loops for the same process.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "[dev.sh] Installing frontend deps..."
bun install

echo "[dev.sh] Starting Next.js dashboard (port 3006)..."
bun run dev &
NEXT_PID=$!

echo "[dev.sh] Starting Python engine supervisor (port 8001)..."
(cd mini-services/trading-engine && bun install --silent 2>/dev/null || true; node spawn-engine.cjs) &
ENGINE_PID=$!

trap 'kill $NEXT_PID $ENGINE_PID 2>/dev/null' EXIT INT TERM

echo "[dev.sh] Dashboard: http://localhost:3006  Engine: http://localhost:8001"
wait
