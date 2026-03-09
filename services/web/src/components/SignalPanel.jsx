import React from "react";

function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

export default function SignalPanel({ signal, selectedHorizon, modelStatus }) {
  const modelMeta = modelStatus?.metadata;
  const backtestMae = modelMeta?.backtest?.mae;

  return (
    <aside className="signal-panel">
      <h3>Signal</h3>
      <div className={`signal-badge ${signal?.signal || "no-trade"}`}>{signal?.signal || "no-trade"}</div>
      <div className="signal-row"><span>Horizon</span><strong>{selectedHorizon}h</strong></div>
      <div className="signal-row"><span>Prob Up</span><strong>{fmt(signal?.prob_up * 100, 2)}%</strong></div>
      <div className="signal-row"><span>Prob Down</span><strong>{fmt(signal?.prob_down * 100, 2)}%</strong></div>
      <div className="signal-row"><span>Expected</span><strong>{fmt(signal?.current_close, 2)}</strong></div>
      <div className="signal-row"><span>Pred Low</span><strong>{fmt(signal?.recommended_sl, 2)}</strong></div>
      <div className="signal-row"><span>Pred High</span><strong>{fmt(signal?.recommended_tp, 2)}</strong></div>
      <div className="signal-row"><span>Backtest MAE</span><strong>{fmt(backtestMae, 4)}</strong></div>
      <div className="signal-row"><span>Model Trained</span><strong>{modelMeta?.trained_at || "-"}</strong></div>
      <div className="signal-row"><span>Confidence</span><strong>{signal?.confidence_level || "-"}</strong></div>
    </aside>
  );
}
