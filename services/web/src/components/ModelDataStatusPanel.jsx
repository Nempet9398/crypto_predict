import React from "react";

function badge(ok) {
  return ok
    ? <span className="status-dot green" />
    : <span className="status-dot red" />;
}

export default function ModelDataStatusPanel({ dataStatus = null, mlStatus = null }) {
  return (
    <div className="panel status-panel">
      <div className="panel-header">
        <span className="panel-title">시스템 상태</span>
      </div>

      <div className="status-section">
        <div className="status-row">
          <span className="status-label">데이터 수집</span>
          {badge(dataStatus?.row_count > 0)}
          <span className="status-val">
            {dataStatus?.row_count
              ? Number(dataStatus.row_count).toLocaleString() + " 봉"
              : "—"}
          </span>
        </div>
        {dataStatus?.max_ts && (
          <div className="status-row">
            <span className="status-label">최신 캔들</span>
            <span className="status-val">
              {new Date(dataStatus.max_ts).toLocaleString("ko-KR", {
                month: "2-digit", day: "2-digit",
                hour: "2-digit", minute: "2-digit",
              })}
            </span>
          </div>
        )}
        {dataStatus?.gap_count > 0 && (
          <div className="status-row warn">
            <span className="status-label">갭 감지</span>
            <span className="status-val">{dataStatus.gap_count}개 구간</span>
          </div>
        )}
      </div>

      <div className="status-section">
        <div className="status-row">
          <span className="status-label">ML 모델</span>
          {badge(mlStatus?.trained)}
          <span className="status-val">
            {mlStatus?.trained ? mlStatus.model_type?.toUpperCase() || "준비됨" : "미학습"}
          </span>
        </div>
        {mlStatus?.trained && (
          <>
            <div className="status-row">
              <span className="status-label">학습 정확도</span>
              <span className="status-val">
                {mlStatus.train_accuracy
                  ? Math.round(mlStatus.train_accuracy * 100) + "%"
                  : "—"}
              </span>
            </div>
            <div className="status-row">
              <span className="status-label">학습 시점</span>
              <span className="status-val">
                {mlStatus.trained_at
                  ? new Date(mlStatus.trained_at).toLocaleString("ko-KR", {
                      month: "2-digit", day: "2-digit",
                      hour: "2-digit", minute: "2-digit",
                    })
                  : "—"}
              </span>
            </div>
            <div className="status-row">
              <span className="status-label">CV F1 스코어</span>
              <span className="status-val">
                {mlStatus.best_cv_f1 != null ? mlStatus.best_cv_f1 : "—"}
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
