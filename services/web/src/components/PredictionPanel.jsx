import React from "react";

function pctFmt(v) {
  if (v == null) return "—";
  const n = Number(v) * 100;
  return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
}

function probBar(prob, color) {
  const w = Math.round((prob || 0) * 100);
  return (
    <div className="prob-bar-wrap">
      <div className="prob-bar-fill" style={{ width: w + "%", background: color }} />
      <span className="prob-bar-label">{w}%</span>
    </div>
  );
}

export default function PredictionPanel({ signal = null }) {
  if (!signal) {
    return (
      <div className="panel prediction-panel">
        <div className="panel-header">
          <span className="panel-title">신호 / 예측</span>
        </div>
        <div className="panel-empty">데이터 로딩 중...</div>
      </div>
    );
  }

  const dir = signal.signal;
  const dirClass = dir === "long" ? "long" : dir === "short" ? "short" : "neutral";
  const dirLabel = dir === "long" ? "▲ LONG" : dir === "short" ? "▼ SHORT" : "— NO TRADE";

  const score = Number(signal.score || 0);
  const confidence = Number(signal.confidence || 0);
  const mlUp = Number(signal.ml_prob_up || 0);
  const mlDown = Number(signal.ml_prob_down || 0);
  const techScore = Number(signal.tech_score || 0);
  const mtf1h = signal.mtf_1h;
  const mtf4h = signal.mtf_4h;

  return (
    <div className="panel prediction-panel">
      <div className="panel-header">
        <span className="panel-title">신호 / 예측</span>
        <span className="panel-update">
          {signal.computed_at
            ? new Date(signal.computed_at).toLocaleTimeString("ko-KR")
            : ""}
        </span>
      </div>

      {/* 메인 신호 */}
      <div className={"signal-main " + dirClass}>
        <span className="signal-label">{dirLabel}</span>
        <span className="signal-score">
          Score: {score >= 0 ? "+" : ""}{score.toFixed(3)}
        </span>
      </div>

      {/* Confidence 바 */}
      <div className="confidence-row">
        <span className="conf-label">신뢰도</span>
        <div className="conf-bar-wrap">
          <div
            className="conf-bar-fill"
            style={{
              width: Math.round(confidence * 100) + "%",
              background: confidence > 0.6 ? "#10b981" : confidence > 0.3 ? "#f59e0b" : "#6b7280",
            }}
          />
        </div>
        <span className="conf-value">{Math.round(confidence * 100)}%</span>
      </div>

      {/* ML 확률 */}
      {signal.ml_available && (
        <div className="sub-section">
          <div className="sub-title">ML 예측</div>
          <div className="prob-row">
            <span className="prob-label">상승</span>
            {probBar(mlUp, "#10b981")}
          </div>
          <div className="prob-row">
            <span className="prob-label">하락</span>
            {probBar(mlDown, "#ef4444")}
          </div>
        </div>
      )}
      {!signal.ml_available && (
        <div className="sub-section">
          <div className="sub-title ml-warn">ML 모델 미학습 (기술지표만 사용)</div>
        </div>
      )}

      {/* 기술지표 스코어 */}
      <div className="sub-section">
        <div className="sub-title">기술지표 스코어</div>
        <div className="tech-score-bar">
          <div
            className="tech-bar-fill"
            style={{
              width: Math.abs(techScore) * 100 + "%",
              marginLeft: techScore < 0 ? "auto" : "0",
              background: techScore > 0 ? "#10b981" : "#ef4444",
            }}
          />
        </div>
        <span className="tech-score-val">
          {techScore >= 0 ? "+" : ""}{techScore.toFixed(3)}
        </span>
      </div>

      {/* MTF */}
      <div className="sub-section">
        <div className="sub-title">멀티타임프레임</div>
        <div className="mtf-row">
          <span className="mtf-label">1H</span>
          <span className={"mtf-badge " + (mtf1h > 0 ? "up" : mtf1h < 0 ? "down" : "neutral")}>
            {mtf1h > 0 ? "▲ 강세" : mtf1h < 0 ? "▼ 약세" : "— 중립"}
          </span>
          <span className="mtf-label">4H</span>
          <span className={"mtf-badge " + (mtf4h > 0 ? "up" : mtf4h < 0 ? "down" : "neutral")}>
            {mtf4h > 0 ? "▲ 강세" : mtf4h < 0 ? "▼ 약세" : "— 중립"}
          </span>
        </div>
      </div>

      {/* 변동성 레짐 */}
      <div className="sub-section">
        <div className="vol-regime-row">
          <span className="sub-title">변동성 레짐</span>
          <span className={"regime-badge " + (signal.vol_regime || "unknown")}>
            {signal.vol_regime?.toUpperCase() || "—"}
          </span>
        </div>
      </div>
    </div>
  );
}
