import React from "react";

const TIMEFRAMES = ["15m", "30m", "1h", "6h", "24h"];

export default function TopBar({
  symbol,
  timeframe,
  models,
  activeModelId,
  autoRefresh,
  loading,
  training,
  onSymbolChange,
  onTimeframeChange,
  onOpenIndicators,
  onModelChange,
  onRefresh,
  onTrain,
  onAutoRefreshChange,
}) {
  return (
    <header className="top-bar">
      <div className="top-left">
        <div className="control">
          <label>Symbol</label>
          <select value={symbol} onChange={(event) => onSymbolChange(event.target.value)}>
            <option value="ETH/USDT">ETH/USDT</option>
            <option value="BTC/USDT">BTC/USDT</option>
          </select>
        </div>

        <div className="control timeframe-control">
          <label>Timeframe</label>
          <div className="timeframe-pills">
            {TIMEFRAMES.map((item) => (
              <button key={item} className={item === timeframe ? "pill active" : "pill"} onClick={() => onTimeframeChange(item)}>
                {item}
              </button>
            ))}
          </div>
        </div>

        <div className="control">
          <label>Model</label>
          <select value={activeModelId || ""} onChange={(event) => onModelChange(event.target.value || null)}>
            <option value="">Auto(active)</option>
            {(models || []).map((model) => (
              <option key={model.id} value={model.id}>
                {model.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="top-right">
        <button onClick={onOpenIndicators}>Indicators</button>
        <label className="toggle">
          <input type="checkbox" checked={autoRefresh} onChange={(event) => onAutoRefreshChange(event.target.checked)} />
          <span>Auto refresh</span>
        </label>
        <button onClick={onRefresh} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh"}
        </button>
        <button className="primary" onClick={onTrain} disabled={training}>
          {training ? "Training..." : "Train"}
        </button>
      </div>
    </header>
  );
}
