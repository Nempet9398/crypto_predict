import React from "react";

function pctFmt(v) {
  if (v == null) return "—";
  const n = Number(v) * 100;
  return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
}

function retFmt(v, digits = 3) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v) * 100;
  return (n >= 0 ? "+" : "") + n.toFixed(digits) + "%";
}

// 분위수 신뢰구간 시각화 바
function CIBar({ lower, median, upper }) {
  if (lower == null || upper == null) return null;
  const lo = Number(lower) * 100;
  const mid = Number(median) * 100;
  const hi = Number(upper) * 100;
  const total = Math.max(Math.abs(lo), Math.abs(hi)) * 2 || 1;
  const zeroPos = 50;
  const loPos = zeroPos + (lo / total) * 50;
  const hiPos = zeroPos + (hi / total) * 50;
  const midPos = zeroPos + (mid / total) * 50;
  const barLeft = Math.min(loPos, hiPos);
  const barWidth = Math.abs(hiPos - loPos);
  const isPos = mid >= 0;
  return (
    <div className="ci-bar-wrap" title={`CI: [${lo.toFixed(3)}%, ${hi.toFixed(3)}%]`}>
      <div className="ci-bar-track">
        <div className={"ci-bar-fill " + (isPos ? "ci-pos" : "ci-neg")}
          style={{ left: barLeft + "%", width: Math.max(barWidth, 1) + "%" }} />
        <div className="ci-bar-mid" style={{ left: midPos + "%" }} />
        <div className="ci-bar-zero" style={{ left: zeroPos + "%" }} />
      </div>
      <div className="ci-labels">
        <span className="ci-bound">{lo.toFixed(2)}%</span>
        <span className={isPos ? "ret-pos" : "ret-neg"}>{retFmt(median)}</span>
        <span className="ci-bound">{hi.toFixed(2)}%</span>
      </div>
    </div>
  );
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
  const hasReg = !!signal.ml_reg_available;
  const ciWidth = Number(signal.ml_conf_width_1h || 0);

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

      {/* ML 회귀 예측 (수익률 + 신뢰구간) */}
      {hasReg && (
        <div className="sub-section">
          <div className="sub-title">
            ML 회귀 예측
            <span className="sub-tag">XGB/LGB</span>
          </div>
          <div className="reg-row">
            <span className="reg-label">1h 예상 수익률</span>
            <span className={Number(signal.ml_return_1h) >= 0 ? "ret-pos" : "ret-neg"}>
              {retFmt(signal.ml_return_1h)}
            </span>
          </div>
          <CIBar lower={signal.ml_lower_1h} median={signal.ml_return_1h} upper={signal.ml_upper_1h} />
          <div className="reg-row">
            <span className="reg-label">CI 폭 (10~90%)</span>
            <span className={
              ciWidth < 0.005 ? "ci-tight" : ciWidth > 0.02 ? "ci-wide" : "ci-medium"
            }>
              {pctFmt(ciWidth)}
              <span className="ci-hint">
                {ciWidth < 0.005 ? " 좁음" : ciWidth > 0.02 ? " 넓음" : " 보통"}
              </span>
            </span>
          </div>
          <div className="reg-row">
            <span className="reg-label">3h 예상</span>
            <span className={Number(signal.ml_return_3h) >= 0 ? "ret-pos" : "ret-neg"}>
              {retFmt(signal.ml_return_3h)}
            </span>
          </div>
          <div className="reg-row">
            <span className="reg-label">6h 예상</span>
            <span className={Number(signal.ml_return_6h) >= 0 ? "ret-pos" : "ret-neg"}>
              {retFmt(signal.ml_return_6h)}
            </span>
          </div>
        </div>
      )}

      {/* ML 분류 확률 */}
      {signal.ml_available && (
        <div className="sub-section">
          <div className="sub-title">ML 방향 확률</div>
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
      {!signal.ml_available && !hasReg && (
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
