import React, { useState, useEffect, useCallback } from "react";
import { Toaster, toast } from "react-hot-toast";
import AccuracyPanel from "./components/AccuracyPanel";
import IndicatorManagerModal from "./components/IndicatorManagerModal";
import MarketStatsBar from "./components/MarketStatsBar";
import ModelDataStatusPanel from "./components/ModelDataStatusPanel";
import PositionPanel from "./components/PositionPanel";
import PredictionPanel from "./components/PredictionPanel";
import SignalHistoryPanel from "./components/SignalHistoryPanel";
import TechnicalSummary from "./components/TechnicalSummary";
import TopBar from "./components/TopBar";
import TradingChart from "./components/TradingChart";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function api(path, options) {
  const res = await fetch(API_URL + path, options);
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json())?.detail || ""; } catch {}
    throw new Error("API " + res.status + (detail ? ": " + detail : ""));
  }
  return res.json();
}

function mergeLatest(history, latest) {
  const map = new Map(history.map((b) => [b.ts, b]));
  latest.forEach((b) => map.set(b.ts, b));
  return [...map.values()].sort((a, b) => new Date(a.ts) - new Date(b.ts));
}

const BOTTOM_TABS = [
  { key: "accuracy", label: "예측 정확도" },
  { key: "signals", label: "신호 이력" },
  { key: "status", label: "시스템 상태" },
];

export default function App() {
  const [symbol, setSymbol] = useState("ETH/USDT");
  const [timeframe, setTimeframe] = useState("15m");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [indicatorModalOpen, setIndicatorModalOpen] = useState(false);
  const [indicators, setIndicators] = useState({ ema20: true, ema50: true });
  const [activeTab, setActiveTab] = useState("accuracy");

  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [signal, setSignal] = useState(null);
  const [signalHistory, setSignalHistory] = useState([]);
  const [features, setFeatures] = useState(null);
  const [dataStatus, setDataStatus] = useState(null);
  const [mlStatus, setMlStatus] = useState(null);
  const [trainingML, setTrainingML] = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);

  const loadHistory = useCallback(async () => {
    const data = await api("/prices/history?symbol=" + encodeURIComponent(symbol) + "&timeframe=" + timeframe + "&limit=500&tz=Asia/Seoul");
    return data.points || [];
  }, [symbol, timeframe]);

  const loadLatest = useCallback(async () => {
    const data = await api("/prices/latest?symbol=" + encodeURIComponent(symbol) + "&timeframe=" + timeframe + "&limit=3&tz=Asia/Seoul");
    return data.points || [];
  }, [symbol, timeframe]);

  const loadSignal = useCallback(async () => {
    try { const d = await api("/signals/current"); setSignal(d); } catch {}
  }, []);

  const loadSignalHistory = useCallback(async () => {
    try { const d = await api("/signals/history?limit=50"); setSignalHistory(d.signals || []); } catch {}
  }, []);

  const loadFeatures = useCallback(async () => {
    try { const d = await api("/features/latest?exchange=binance&symbol=" + encodeURIComponent(symbol) + "&timeframe=" + timeframe); setFeatures(d); } catch {}
  }, [symbol, timeframe]);

  const loadDataStatus = useCallback(async () => {
    try { const d = await api("/data/status"); setDataStatus(d); } catch {}
  }, []);

  const loadMlStatus = useCallback(async () => {
    try { const d = await api("/ml/status"); setMlStatus(d); } catch {}
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const pts = await loadHistory();
      setHistory(pts);
      setLastUpdate(new Date().toISOString());
      await Promise.allSettled([loadSignal(), loadSignalHistory(), loadFeatures(), loadDataStatus(), loadMlStatus()]);
    } catch (err) {
      toast.error(err.message || "데이터 로딩 실패");
    } finally {
      setLoading(false);
    }
  }, [loadHistory, loadSignal, loadSignalHistory, loadFeatures, loadDataStatus, loadMlStatus]);

  useEffect(() => { loadAll(); }, [loadAll]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(async () => {
      try {
        const pts = await loadLatest();
        if (pts.length) { setHistory((prev) => mergeLatest(prev, pts)); setLastUpdate(new Date().toISOString()); }
        await Promise.allSettled([loadSignal(), loadFeatures()]);
      } catch {}
    }, 15000);
    return () => clearInterval(timer);
  }, [autoRefresh, loadLatest, loadSignal, loadFeatures]);

  const onSymbolChange = useCallback((s) => { setSymbol(s); setHistory([]); }, []);
  const onTimeframeChange = useCallback((tf) => { setTimeframe(tf); setHistory([]); }, []);

  const onTrainML = useCallback(async () => {
    setTrainingML(true);
    const tid = toast.loading("ML 모델 학습 시작...");
    try {
      const res = await api("/ml/train?lookback_days=60&horizon_bars=4", { method: "POST" });
      toast.success(res.message || "학습 시작됨", { id: tid });
      setTimeout(() => loadMlStatus(), 30000);
    } catch (err) {
      toast.error(err.message || "학습 실패", { id: tid });
    } finally {
      setTrainingML(false);
    }
  }, [loadMlStatus]);

  const onRefresh = useCallback(async () => {
    const tid = toast.loading("새로고침 중...");
    try { await loadAll(); toast.success("완료", { id: tid }); }
    catch (err) { toast.error(err.message, { id: tid }); }
  }, [loadAll]);

  return (
    <div className="app-root">
      <Toaster position="top-right" toastOptions={{ style: { background: "#1e2130", color: "#e2e8f0", border: "1px solid rgba(255,255,255,0.1)", fontSize: "13px" } }} />

      <TopBar
        symbol={symbol} timeframe={timeframe} autoRefresh={autoRefresh}
        loading={loading} mlStatus={mlStatus} lastUpdate={lastUpdate}
        onSymbolChange={onSymbolChange} onTimeframeChange={onTimeframeChange}
        onAutoRefreshChange={setAutoRefresh} onRefresh={onRefresh}
        onOpenIndicators={() => setIndicatorModalOpen(true)}
        onTrainML={onTrainML} trainingML={trainingML}
      />

      <MarketStatsBar history={history} features={features} dataStatus={dataStatus} />

      <main className="dashboard-main">
        <div className="chart-area">
          <TradingChart history={history} signal={signal} indicators={indicators} />
        </div>
        <aside className="right-panel">
          <PredictionPanel signal={signal} />
          <PositionPanel signal={signal} />
          <TechnicalSummary features={features} />
        </aside>
      </main>

      <section className="bottom-section">
        <div className="bottom-tabs">
          {BOTTOM_TABS.map((tab) => (
            <button key={tab.key} className={"bottom-tab" + (activeTab === tab.key ? " active" : "")} onClick={() => setActiveTab(tab.key)}>
              {tab.label}
            </button>
          ))}
        </div>
        <div className="bottom-content">
          {activeTab === "accuracy" && <AccuracyPanel />}
          {activeTab === "signals" && <SignalHistoryPanel signals={signalHistory} />}
          {activeTab === "status" && <ModelDataStatusPanel dataStatus={dataStatus} mlStatus={mlStatus} />}
        </div>
      </section>

      <IndicatorManagerModal open={indicatorModalOpen} indicators={indicators} onChange={setIndicators} onClose={() => setIndicatorModalOpen(false)} />
    </div>
  );
}
