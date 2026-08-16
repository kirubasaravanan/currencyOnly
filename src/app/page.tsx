"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Snapshot, type MacroSnapshot } from "@/lib/trading-api";
import { Header } from "@/components/dashboard/header";
import { EngineStatusBar } from "@/components/dashboard/engine-status";
import { KpiRow } from "@/components/dashboard/kpi-row";
import { SignalsPanel } from "@/components/dashboard/signals-panel";
import { OpenPositionsPanel } from "@/components/dashboard/open-positions";
import { EquityCurve } from "@/components/dashboard/equity-curve";
import { TradeHistoryPanel } from "@/components/dashboard/trade-history";
import { BacktestPanel } from "@/components/dashboard/backtest-panel";
import { MacroPanel } from "@/components/dashboard/macro-panel";
import { PairCalibrationPanel } from "@/components/dashboard/pair-calibration-panel";
import { CurrencyExposurePanel } from "@/components/dashboard/currency-exposure-panel";

const REFRESH_MS = 5000;
const MACRO_REFRESH_MS = 60000;
const INITIAL_BALANCE = 10000;

export default function Home() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [macro, setMacro] = useState<MacroSnapshot | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState("");
  const requestSeq = useRef(0);

  const refresh = useCallback(async () => {
    const seq = ++requestSeq.current;
    try {
      const snap = await api.snapshot();
      if (seq !== requestSeq.current) return;
      setSnapshot(snap);
      setError(null);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (e) {
      if (seq === requestSeq.current) setError(String(e));
    }
  }, []);

  const refreshMacro = useCallback(async () => {
    try {
      setMacro(await api.macro());
    } catch {
      // macro is best-effort context, not critical path
    }
  }, []);

  useEffect(() => {
    refresh();
    refreshMacro();
  }, [refresh, refreshMacro]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
  }, [autoRefresh, refresh]);

  useEffect(() => {
    const id = setInterval(refreshMacro, MACRO_REFRESH_MS);
    return () => clearInterval(id);
  }, [refreshMacro]);

  async function handleCloseTrade(id: number) {
    await api.closeTrade(id);
    refresh();
  }

  async function handleReset() {
    if (!confirm("Reset the paper account to $10,000 and clear all trade history?")) return;
    await api.resetAccount();
    refresh();
  }

  return (
    <div className="max-w-[1600px] mx-auto p-4">
      <Header
        autoRefresh={autoRefresh}
        onToggleAutoRefresh={() => setAutoRefresh((v) => !v)}
        onRefresh={refresh}
        onReset={handleReset}
        lastUpdated={lastUpdated}
      />
      <EngineStatusBar engine={snapshot?.engine ?? null} />
      {error && (
        <div className="mb-4 rounded border p-3 text-sm" style={{ borderColor: "var(--red)", color: "var(--red)" }}>
          {error}
        </div>
      )}
      {snapshot?.stats && <KpiRow stats={snapshot.stats} />}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <div className="lg:col-span-8 space-y-4">
          {snapshot && <SignalsPanel signals={snapshot.signals} />}
          {snapshot && <OpenPositionsPanel trades={snapshot.open_trades} onClose={handleCloseTrade} />}
          {snapshot && <EquityCurve trades={snapshot.closed_trades} initialBalance={INITIAL_BALANCE} />}
          {snapshot && <TradeHistoryPanel trades={snapshot.closed_trades} />}
          {snapshot && <BacktestPanel pairs={snapshot.pairs} />}
        </div>
        <div className="lg:col-span-4 space-y-4">
          <MacroPanel macro={macro} />
          {snapshot && <CurrencyExposurePanel openTrades={snapshot.open_trades} />}
          {snapshot && <PairCalibrationPanel calibration={snapshot.calibration} />}
        </div>
      </div>
    </div>
  );
}
