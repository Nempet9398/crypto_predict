/**
 * TrainingPanel — ML 학습 파라미터 설정 + 진행률 모달
 *
 * 기능:
 *  - 학습 파라미터 설정 (기본값 제공)
 *  - 학습 시작 후 2초마다 /ml/train-status 폴링 → 진행률 % 표시
 *  - 단계별 상태 로그 실시간 출력
 */
import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "../api";

const DEFAULTS = {
  lookback_days:    60,
  horizon_bars:     4,
  n_trials:         40,
  outer_cv_splits:  5,
  timeout_sec:      600,
  train_regressor:  true,
  quality_gate:     0.50,
};

const HORIZON_OPTIONS = [
  { value: 4,  label: "1시간 (4봉)" },
  { value: 12, label: "3시간 (12봉)" },
  { value: 24, label: "6시간 (24봉)" },
  { value: 16, label: "4시간 (16봉)" },
  { value: 32, label: "8시간 (32봉)" },
];

function ParamRow({ label, hint, children }) {
  return (
    <div className="tp-param-row">
      <div className="tp-param-label">
        <span>{label}</span>
        {hint && <span className="tp-param-hint">{hint}</span>}
      </div>
      <div className="tp-param-input">{children}</div>
    </div>
  );
}

function ProgressBar({ value }) {
  const pct = Math.min(100, Math.max(0, value));
  const color = pct === 100 ? "#22c55e" : pct > 70 ? "#f59e0b" : "#6366f1";
  return (
    <div className="tp-progress-wrap">
      <div className="tp-progress-bar">
        <div
          className="tp-progress-fill"
          style={{ width: `${pct}%`, background: color, transition: "width 0.4s ease" }}
        />
      </div>
      <span className="tp-progress-pct">{pct}%</span>
    </div>
  );
}

