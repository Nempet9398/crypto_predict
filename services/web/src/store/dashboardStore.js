import { create } from "zustand";

const INDICATOR_COLORS = {
  EMA: "#f59e0b",
  SMA: "#60a5fa",
  RSI: "#a78bfa",
  MACD: "#22d3ee",
  BB: "#f472b6",
};

let nextIndicatorId = 1;
const makeId = () => `ind-${nextIndicatorId++}`;

const defaultIndicators = [
  { id: makeId(), type: "EMA", enabled: true, color: "#f59e0b", params: { period: 20 } },
  { id: makeId(), type: "EMA", enabled: true, color: "#60a5fa", params: { period: 50 } },
  { id: makeId(), type: "EMA", enabled: true, color: "#a78bfa", params: { period: 200 } },
];

const defaultModelColors = ["#38bdf8", "#22c55e", "#f59e0b", "#f472b6", "#a78bfa", "#14b8a6"];

export const useDashboardStore = create((set, get) => ({
  symbol: "ETH/USDT",
  timeframe: "15m",
  horizonHours: 6,
  lookbackDays: 14,
  autoRefresh: true,
  indicatorModalOpen: false,

  loadedRange: { oldestTs: null, newestTs: null, hasMoreHistory: true, loadingOlder: false },
  viewport: { startValue: null, endValue: null },

  indicators: defaultIndicators,

  modelRegistry: [],
  activeModelId: null,
  forecastVisibility: {},
  modelColors: {},

  setSymbol: (symbol) => set({ symbol }),
  setTimeframe: (timeframe) =>
    set({
      timeframe,
      loadedRange: { oldestTs: null, newestTs: null, hasMoreHistory: true, loadingOlder: false },
      viewport: { startValue: null, endValue: null },
    }),
  setHorizonHours: (horizonHours) => set({ horizonHours }),
  setLookbackDays: (lookbackDays) => set({ lookbackDays }),
  setAutoRefresh: (autoRefresh) => set({ autoRefresh }),

  setIndicatorModalOpen: (indicatorModalOpen) => set({ indicatorModalOpen }),
  addIndicator: (indicatorType, params = {}) =>
    set((state) => {
      const id = makeId();
      const indicator = {
        id,
        type: indicatorType,
        enabled: true,
        color: INDICATOR_COLORS[indicatorType] || "#38bdf8",
        params:
          indicatorType === "MACD"
            ? { fast: 12, slow: 26, signal: 9, ...params }
            : indicatorType === "BB"
              ? { period: 20, stddev: 2, ...params }
              : { period: 14, ...params },
      };
      return { indicators: [...state.indicators, indicator] };
    }),
  removeIndicator: (id) => set((state) => ({ indicators: state.indicators.filter((item) => item.id !== id) })),
  toggleIndicator: (id) =>
    set((state) => ({
      indicators: state.indicators.map((item) => (item.id === id ? { ...item, enabled: !item.enabled } : item)),
    })),
  updateIndicator: (id, patch) =>
    set((state) => ({
      indicators: state.indicators.map((item) => (item.id === id ? { ...item, ...patch, params: { ...item.params, ...(patch.params || {}) } } : item)),
    })),

  setLoadedRange: (loadedRange) => set({ loadedRange }),
  setLoadingOlder: (loadingOlder) => set((state) => ({ loadedRange: { ...state.loadedRange, loadingOlder } })),
  setViewport: (viewport) => set({ viewport }),
  shiftViewportBy: (delta) =>
    set((state) => {
      const { startValue, endValue } = state.viewport;
      if (startValue == null || endValue == null) return state;
      return { viewport: { startValue: startValue + delta, endValue: endValue + delta } };
    }),

  setModels: (modelsPayload) =>
    set((state) => {
      const items = modelsPayload?.models || [];
      const activeId = modelsPayload?.active_model_id || null;
      const resolvedActiveId = activeId || state.activeModelId || items[0]?.id || null;
      const modelColors = { ...state.modelColors };
      const forecastVisibility = { ...state.forecastVisibility };
      items.forEach((item, index) => {
        if (!modelColors[item.id]) modelColors[item.id] = defaultModelColors[index % defaultModelColors.length];
        if (forecastVisibility[item.id] == null) forecastVisibility[item.id] = item.id === resolvedActiveId;
      });
      return {
        modelRegistry: items,
        activeModelId: resolvedActiveId,
        modelColors,
        forecastVisibility,
      };
    }),
  setActiveModelId: (activeModelId) =>
    set((state) => ({
      activeModelId,
      forecastVisibility:
        activeModelId == null
          ? state.forecastVisibility
          : {
              ...state.forecastVisibility,
              [activeModelId]: true,
            },
    })),
  toggleForecastModel: (modelId) =>
    set((state) => ({
      forecastVisibility: {
        ...state.forecastVisibility,
        [modelId]: !state.forecastVisibility[modelId],
      },
    })),
  setForecastModelVisibility: (modelId, visible) =>
    set((state) => ({
      forecastVisibility: {
        ...state.forecastVisibility,
        [modelId]: visible,
      },
    })),

  resetViewport: () => set({ viewport: { startValue: null, endValue: null } }),

  visibleForecastModelIds: () =>
    (get().modelRegistry || [])
      .map((item) => item.id)
      .filter((modelId) => get().forecastVisibility[modelId]),
}));
