const ENGINE_BASE = "http://localhost:8001";
const HEALTH_CHECK_INTERVAL_MS = 30_000;

export const maxDuration = 600; // full multi-pair/60-day backtests can take minutes
export const dynamic = "force-dynamic";

let lastHealthCheck = 0;
let engineHealthy = false;

async function checkEngineHealth(): Promise<boolean> {
  const now = Date.now();
  if (now - lastHealthCheck < HEALTH_CHECK_INTERVAL_MS) {
    return engineHealthy;
  }
  lastHealthCheck = now;
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    const res = await fetch(`${ENGINE_BASE}/health`, { signal: controller.signal });
    clearTimeout(timeout);
    engineHealthy = res.ok;
  } catch {
    engineHealthy = false;
  }
  return engineHealthy;
}

function offlineResponse() {
  return Response.json(
    {
      error: "engine_offline",
      detail:
        "The Python trading engine isn't reachable on http://localhost:8001. Start it with: cd trading-engine && .venv/Scripts/python -m uvicorn main:app --port 8001",
    },
    { status: 503 }
  );
}

async function proxy(req: Request, path: string[]) {
  if (!(await checkEngineHealth())) {
    return offlineResponse();
  }
  const search = new URL(req.url).search;
  const target = `${ENGINE_BASE}/${path.join("/")}${search}`;

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 290_000);
    const init: RequestInit = {
      method: req.method,
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    };
    if (req.method !== "GET" && req.method !== "HEAD") {
      init.body = await req.text();
    }
    const res = await fetch(target, init);
    clearTimeout(timeout);
    const body = await res.text();
    return new Response(body, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("Content-Type") ?? "application/json" },
    });
  } catch (err) {
    return Response.json({ error: "engine_unreachable", detail: String(err), path: target }, { status: 502 });
  }
}

export async function GET(req: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(req, path);
}

export async function POST(req: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(req, path);
}
