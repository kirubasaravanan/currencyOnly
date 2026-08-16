const PROXY_BASE = "/api/trading";

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${PROXY_BASE}${path}`, { ...init, cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${path}: ${body}`);
  }
  return res.json();
}

export interface PairCalibration {
  session_windows_ist: [number, number][];
  stop_mult: number;
  tp_mult: number;
  tp2_mult: number;
  para_thresh: number;
  sweep_lb: number;
  sweep_mem: number;
  mss_lb: number;
  max_sl_pips: number;
  min_sl_pips: number;
  conf_boost: number;
  use_trend_filter: boolean;
  activation_usd: number;
  activation_pct: number;
  trail_lock_pct: number;
  confidence_threshold: number;
}

export interface Trade {
  id: number;
  symbol: string;
  side: "BULLISH" | "BEARISH";
  lots: number;
  entry_price: number;
  sl_price: number;
  tp_price: number;
  tp2_price: number;
  rr: number;
  confidence: number;
  session: string;
  opened_at: string;
  closed_at?: string;
  status: "open" | "closed";
  pnl: number;
  pnl_gross?: number;
  commission_paid?: number;
  pnl_pips: number;
  reason?: string;
  partial_taken: boolean;
  be_moved: boolean;
}

export interface SymbolStats {
  trades: number;
  wins: number;
  losses: number;
  pnl_gross: number;
  commission: number;
  pnl_net: number;
  win_rate: number;
}

export interface Stats {
  total_trades: number;
  open_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number | null;
  expectancy: number;
  pnl_gross: number;
  commission_paid: number;
  pnl_net: number;
  equity: number;
  balance: number;
  peak_equity: number;
  drawdown_pct: number;
  by_symbol: Record<string, SymbolStats>;
}

export interface Signal {
  symbol: string;
  side: string;
  entry_price: number;
  sl_price: number;
  tp_price: number;
  tp2_price: number;
  rr: number;
  confidence: number;
  threshold: number;
  session: string;
  adr_used_pct: number;
  adr_exhausted: boolean;
  factor_scores: Record<string, number>;
  reasons: Record<string, unknown>;
}

export interface Snapshot {
  engine: {
    running: boolean;
    started_at: number;
    last_scan_at: number;
    scan_count: number;
    last_error: string | null;
  };
  pairs: string[];
  calibration: Record<string, PairCalibration>;
  signals: Record<string, Signal>;
  open_trades: Trade[];
  closed_trades: Trade[];
  stats: Stats;
  threshold: number;
}

export interface MacroSnapshot {
  dxy_direction: string;
  us10y_direction: string;
  usd_bias: string;
  news_blackout: { blocked: boolean; event: string | null; country?: string; minutes_to_event?: number };
  calendar_next_24h: Array<{ title: string; country: string; time: string; impact: string }>;
}

export interface BacktestRun {
  id: string;
  status: "running" | "done" | "error";
  progress: number;
  symbols: string[];
  days: number;
  started_at: string;
  error?: string;
}

export interface BacktestResult {
  symbols: string[];
  days: number;
  entry_timeframe: string;
  bars_processed: number;
  actual_days_covered: number;
  data_coverage_warning: boolean;
  run_at: string;
  stats: Stats;
  closed_trades: Trade[];
}

export const api = {
  snapshot: () => fetchJson<Snapshot>("/snapshot"),
  stats: () => fetchJson<Stats>("/stats"),
  trades: () => fetchJson<{ open: Trade[]; closed: Trade[] }>("/trades"),
  closeTrade: (id: number) => fetchJson<Trade>(`/trades/${id}/close`, { method: "POST" }),
  resetAccount: () => fetchJson<{ status: string }>("/reset", { method: "POST" }),
  signals: () => fetchJson<Record<string, Signal>>("/signals"),
  pairs: () => fetchJson<{ pairs: string[]; calibration: Record<string, PairCalibration> }>("/pairs"),
  pairAnalysis: (symbol: string) => fetchJson(`/pairs/${symbol}/analysis`),
  macro: () => fetchJson<MacroSnapshot>("/macro"),
  calendar: (hours = 24) => fetchJson(`/calendar?hours=${hours}`),
  setThreshold: (threshold: number) =>
    fetchJson<{ threshold: number }>("/threshold", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ threshold }),
    }),
  runBacktest: (symbols: string[] | null, days: number) =>
    fetchJson<BacktestRun>("/backtest/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols, days }),
    }),
  backtestStatus: (id: string) => fetchJson<BacktestRun>(`/backtest/status/${id}`),
  backtestResultsList: () => fetchJson<BacktestRun[]>("/backtest/results"),
  backtestResult: (id: string) => fetchJson<BacktestResult>(`/backtest/results/${id}`),
};
