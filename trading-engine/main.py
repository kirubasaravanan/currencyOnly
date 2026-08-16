"""FastAPI app entry. `.env` is loaded before importing engine modules
since some of them read env vars (OANDA_API_TOKEN) as module-level
constants at import time."""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from engine import orchestrator
from routes import register_routes

app = FastAPI(title="currencyOnly Trading Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    await orchestrator.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await orchestrator.stop()


@app.get("/health")
async def health():
    return {"status": "ok", "engine": "running"}


register_routes(app)
