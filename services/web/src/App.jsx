import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Toaster, toast } from "react-hot-toast";
import IndicatorManagerModal from "./components/IndicatorManagerModal";
import RightPanel from "./components/RightPanel";
import SignalPanel from "./components/SignalPanel";
import TopBar from "./components/TopBar";
import TradingChart from "./components/TradingChart";
import { useDashboardStore } from "./store/dashboardStore";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const TZ = "Asia/Seoul";
const INITIAL_LIMIT = 500;
const PAGE_LIMIT = 500;

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = body?.detail ? `: ${body.detail}` : "";
    } catch {
      // ignore json parse failures
    }
    throw new Error(`API error (${response.status})${detail}`);
  }
  return response.json();
}

function mergeLatest(history, latestPoints) {
  if (!Array.isArray(latestPoints) || !latestPoints.length) return history;
  const byTs = new Map((history || []).map((item) => [item.ts, item]));
  latestPoints.forEach((item) => byTs.set(item.ts, item));
  return [...byTs.values()].sort((left, right) => new Date(left.ts) - new Date(right.ts));
}

function prependOlder(history, olderPoints) {
  if (!Array.isArray(olderPoints) || !olderPoints.length) return { merged: history, prepended: 0 };
  const known = new Set((history || []).map((item) => item.ts));
  const filtered = olderPoints.filter((item) => !known.has(item.ts));
  return { merged: [...filtered, ...(history || [])], prepended: filtered.length };
}

