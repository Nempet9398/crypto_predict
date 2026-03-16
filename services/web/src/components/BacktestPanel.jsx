import React, { useCallback, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return (Number(value) * 100).toFixed(1) + "%";
}

function MetricCard({ label, value, colorClass, hint }) {
  return (
    <div className={`bt-metric-card ${colorClass || ""}`} title={hint}>
      <span className="bt-metric-label">{label}</span>
      <span className="bt-metric-value">{value}</span>
    </div>
  );
}

function colorForSharpe(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return "";
  if (n >= 1.5) return "metric-great";
  if (n >= 0.5) return "metric-ok";
  if (n >= 0) return "metric-neutral";
  return "metric-bad";
}

function colorForWinRate(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return "";
  if (n >= 0.55) return "metric-great";
  if (n >= 0.45) return "metric-ok";
  return "metric-bad";
}

function colorForPnl(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return "";
  return n >= 0 ? "metric-great" : "metric-bad";
}

// Mini sparkline using SVG for cumulative PnL
function PnlSparkline({ data }) {
  if (!data || data.length < 2) return null;
  const values = data.map((d) => d.cum_pnl);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 0.001;
  const W = 320;
  const H = 60;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * W;
    const y = H - ((v - min) / range) * H;
    return `${x},${y}`;
  });
  const path = `M ${pts.join(" L ")}`;
  const fillPts = `M 0,${H} L ${pts.join(" L ")} L ${W},${H} Z`;
  const finalVal = values[values.length - 1];
  const isPositive = finalVal >= 0;

  return (
    <div className="bt-sparkline-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="bt-sparkline">
        <defs>
          <linearGradient id="sparkGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={isPositive ? "#22d3ee" : "#f87171"} stopOpacity="0.25" />
            <stop offset="100%" stopColor={isPositive ? "#22d3ee" : "#f87171"} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <path d={fillPts} fill="url(#sparkGrad)" />
        <path d={path} fill="none" stroke={isPositive ? "#22d3ee" : "#f87171"} strokeWidth="1.5" />
        {/* Zero line */}
        {min < 0 && max > 0 && (
          <line
            x1="0" y1={H - ((-min) / range) * H}
            x2={W} y2={H - ((-min) / range) * H}
            stroke="rgba(148,167,202,0.3)" strokeWidth="1" strokeDasharray="4,3"
          />
        )}
      </svg>
      <div className="bt-sparkline-labels">
        <span>PnL</span>
        <span className={isPositive ? "ret-pos" : "ret-neg"}>
          {isPositive ? "+" : ""}{(finalVal * 100).toFixed(1)}%
        </span>
      </div>
    </div>
  );
}

export default function BacktestPanel({ timeframe }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lookbackDays, setLookbackDays] = useState(14);
  const [horizonHours, setHorizonHours] = useState(6);

  const runBacktest = useCallback(async () => {
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const params = new URLSearchParams({
        lookback_days: lookbackDays,
        horizon_hours: horizonHours,
        up_prob_threshold: "0.6",
        down_prob_threshold: "0.6",
        up_pct: "1.0",
        down_pct: "1.0",
        fee_bps: "4",
        slippage_bps: "2",
        tz: "Asia/Seoul",
      });
      if (timeframe) params.set("timeframe", timeframe);
      const res = await fetch(`${API_URL}/backtest/strategy?${params.toString()}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
      setResult(await res.json());
    } catch (err) {
      setError(err.message || "Backtest failed");
    } finally {
      setLoading(false);
    }
  }, [lookbackDays, horizonHours, timeframe]);

  const s = result?.summary;

  return (
    <div className="backtest-panel">
      <div className="bt-header">
        <h3>Backtest</h3>
        <span className="section-tag">ARIMA Walk-Forward</span>
      </div>

      {/* Controls */}
      <div className="bt-controls">
        <label className="bt-ctrl">
          <span>Lookback</span>
          <select value={lookbackDays} onChange={(e) => setLookbackDays(Number(e.target.value))}>
            {[7, 14, 30, 60].map((d) => <option key={d} value={d}>{d}d</option>)}
          </select>
        </label>
        <label className="bt-ctrl">
          <span>Horizon</span>
          <select value={horizonHours} onChange={(e) => setHorizonHours(Number(e.target.value))}>
            {[1, 3, 6, 12, 24].map((h) => <option key={h} value={h}>{h}h</option>)}
          </select>
        </label>
        <button
          className="bt-run-btn"
          onClick={runBacktest}
          disabled={loading}
        >
          {loading ? "Running…" : "▶ Run"}
        </button>
      </div>

      {error && <div className="bt-error">{error}</div>}

      {!result && !loading && !error && (
        <div className="bt-empty">Run a backtest to see results</div>
      )}

      {loading && (
        <div className="bt-empty bt-loading">
          <span className="bt-spinner" />
          Running walk-forward backtest…
        </div>
      )}

      {s && (
        <>
          {/* PnL sparkline */}
          <PnlSparkline data={result.pnl_series} />

          {/* Metrics grid */}
          <div className="bt-metrics-grid">
            <MetricCard label="Sharpe" value={fmt(s.sharpe, 2)}
              colorClass={colorForSharpe(s.sharpe)}
              hint="Annualized Sharpe ratio. >1.5 = excellent" />
            <MetricCard label="Sortino" value={fmt(s.sortino, 2)}
              colorClass={colorForSharpe(s.sortino)}
              hint="Like Sharpe but only penalizes downside deviation" />
            <MetricCard label="Calmar" value={fmt(s.calmar, 2)}
              colorClass={colorForSharpe(s.calmar)}
              hint="Return / Max Drawdown ratio" />
            <MetricCard label="Profit Factor" value={fmt(s.profit_factor, 2)}
              colorClass={s.profit_factor >= 1.3 ? "metric-great" : s.profit_factor >= 1.0 ? "metric-ok" : "metric-bad"}
              hint="Gross profit / Gross loss. >1.3 = good" />
            <MetricCard label="Win Rate" value={pct(s.win_rate)}
              colorClass={colorForWinRate(s.win_rate)}
              hint="% of winning trades" />
            <MetricCard label="Payoff" value={fmt(s.payoff_ratio, 2)}
              colorClass={s.payoff_ratio >= 1.2 ? "metric-great" : "metric-neutral"}
              hint="Avg win / Avg loss" />
            <MetricCard label="Cum PnL" value={(Number(s.cumulative_pnl) * 100).toFixed(1) + "%"}
              colorClass={colorForPnl(s.cumulative_pnl)}
              hint="Total cumulative return" />
            <MetricCard label="Max DD" value={(Math.abs(Number(s.mdd)) * 100).toFixed(1) + "%"}
              colorClass={Math.abs(Number(s.mdd)) < 0.05 ? "metric-great" : Math.abs(Number(s.mdd)) < 0.15 ? "metric-ok" : "metric-bad"}
              hint="Maximum drawdown (lower = better)" />
            <MetricCard label="MDD Bars" value={s.mdd_duration_bars || "-"}
              colorClass="metric-neutral"
              hint="Max drawdown duration in bars" />
            <MetricCard label="# Trades" value={s.n_trades || "-"}
              colorClass="metric-neutral"
              hint="Number of trades executed" />
          </div>

          <div className="bt-footer">
            {lookbackDays}d lookback · {horizonHours}h horizon · {result.timeframe}
          </div>
        </>
      )}
    </div>
  );
}
