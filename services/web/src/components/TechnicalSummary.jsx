import React from "react";

function fmt(v, digits = 2) {
  if (v == null) return "—";
  return Number(v).toFixed(digits);
}

function rsiColor(rsi) {
  if (!rsi) return "";
  return rsi > 70 ? "down" : rsi < 30 ? "up" : "";
}

function macdColor(hist) {
  if (!hist) return "";
  return Number(hist) > 0 ? "up" : "down";
}

export default function TechnicalSummary({ features = null }) {
  if (!features) {
    return (
      <div className="panel tech-panel">
        <div className="panel-header"><span className="panel-title">기술 지표</span></div>
        <div className="panel-empty">—</div>
      </div>
    );
  }

  const rows = [
    { label: "RSI (14)", value: fmt(features.rsi_14), cls: rsiColor(features.rsi_14), unit: "" },
    { label: "MACD", value: fmt(features.macd_hist, 4), cls: macdColor(features.macd_hist), unit: "" },
    { label: "BB %B", value: fmt(features.bb_pct, 3), cls: "", unit: "" },
    { label: "ATR (14)", value: fmt(features.atr_14), cls: "", unit: "$" },
    { label: "Stoch %K", value: fmt(features.stoch_k), cls: "", unit: "" },
    { label: "Stoch %D", value: fmt(features.stoch_d), cls: "", unit: "" },
    { label: "OBV", value: features.obv ? Number(features.obv).toLocaleString(undefined, {notation: "compact"}) : "—", cls: "", unit: "" },
    { label: "VWAP", value: fmt(features.vwap), cls: "", unit: "$" },
    { label: "EMA 20", value: fmt(features.ema_20), cls: "", unit: "$" },
    { label: "EMA 50", value: fmt(features.ema_50), cls: "", unit: "$" },
    { label: "변동성(10)", value: features.volatility_10 ? (Number(features.volatility_10) * 100).toFixed(3) + "%" : "—", cls: "", unit: "" },
  ];

  return (
    <div className="panel tech-panel">
      <div className="panel-header">
        <span className="panel-title">기술 지표</span>
        {features.ts && (
          <span className="panel-update">
            {new Date(features.ts).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}
          </span>
        )}
      </div>
      <table className="tech-table">
        <tbody>
          {rows.map(({ label, value, cls, unit }) => (
            <tr key={label}>
              <td className="tech-label">{label}</td>
              <td className={"tech-value " + cls}>{unit}{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