export default function TrainingPanel({ open, onClose }) {
  const [params, setParams] = useState({ ...DEFAULTS });
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState("");
  const [logs, setLogs] = useState([]);
  const [result, setResult] = useState(null);  // {success, stdout, stderr, finished_at}
  const [error, setError] = useState("");
  const pollRef = useRef(null);
  const logEndRef = useRef(null);

  // 폴링 중지
  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // 학습 상태 폴링 (2초마다)
  const startPoll = useCallback(() => {
    stopPoll();
    pollRef.current = setInterval(async () => {
      try {
        const s = await api("/ml/train-status");
        setProgress(s.progress ?? 0);
        setStage(s.stage ?? "");
        setLogs(s.logs ?? []);
        if (!s.running) {
          stopPoll();
          setRunning(false);
          setResult(s.last_result);
        }
      } catch {}
    }, 2000);
  }, [stopPoll]);

  // 패널 닫힐 때 폴링 중지
  useEffect(() => {
    if (!open) stopPoll();
  }, [open, stopPoll]);

  // 로그 자동 스크롤
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const set = (key) => (e) => {
    const val = e.target.type === "checkbox" ? e.target.checked
      : e.target.type === "number" ? Number(e.target.value)
      : e.target.value;
    setParams((p) => ({ ...p, [key]: val }));
  };

  const handleStart = async () => {
    setError("");
    setResult(null);
    setLogs([]);
    setProgress(0);
    setStage("학습 요청 중...");
    setRunning(true);

    const qs = new URLSearchParams({
      lookback_days:   params.lookback_days,
      horizon_bars:    params.horizon_bars,
      n_trials:        params.n_trials,
      outer_cv_splits: params.outer_cv_splits,
      timeout_sec:     params.timeout_sec,
      train_regressor: params.train_regressor,
      quality_gate:    params.quality_gate,
    }).toString();

    try {
      const res = await api(`/ml/train?${qs}`, { method: "POST" });
      if (res.status === "already_running") {
        setStage("이미 학습 중...");
        startPoll();
        return;
      }
      setStage("학습 시작됨");
      startPoll();
    } catch (e) {
      setError(e.message || "학습 요청 실패");
      setRunning(false);
      setStage("");
    }
  };

  const handleReset = () => {
    setParams({ ...DEFAULTS });
    setResult(null);
    setLogs([]);
    setProgress(0);
    setStage("");
    setError("");
  };

  if (!open) return null;

  const elapsedMin = running ? null : null; // 추후 확장

  return (
    <div className="tp-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="tp-modal">
        {/* 헤더 */}
        <div className="tp-header">
          <div className="tp-title">
            <span className="tp-title-icon">⚙</span>
            ML 학습 설정
          </div>
          <button className="tp-close" onClick={onClose}>✕</button>
        </div>

        <div className="tp-body">
          {/* 파라미터 설정 */}
          <section className="tp-section">
            <div className="tp-section-title">학습 데이터</div>

            <ParamRow label="학습 기간" hint="최근 N일치 데이터 사용">
              <input
                type="number" min={7} max={730} step={1}
                value={params.lookback_days} onChange={set("lookback_days")}
                disabled={running}
              />
              <span className="tp-unit">일</span>
              <span className="tp-calc">≈ {(params.lookback_days * 96).toLocaleString()}봉</span>
            </ParamRow>

            <ParamRow label="예측 대상" hint="몇 봉 후 가격 방향 예측">
              <select value={params.horizon_bars} onChange={set("horizon_bars")} disabled={running}>
                {HORIZON_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </ParamRow>
          </section>

          <section className="tp-section">
            <div className="tp-section-title">AutoML 설정</div>

            <ParamRow label="Optuna 튜닝 횟수" hint="많을수록 정확하지만 느림">
              <input
                type="range" min={5} max={200} step={5}
                value={params.n_trials} onChange={set("n_trials")}
                disabled={running}
              />
              <span className="tp-range-val">{params.n_trials}회</span>
            </ParamRow>

            <ParamRow label="Walk-Forward CV" hint="시계열 교차검증 폴드 수">
              <input
                type="range" min={3} max={10} step={1}
                value={params.outer_cv_splits} onChange={set("outer_cv_splits")}
                disabled={running}
              />
              <span className="tp-range-val">{params.outer_cv_splits}-fold</span>
            </ParamRow>

            <ParamRow label="품질 게이트" hint="이 정확도 미달 모델은 자동 탈락">
              <input
                type="range" min={45} max={70} step={1}
                value={Math.round(params.quality_gate * 100)}
                onChange={(e) => setParams((p) => ({ ...p, quality_gate: Number(e.target.value) / 100 }))}
                disabled={running}
              />
              <span className="tp-range-val">{(params.quality_gate * 100).toFixed(0)}%</span>
            </ParamRow>
          </section>

          <section className="tp-section">
            <div className="tp-section-title">옵션</div>

            <ParamRow label="회귀 모델 함께 학습" hint="수익률 예측 모델 (1h/3h/6h) 포함">
              <label className="tp-toggle">
                <input type="checkbox" checked={params.train_regressor} onChange={set("train_regressor")} disabled={running} />
                <span className="tp-toggle-track"><span className="tp-toggle-thumb" /></span>
              </label>
            </ParamRow>

            <ParamRow label="최대 학습 시간" hint="초과 시 자동 종료">
              <select value={params.timeout_sec} onChange={set("timeout_sec")} disabled={running}>
                <option value={300}>5분</option>
                <option value={600}>10분 (기본)</option>
                <option value={1200}>20분</option>
                <option value={3600}>1시간</option>
                <option value={7200}>2시간</option>
              </select>
            </ParamRow>
          </section>

          {/* 예상 시간 안내 */}
          <div className="tp-estimate">
            예상 소요: Optuna {params.n_trials}회 × 2알고 + CV {params.outer_cv_splits}-fold
            {params.train_regressor ? " + 회귀 3 horizon" : ""}
            &nbsp;→ 약 <strong>{Math.round(params.n_trials * 0.15 * (params.train_regressor ? 1.8 : 1))}분</strong> 예상
          </div>

          {/* 에러 */}
          {error && <div className="tp-error">{error}</div>}

          {/* 진행률 */}
          {(running || progress > 0) && (
            <div className="tp-progress-section">
              <div className="tp-stage-label">
                {stage || "대기 중..."}
                {running && <span className="tp-spinner" />}
              </div>
              <ProgressBar value={progress} />
              {result && (
                <div className={`tp-result-badge ${result.success ? "success" : "fail"}`}>
                  {result.success ? "학습 완료 ✓" : "학습 실패 ✗"}
                  {result.finished_at && (
                    <span className="tp-result-time">
                      {new Date(result.finished_at).toLocaleTimeString("ko-KR")}
                    </span>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 실시간 로그 */}
          {(running || logs.length > 0) && (
            <div className="tp-logs-section">
              <div className="tp-logs-title">실시간 로그</div>
              <div className="tp-logs">
                {logs.map((line, i) => (
                  <div key={i} className="tp-log-line">{line}</div>
                ))}
                <div ref={logEndRef} />
              </div>
            </div>
          )}

          {/* 학습 완료 후 stdout 요약 */}
          {result && result.stdout && !running && (
            <details className="tp-stdout">
              <summary>상세 로그 보기</summary>
              <pre>{result.stdout}</pre>
            </details>
          )}
          {result && result.stderr && !running && (
            <details className="tp-stderr">
              <summary>에러 로그</summary>
              <pre>{result.stderr}</pre>
            </details>
          )}
        </div>

        {/* 푸터 */}
        <div className="tp-footer">
          <button className="tp-btn-reset" onClick={handleReset} disabled={running}>
            기본값 복원
          </button>
          <div className="tp-footer-right">
            <button className="tp-btn-cancel" onClick={onClose} disabled={running}>
              닫기
            </button>
            <button
              className={"tp-btn-start" + (running ? " is-running" : "")}
              onClick={handleStart}
              disabled={running}
            >
              {running ? (
                <>학습 중 <span className="tp-spinner-sm" /></>
              ) : "학습 시작"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
