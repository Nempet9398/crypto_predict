import React from "react";

const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"];
const SYMBOLS = ["ETH/USDT", "BTC/USDT", "SOL/USDT"];

export default function TopBar({
  symbol,
  timeframe,
  autoRefresh,
  loading,
  mlStatus,
  onSymbolChange,
  onTimeframeChange,
  onAutoRefreshChange,
  onRefresh,
  onOpenIndicators,
  onOpenTrainingPanel,
  lastUpdate,
}) {
  return (
    <header className="topbar">
      <div className="topbar-logo">
        <span className="logo-icon">◈</span>
        <span className="logo-text">ETH Dashboard</span>
      </div>

      <div className="topbar-group">
        <select
          className="topbar-select symbol-select"
          value={symbol}
          onChange={(e) => onSymbolChange?.(e.target.value)}
        >
          {SYMBOLS.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      <div className="topbar-group tf-group">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            className={"tf-btn" + (timeframe === tf ? " active" : "")}
            onClick={() => onTimeframeChange?.(tf)}
          >
            {tf}
          </button>
        ))}
      </div>

      <div className="topbar-status">
        {loading && (
          <span className="status-badge loading">
            <span className="spinner" /> 로딩 중
          </span>
        )}
        {mlStatus?.trained === true && (
          <span className="status-badge ml-ready">ML 준비됨</span>
        )}
        {mlStatus?.trained === false && (
          <span className="status-badge ml-none">ML 미학습</span>
        )}
        {lastUpdate && (
          <span className="status-badge update-time">
            {new Date(lastUpdate).toLocaleTimeString("ko-KR", {
              hour: "2-digit", minute: "2-digit", second: "2-digit",
            })}
          </span>
        )}
      </div>

      <div className="topbar-actions">
        <button className="topbar-btn" onClick={onOpenIndicators} title="지표 설정">
          ≡ 지표
        </button>
        <button
          className="topbar-btn"
          onClick={onOpenTrainingPanel}
          title="ML 학습 설정"
        >
          ⚙ ML 학습
        </button>
        <button
          className="topbar-btn icon-btn"
          onClick={onRefresh}
          disabled={loading}
          title="새로고침"
        >
          ↻
        </button>
        <label className="auto-refresh-toggle" title="자동 새로고침 15초">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => onAutoRefreshChange?.(e.target.checked)}
          />
          <span className="toggle-track">
            <span className="toggle-thumb" />
          </span>
          <span className="toggle-label">AUTO</span>
        </label>
      </div>
    </header>
  );
}