export default function App() {
  const {
    symbol,
    timeframe,
    horizonHours,
    lookbackDays,
    autoRefresh,
    indicatorModalOpen,
    indicators,
    modelRegistry,
    activeModelId,
    forecastVisibility,
    modelColors,
    viewport,
    loadedRange,
    setSymbol,
    setTimeframe,
    setHorizonHours,
    setLookbackDays,
    setAutoRefresh,
    setIndicatorModalOpen,
    addIndicator,
    removeIndicator,
    toggleIndicator,
    updateIndicator,
    setModels,
    setActiveModelId,
    toggleForecastModel,
    setLoadedRange,
    setLoadingOlder,
    setViewport,
  } = useDashboardStore();

  const [history, setHistory] = useState([]);
  const [forecastsByModel, setForecastsByModel] = useState({});
  const [loading, setLoading] = useState(false);
  const [training, setTraining] = useState(false);
  const [error, setError] = useState("");
  const [ensembleSignal, setEnsembleSignal] = useState(null);
  const loadingOlderRef = useRef(false);
  const loadedRangeRef = useRef(loadedRange);

  useEffect(() => {
    loadedRangeRef.current = loadedRange;
  }, [loadedRange]);

  const visibleModelIds = useMemo(
    () => (modelRegistry || []).filter((model) => forecastVisibility[model.id]).map((model) => model.id),
    [modelRegistry, forecastVisibility]
  );

  const fetchHistory = useCallback(
    async ({ before = null, limit = INITIAL_LIMIT } = {}) => {
      const query = new URLSearchParams({
        symbol,
        timeframe,
        limit: String(limit),
        tz: TZ,
      });
      if (before) query.set("before", before);
      return fetchJson(`${API_URL}/prices/history?${query.toString()}`);
    },
    [symbol, timeframe]
  );

  const fetchModels = useCallback(async () => fetchJson(`${API_URL}/models?limit=100`), []);

  const refreshEnsembleSignal = useCallback(async () => {
    try {
      const query = new URLSearchParams({ horizon_hours: String(horizonHours), timeframe });
      const result = await fetchJson(`${API_URL}/signals/ensemble?${query.toString()}`);
      setEnsembleSignal(result);
    } catch {
      // non-fatal: keep previous signal
    }
  }, [horizonHours, timeframe]);

  const refreshForecasts = useCallback(
    async (modelIds) => {
      const ids = (modelIds || []).filter(Boolean);
      if (!ids.length) {
        setForecastsByModel({});
        return;
      }
      const jobs = ids.map(async (modelId) => {
        try {
          const query = new URLSearchParams({
            model_id: modelId,
            timeframe,
            horizon_hours: String(horizonHours),
            tz: TZ,
          });
          const forecast = await fetchJson(`${API_URL}/forecast?${query.toString()}`);
          return [modelId, forecast];
        } catch {
          return [modelId, null];
        }
      });
      const entries = await Promise.all(jobs);
      setForecastsByModel(Object.fromEntries(entries.filter((item) => item[1])));
    },
    [timeframe, horizonHours]
  );

  const loadInitial = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [modelsPayload, historyPayload] = await Promise.all([fetchModels(), fetchHistory({ limit: INITIAL_LIMIT })]);
      setModels(modelsPayload);

      const points = historyPayload.points || [];
      setHistory(points);
      setLoadedRange({
        oldestTs: points[0]?.ts || null,
        newestTs: points[points.length - 1]?.ts || null,
        hasMoreHistory: points.length === INITIAL_LIMIT,
        loadingOlder: false,
      });

      if (points.length > 0) {
        const endValue = points.length - 1;
        const startIndex = Math.max(0, endValue - 240);
        setViewport({ startValue: points[startIndex]?.ts || null, endValue: points[endValue]?.ts || null });
      } else {
        setViewport({ startValue: null, endValue: null });
      }

      const preferredModelId = modelsPayload.active_model_id || modelsPayload.models?.[0]?.id || null;
      if (preferredModelId) setActiveModelId(preferredModelId);
      await refreshForecasts(preferredModelId ? [preferredModelId] : []);
      refreshEnsembleSignal().catch(() => {});
    } catch (loadError) {
      setError(loadError.message || "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, [
    fetchHistory,
    fetchModels,
    refreshEnsembleSignal,
    refreshForecasts,
    setActiveModelId,
    setLoadedRange,
    setModels,
    setViewport,
  ]);

  const refreshLatestOnly = useCallback(async () => {
    const query = new URLSearchParams({
      symbol,
      timeframe,
      limit: "3",
      tz: TZ,
    });
    const latestPayload = await fetchJson(`${API_URL}/prices/latest?${query.toString()}`);
    const latestPoints = latestPayload.points || [];
    setHistory((previous) => mergeLatest(previous, latestPoints));
    if (latestPoints.length) {
      const nextRange = {
        ...(loadedRangeRef.current || loadedRange),
        newestTs: latestPoints[latestPoints.length - 1].ts,
      };
      loadedRangeRef.current = nextRange;
      setLoadedRange(nextRange);
    }
  }, [symbol, timeframe, setLoadedRange]);

  const loadOlderHistory = useCallback(async () => {
    const range = loadedRangeRef.current;
    if (loadingOlderRef.current || range.loadingOlder || !range.hasMoreHistory || !range.oldestTs) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    try {
      const olderPayload = await fetchHistory({ before: range.oldestTs, limit: PAGE_LIMIT });
      const olderPoints = olderPayload.points || [];
      if (!olderPoints.length) {
        const nextRange = { ...range, hasMoreHistory: false, loadingOlder: false };
        loadedRangeRef.current = nextRange;
        setLoadedRange(nextRange);
        loadingOlderRef.current = false;
        return;
      }

      let prependedCount = 0;
      setHistory((previous) => {
        const { merged, prepended } = prependOlder(previous, olderPoints);
        prependedCount = prepended;
        return merged;
      });

      const nextRange = {
        oldestTs: olderPoints[0]?.ts || range.oldestTs,
        newestTs: range.newestTs,
        hasMoreHistory: olderPoints.length === PAGE_LIMIT,
        loadingOlder: false,
      };
      loadedRangeRef.current = nextRange;
      setLoadedRange(nextRange);
    } catch (olderError) {
      setError(olderError.message || "Failed to load older candles");
      setLoadingOlder(false);
    } finally {
      loadingOlderRef.current = false;
    }
  }, [fetchHistory, setLoadedRange, setLoadingOlder]);

  const onRefresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      await refreshLatestOnly();
      const modelsPayload = await fetchModels();
      setModels(modelsPayload);
      const preferredModelId = activeModelId || modelsPayload.active_model_id || modelsPayload.models?.[0]?.id || null;
      if (preferredModelId && !activeModelId) setActiveModelId(preferredModelId);
      const targetIds = (modelsPayload.models || [])
        .filter((model) => forecastVisibility[model.id] || model.id === preferredModelId)
        .map((model) => model.id);
      await refreshForecasts(targetIds);
    } catch (refreshError) {
      setError(refreshError.message || "Failed to refresh dashboard");
    } finally {
      setLoading(false);
    }
  }, [
    activeModelId,
    fetchModels,
    forecastVisibility,
    refreshForecasts,
    refreshLatestOnly,
    setActiveModelId,
    setModels,
  ]);

  const onTrain = useCallback(async () => {
    setTraining(true);
    const toastId = toast.loading("Training model...");
    const prevViewport = viewport;
    try {
      await fetchJson(`${API_URL}/train`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback_days: Number(lookbackDays),
          horizon_hours: Number(horizonHours),
          symbol,
          timeframe,
          exchange: "binance",
          auto_order: true,
          set_active: true,
        }),
      });
      await loadInitial();
      if (prevViewport?.startValue != null && prevViewport?.endValue != null) {
        setViewport(prevViewport);
      }
      toast.success("Training complete", { id: toastId });
    } catch (trainError) {
      toast.error(trainError.message || "Training failed", { id: toastId });
    } finally {
      setTraining(false);
    }
  }, [horizonHours, loadInitial, lookbackDays, setViewport, symbol, timeframe, viewport]);

  useEffect(() => {
    loadInitial();
  }, [loadInitial]);

  useEffect(() => {
    const ids = visibleModelIds.length ? visibleModelIds : activeModelId ? [activeModelId] : [];
    refreshForecasts(ids).catch(() => {
      // keep previous forecasts on transient issues
    });
  }, [activeModelId, refreshForecasts, visibleModelIds]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = setInterval(() => {
      refreshLatestOnly()
        .then(() => Promise.all([
          refreshForecasts(visibleModelIds.length ? visibleModelIds : activeModelId ? [activeModelId] : []),
          refreshEnsembleSignal(),
        ]))
        .catch(() => {
          // no-op, transient refresh errors are ignored
        });
    }, 15000);
    return () => clearInterval(timer);
  }, [activeModelId, autoRefresh, refreshEnsembleSignal, refreshForecasts, refreshLatestOnly, visibleModelIds]);

  return (
    <div className="dashboard-root">
      <Toaster position="top-right" toastOptions={{ style: { background: "#0f1628", color: "#dbe7ff", border: "1px solid #1f2b45" } }} />

      <TopBar
        symbol={symbol}
        timeframe={timeframe}
        models={modelRegistry}
        activeModelId={activeModelId}
        autoRefresh={autoRefresh}
        loading={loading}
        training={training}
        onSymbolChange={setSymbol}
        onTimeframeChange={setTimeframe}
        onOpenIndicators={() => setIndicatorModalOpen(true)}
        onModelChange={setActiveModelId}
        onRefresh={onRefresh}
        onTrain={onTrain}
        onAutoRefreshChange={setAutoRefresh}
      />

      {error ? <div className="error-banner">{error}</div> : null}

      <section className="dashboard-main">
        <div className="chart-shell">
          <TradingChart
            history={history}
            indicators={indicators}
            forecastsByModel={forecastsByModel}
            modelColors={modelColors}
            forecastVisibility={forecastVisibility}
            activeModelId={activeModelId}
            viewport={viewport}
            onViewportChange={setViewport}
            onNeedOlderData={loadOlderHistory}
          />
          <div className="model-toggles">
            {(modelRegistry || []).map((model) => (
              <label key={model.id} className="toggle">
                <input type="checkbox" checked={Boolean(forecastVisibility[model.id])} onChange={() => toggleForecastModel(model.id)} />
                <span style={{ color: modelColors[model.id] || "#38bdf8" }}>{model.name}</span>
              </label>
            ))}
          </div>
        </div>

        <RightPanel
          timeframe={timeframe}
          modelRegistry={modelRegistry}
          forecastsByModel={forecastsByModel}
          modelColors={modelColors}
          forecastVisibility={forecastVisibility}
          activeModelId={activeModelId}
        />

        <SignalPanel
          ensemble={ensembleSignal}
          selectedHorizon={horizonHours}
          modelStatus={null}
        />
      </section>

      <IndicatorManagerModal
        open={indicatorModalOpen}
        indicators={indicators}
        onClose={() => setIndicatorModalOpen(false)}
        onAdd={addIndicator}
        onRemove={removeIndicator}
        onToggle={toggleIndicator}
        onUpdate={updateIndicator}
      />
    </div>
  );
}
