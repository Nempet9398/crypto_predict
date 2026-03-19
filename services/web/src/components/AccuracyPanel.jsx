import React, { useState, useEffect, useCallback } from "react";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const HORIZONS = [
  { label: "1H (4봉)", value: 4 },
  { label: "4H (16봉)", value: 16 },
  { label: "8H (32봉)", value: 32 },
];

export default function AccuracyPanel() {
  const [horizon, setHorizon] = useState(4);
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const fetchAccuracy = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/ml/accuracy?days=${days}&horizon_bars=${horizon}`);
      const json = await res.json();
      setData(json);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [days, horizon]);

  useEffect(() => { fetchAccuracy(); }, [fetchAccuracy]);

  const acc = data?.accuracy;
  const longAcc = data?.long_accuracy;
  const shortAcc = data?.short_accuracy;
  const total = data?.total;

  return (
    <div className="panel accuracy-panel">
      <div className="panel-header">
        <span className="panel-title">예측 정확도</span>
      </div>

      <div className="accuracy-controls">
        <div className="acc-control-row">
          <span className="acc-label">예측 범위</span>
          <div className="horizon-btns">
            {HORIZONS.map((h) => (
              <button
                key={h.value}
                className={"horizon-btn" + (horizon === h.value ? " active" : "")}
                onClick={() => setHorizon(h.value)}
              >
                {h.label}
              </button>
            ))}
          </div>
        </div>
        <div className="acc-control-row">
          <span className="acc-label">기간</span>
          <div className="days-btns">
            {[7, 14, 30, 60, 90].map((d) => (
              <button
                key={d}
                className={"days-btn" + (days === d ? " active" : "")}
                onClick={() => setDays(d)}
              >
                {d}일
              </button>
            ))}
          </div>
        </div>
      </div>

      {loading && <div className="panel-empty">계산 중...</div>}

      {!loading && data && total === 0 && (
        <div className="panel-empty acc-empty">
          <div>아직 검증된 예측 데이터가 없습니다.</div>
          <div className="acc-hint">ML 학습 후 일정 시간 경과 시 정확도가 표시됩니다.</div>
        </div>
      )}

      {!loading && data && total > 0 && (
        <div className="accuracy-results">
          {/* 수익률 KPI (Primary) */}
          {data.avg_return_per_trade != null && (
            <div className="acc-kpi-section">
              <div className="acc-kpi-title">수익률 성과 (Primary KPI)</div>
              <div className="acc-kpi-row">
                <span className="acc-kpi-label">거래당 평균 수익률</span>
                <span className={`acc-kpi-val ${data.avg_return_per_trade >= 0 ? "kpi-pos" : "kpi-neg"}`}>
                  {data.avg_return_per_trade >= 0 ? "+" : ""}{(data.avg_return_per_trade * 100).toFixed(3)}%
                </span>
              </div>
              {data.win_rate != null && (
                <div className="acc-kpi-row">
                  <span className="acc-kpi-label">수익 승률 (>0.3%)</span>
                  <span className={`acc-kpi-val ${data.win_rate >= 0.5 ? "kpi-pos" : "kpi-neg"}`}>
                    {Math.round(data.win_rate * 100)}%
                    <span className="kpi-sub"> ({data.profit_trades}/{data.directional_total})</span>
                  </span>
                </div>
              )}
            </div>
          )}
          {/* 방향 정확도 */}
          <div className="acc-main">
            <div className="acc-circle">
              <span className="acc-pct">{acc != null ? Math.round(acc * 100) + "%" : "—"}</span>
              <span className="acc-sub">방향 정확도</span>
            </div>
            <div className="acc-details">
              <div className="acc-detail-row">
                <span className="acc-detail-label">총 예측 수</span>
                <span className="acc-detail-val">{total}건</span>
              </div>
              <div className="acc-detail-row up">
                <span className="acc-detail-label">▲ LONG 정확도</span>
                <span className="acc-detail-val">
                  {longAcc != null ? Math.round(longAcc * 100) + "%" : "—"}
                </span>
              </div>
              <div className="acc-detail-row down">
                <span className="acc-detail-label">▼ SHORT 정확도</span>
                <span className="acc-detail-val">
                  {shortAcc != null ? Math.round(shortAcc * 100) + "%" : "—"}
                </span>
              </div>
              {data.mae != null && (
                <div className="acc-detail-row">
                  <span className="acc-detail-label">MAE</span>
                  <span className="acc-detail-val">{(data.mae * 100).toFixed(4)}%</span>
                </div>
              )}
            </div>
          </div>
          {data.from_ts && data.to_ts && (
            <div className="acc-period">
              {new Date(data.from_ts).toLocaleDateString("ko-KR")}
              {" ~ "}
              {new Date(data.to_ts).toLocaleDateString("ko-KR")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
