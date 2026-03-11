import React from "react";

function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return (Number(value) * 100).toFixed(1) + "%";
}

function MtfBadge({ value }) {
  if (value === 1) return <span className="mtf-badge bullish">▲ Bull</span>;
  if (value === -1) return <span className="mtf-badge bearish">▼ Bear</span>;
  return <span className="mtf-badge neutral">— Neutral</span>;
}

function VolRegimeBadge({ regime }) {
  const cls = regime === "low" ? "vol-low" : regime === "high" ? "vol-high" : "vol-medium";
  const label = regime === "low" ? "Low Vol" : regime === "high" ? "High Vol" : "Mid Vol";
  return <span className={`vol-badge ${cls}`}>{label}</span>;
}

function DirBadge({ direction }) {
  if (direction === 1) return <span className="dir-badge up">▲ Up</span>;
  if (direction === -1) return <span className="dir-badge down">▼ Down</span>;
  return <span className="dir-badge neutral">— Neutral</span>;
}

export default function SignalPanel({ signal, ensemble, selectedHorizon, modelStatus }) {
  const modelMeta = modelStatus?.metadata;
  const backtestMae = modelMeta?.backtest?.mae;

  // Prefer ensemble signal if available
  const active = ensemble || signal;
  const isEnsemble = !!ensemble;

  return (
    <aside className="signal-panel">
      <h3>Signal {isEnsemble && <span className="ensemble-tag">Ensemble</span>}</h3>

      {/* Main signal badge */}
      <div className={`signal-badge ${active?.signal || "no-trade"}`}>
        {active?.signal || "no-trade"}
      </div>

      {/* Score & confidence */}
      {isEnsemble && (
        <>
          <div className="signal-row">
            <span>Score</span>
            <strong>{fmt(ensemble?.score, 3)}</strong>
          </div>
          <div className="signal-row">
            <span>Confidence</span>
            <strong>{pct(ensemble?.confidence)}</strong>
          </div>
        </>
      )}

      {/* Horizon */}
      <div className="signal-row"><span>Horizon</span><strong>{selectedHorizon}h</strong></div>

      {/* ARIMA component */}
      <div className="signal-section-header">ARIMA</div>
      <div className="signal-row"><span>Prob Up</span><strong>{pct(active?.prob_up ?? active?.arima_prob_up)}</strong></div>
      <div className="signal-row"><span>Prob Down</span><strong>{pct(active?.prob_down ?? active?.arima_prob_down)}</strong></div>

      {/* ML component (ensemble only) */}
      {isEnsemble && ensemble?.ml_available && (
        <>
          <div className="signal-section-header">ML Model</div>
          <div className="signal-row">
            <span>ML Direction</span>
            <strong><DirBadge direction={ensemble?.ml_direction} /></strong>
          </div>
          <div className="signal-row"><span>ML Prob Up</span><strong>{pct(ensemble?.ml_prob_up)}</strong></div>
          <div className="signal-row"><span>ML Prob Down</span><strong>{pct(ensemble?.ml_prob_down)}</strong></div>
        </>
      )}

      {/* Multi-timeframe */}
      {isEnsemble && (
        <>
          <div className="signal-section-header">Multi-TF</div>
          <div className="signal-row">
            <span>1h Signal</span>
            <strong><MtfBadge value={ensemble?.mtf_1h} /></strong>
          </div>
          <div className="signal-row">
            <span>4h Signal</span>
            <strong><MtfBadge value={ensemble?.mtf_4h} /></strong>
          </div>
          <div className="signal-row">
            <span>Vol Regime</span>
            <strong><VolRegimeBadge regime={ensemble?.vol_regime} /></strong>
          </div>
        </>
      )}

      {/* TP / SL */}
      <div className="signal-section-header">TP / SL</div>
      {isEnsemble ? (
        <>
          <div className="signal-row"><span>Entry</span><strong>{fmt(ensemble?.current_close, 2)}</strong></div>
          <div className="signal-row"><span>TP (ATR ×{fmt(2.0,1)})</span><strong className="tp">{ensemble?.tp_price ? fmt(ensemble.tp_price, 2) : "-"}</strong></div>
          <div className="signal-row"><span>SL (ATR ×{fmt(1.0,1)})</span><strong className="sl">{ensemble?.sl_price ? fmt(ensemble.sl_price, 2) : "-"}</strong></div>
          <div className="signal-row"><span>RR Ratio</span><strong>{fmt(ensemble?.rr_ratio, 1)}</strong></div>
          <div className="signal-row"><span>ATR (14)</span><strong>{fmt(ensemble?.atr_14, 2)}</strong></div>
        </>
      ) : (
        <>
          <div className="signal-row"><span>Entry</span><strong>{fmt(signal?.current_close, 2)}</strong></div>
          <div className="signal-row"><span>TP</span><strong className="tp">{fmt(signal?.recommended_tp, 2)}</strong></div>
          <div className="signal-row"><span>SL</span><strong className="sl">{fmt(signal?.recommended_sl, 2)}</strong></div>
        </>
      )}

      {/* Position sizing */}
      {isEnsemble && (
        <>
          <div className="signal-section-header">Position Sizing</div>
          <div className="signal-row">
            <span>Size (Half-Kelly)</span>
            <strong>{fmt(ensemble?.position_size_pct, 1)}%</strong>
          </div>
        </>
      )}

      {/* Model meta */}
      <div className="signal-section-header">Model</div>
      <div className="signal-row"><span>Backtest MAE</span><strong>{fmt(backtestMae, 4)}</strong></div>
      <div className="signal-row"><span>Trained At</span><strong style={{ fontSize: "0.75rem" }}>{modelMeta?.trained_at || "-"}</strong></div>
      {!isEnsemble && (
        <div className="signal-row"><span>Confidence</span><strong>{signal?.confidence_level || "-"}</strong></div>
      )}
    </aside>
  );
}
