import React, { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { calculateBollinger, calculateEMA, calculateMACD, calculateRSI, calculateSMA } from "../utils/indicators";

function alignForecast(categories, points, key) {
  const indexMap = new Map(categories.map((item, index) => [item, index]));
  const values = new Array(categories.length).fill(null);
  for (const point of points || []) {
    const index = indexMap.get(point.ts);
    if (index == null) continue;
    values[index] = Number(point[key]);
  }
  return values;
}

function toSeriesData(values) {
  return values.map((item) => (item == null ? "-" : Number(item)));
}

export default function TradingChart({
  history,
  indicators,
  forecastsByModel,
  modelColors,
  forecastVisibility,
  activeModelId,
  viewport,
  onViewportChange,
  onNeedOlderData,
}) {
  const categories = useMemo(() => {
    const historyTs = history.map((item) => item.ts);
    const forecastTs = [];
    Object.values(forecastsByModel || {}).forEach((forecast) => {
      (forecast?.points || []).forEach((point) => {
        if (!historyTs.includes(point.ts) && !forecastTs.includes(point.ts)) forecastTs.push(point.ts);
      });
    });
    return [...historyTs, ...forecastTs.sort((left, right) => new Date(left) - new Date(right))];
  }, [history, forecastsByModel]);

  const categoryIndexMap = useMemo(
    () => new Map(categories.map((value, index) => [value, index])),
    [categories]
  );

  const option = useMemo(() => {
    const closeValues = history.map((item) => Number(item.close));
    const candleData = history.map((item) => [Number(item.open), Number(item.close), Number(item.low), Number(item.high)]);
    const volumeData = history.map((item) => Number(item.volume));

    const enabledIndicators = (indicators || []).filter((item) => item.enabled);
    const mainIndicators = enabledIndicators.filter((item) => ["EMA", "SMA", "BB"].includes(item.type));
    const rsiIndicators = enabledIndicators.filter((item) => item.type === "RSI");
    const macdIndicators = enabledIndicators.filter((item) => item.type === "MACD");
    const hasRsiPanel = rsiIndicators.length > 0;
    const hasMacdPanel = macdIndicators.length > 0;

    const panelNames = ["main", "volume", ...(hasRsiPanel ? ["rsi"] : []), ...(hasMacdPanel ? ["macd"] : [])];
    const panelCount = panelNames.length;
    const gap = 2;
    const totalGap = (panelCount - 1) * gap;
    const usable = 90 - totalGap;
    const mainHeight = panelCount > 2 ? Math.max(42, usable * 0.62) : Math.max(56, usable * 0.7);
    const secondaryHeight = panelCount > 1 ? (usable - mainHeight) / (panelCount - 1) : 0;

    let topCursor = 5;
    const gridByName = {};
    panelNames.forEach((name, index) => {
      const height = index === 0 ? mainHeight : secondaryHeight;
      gridByName[name] = { top: `${topCursor}%`, height: `${height}%` };
      topCursor += height + gap;
    });

    const grids = panelNames.map((name) => ({
      left: 54,
      right: 14,
      top: gridByName[name].top,
      height: gridByName[name].height,
    }));

    const xAxes = panelNames.map((_, index) => ({
      type: "category",
      gridIndex: index,
      data: categories,
      boundaryGap: true,
      axisLine: { lineStyle: { color: "#2a3858" } },
      axisTick: { show: false },
      axisLabel: { color: "#8fa7cf", show: index === panelCount - 1 },
      min: "dataMin",
      max: "dataMax",
    }));

    const yAxes = panelNames.map((name, index) => ({
      type: "value",
      gridIndex: index,
      scale: true,
      axisLine: { lineStyle: { color: "#2a3858" } },
      axisLabel: { color: "#8fa7cf" },
      splitLine: { lineStyle: { color: "rgba(148,167,202,0.14)" } },
      min: name === "rsi" ? 0 : null,
      max: name === "rsi" ? 100 : null,
    }));

    const panelIndex = (name) => panelNames.indexOf(name);
    const series = [
      {
        name: "Candles",
        type: "candlestick",
        xAxisIndex: panelIndex("main"),
        yAxisIndex: panelIndex("main"),
        data: candleData,
        itemStyle: {
          color: "#22c55e",
          color0: "#ef4444",
          borderColor: "#22c55e",
          borderColor0: "#ef4444",
        },
      },
      {
        name: "Volume",
        type: "bar",
        xAxisIndex: panelIndex("volume"),
        yAxisIndex: panelIndex("volume"),
        data: volumeData,
        itemStyle: {
          color: (params) => {
            const candle = history[params?.dataIndex] || {};
            return Number(candle.close) >= Number(candle.open) ? "rgba(34,197,94,0.52)" : "rgba(239,68,68,0.52)";
          },
        },
      },
    ];

    mainIndicators.forEach((indicator) => {
      if (indicator.type === "EMA") {
        series.push({
          name: `EMA ${indicator.params.period}`,
          type: "line",
          xAxisIndex: panelIndex("main"),
          yAxisIndex: panelIndex("main"),
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.5, color: indicator.color },
          data: toSeriesData(calculateEMA(closeValues, Number(indicator.params.period) || 20)),
        });
      }
      if (indicator.type === "SMA") {
        series.push({
          name: `SMA ${indicator.params.period}`,
          type: "line",
          xAxisIndex: panelIndex("main"),
          yAxisIndex: panelIndex("main"),
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.5, color: indicator.color },
          data: toSeriesData(calculateSMA(closeValues, Number(indicator.params.period) || 20)),
        });
      }
      if (indicator.type === "BB") {
        const period = Number(indicator.params.period) || 20;
        const stddev = Number(indicator.params.stddev) || 2;
        const bands = calculateBollinger(closeValues, period, stddev);
        series.push(
          {
            name: `BB Mid ${period}`,
            type: "line",
            xAxisIndex: panelIndex("main"),
            yAxisIndex: panelIndex("main"),
            showSymbol: false,
            lineStyle: { width: 1.2, color: indicator.color, opacity: 0.85 },
            data: toSeriesData(bands.middle),
          },
          {
            name: `BB Upper ${period}`,
            type: "line",
            xAxisIndex: panelIndex("main"),
            yAxisIndex: panelIndex("main"),
            showSymbol: false,
            lineStyle: { width: 1, color: indicator.color, opacity: 0.5 },
            data: toSeriesData(bands.upper),
          },
          {
            name: `BB Lower ${period}`,
            type: "line",
            xAxisIndex: panelIndex("main"),
            yAxisIndex: panelIndex("main"),
            showSymbol: false,
            lineStyle: { width: 1, color: indicator.color, opacity: 0.5 },
            data: toSeriesData(bands.lower),
          }
        );
      }
    });

    if (hasRsiPanel) {
      rsiIndicators.forEach((indicator) => {
        const period = Number(indicator.params.period) || 14;
        series.push({
          name: `RSI ${period}`,
          type: "line",
          xAxisIndex: panelIndex("rsi"),
          yAxisIndex: panelIndex("rsi"),
          showSymbol: false,
          lineStyle: { width: 1.5, color: indicator.color },
          data: toSeriesData(calculateRSI(closeValues, period)),
          markLine: {
            symbol: ["none", "none"],
            lineStyle: { color: "rgba(148,167,202,0.45)", type: "dashed", width: 1 },
            data: [{ yAxis: 30 }, { yAxis: 70 }],
          },
        });
      });
    }

    if (hasMacdPanel) {
      macdIndicators.forEach((indicator) => {
        const fast = Number(indicator.params.fast) || 12;
        const slow = Number(indicator.params.slow) || 26;
        const signal = Number(indicator.params.signal) || 9;
        const macd = calculateMACD(closeValues, fast, slow, signal);
        series.push(
          {
            name: `MACD ${fast}/${slow}`,
            type: "line",
            xAxisIndex: panelIndex("macd"),
            yAxisIndex: panelIndex("macd"),
            showSymbol: false,
            lineStyle: { width: 1.5, color: indicator.color },
            data: toSeriesData(macd.macdLine),
          },
          {
            name: `MACD Signal ${signal}`,
            type: "line",
            xAxisIndex: panelIndex("macd"),
            yAxisIndex: panelIndex("macd"),
            showSymbol: false,
            lineStyle: { width: 1.2, color: "#f472b6" },
            data: toSeriesData(macd.signalLine),
          },
          {
            name: `MACD Hist ${fast}/${slow}`,
            type: "bar",
            xAxisIndex: panelIndex("macd"),
            yAxisIndex: panelIndex("macd"),
            itemStyle: {
              color: (params) => (Number(params.value || 0) >= 0 ? "rgba(34,197,94,0.5)" : "rgba(239,68,68,0.5)"),
            },
            data: toSeriesData(macd.histogram),
          }
        );
      });
    }

    Object.entries(forecastsByModel || {}).forEach(([modelId, forecast]) => {
      if (!forecastVisibility[modelId]) return;
      const color = modelColors[modelId] || "#38bdf8";
      const mean = toSeriesData(alignForecast(categories, forecast?.points || [], "pred_close"));
      series.push({
        name: `Forecast ${modelId.slice(-6)}`,
        type: "line",
        xAxisIndex: panelIndex("main"),
        yAxisIndex: panelIndex("main"),
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 2, type: "dashed", color },
        data: mean,
      });

      if (modelId === activeModelId) {
        const lower = alignForecast(categories, forecast?.points || [], "pred_low");
        const upper = alignForecast(categories, forecast?.points || [], "pred_high");
        const band = upper.map((value, index) => (value == null || lower[index] == null ? null : Number(value) - Number(lower[index])));
        series.push(
          {
            name: "Band Lower",
            type: "line",
            xAxisIndex: panelIndex("main"),
            yAxisIndex: panelIndex("main"),
            stack: "active-band",
            lineStyle: { width: 0, opacity: 0 },
            showSymbol: false,
            data: toSeriesData(lower),
          },
          {
            name: "Band Width",
            type: "line",
            xAxisIndex: panelIndex("main"),
            yAxisIndex: panelIndex("main"),
            stack: "active-band",
            lineStyle: { width: 0, opacity: 0 },
            areaStyle: { color: "rgba(56,189,248,0.14)" },
            showSymbol: false,
            data: toSeriesData(band),
          }
        );
      }
    });

    const hasViewport = viewport.startValue != null && viewport.endValue != null;
    return {
      animationDurationUpdate: 260,
      backgroundColor: "transparent",
      legend: { top: 4, textStyle: { color: "#9fb0d3" } },
      axisPointer: { link: [{ xAxisIndex: panelNames.map((_, index) => index) }], label: { backgroundColor: "#111b2f" } },
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      grid: grids,
      xAxis: xAxes,
      yAxis: yAxes,
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: panelNames.map((_, index) => index),
          ...(hasViewport ? { startValue: viewport.startValue, endValue: viewport.endValue } : { start: 70, end: 100 }),
        },
        {
          type: "slider",
          xAxisIndex: panelNames.map((_, index) => index),
          bottom: 4,
          height: 18,
          borderColor: "#2a3858",
          backgroundColor: "#0a1224",
          fillerColor: "rgba(56,189,248,0.2)",
          handleStyle: { color: "#38bdf8" },
          textStyle: { color: "#9fb0d3" },
          ...(hasViewport ? { startValue: viewport.startValue, endValue: viewport.endValue } : { start: 70, end: 100 }),
        },
      ],
      series,
    };
  }, [history, indicators, forecastsByModel, modelColors, forecastVisibility, activeModelId, viewport, categories]);

  const onEvents = useMemo(
    () => ({
      datazoom: (event) => {
        const payload = Array.isArray(event?.batch) && event.batch.length ? event.batch[0] : event;
        const length = categories.length;
        if (!length) return;

        const toIndex = (value, fallbackPercent) => {
          if (typeof value === "number" && Number.isFinite(value)) {
            return Math.max(0, Math.min(length - 1, Math.round(value)));
          }
          if (typeof value === "string") {
            const idx = categoryIndexMap.get(value);
            if (typeof idx === "number") return idx;
          }
          const percent = Number(fallbackPercent ?? 0);
          return Math.max(0, Math.min(length - 1, Math.round((percent / 100) * (length - 1))));
        };

        const startIndex = toIndex(payload?.startValue, payload?.start);
        const endIndex = toIndex(payload?.endValue, payload?.end);
        const nextStart = categories[Math.min(startIndex, endIndex)] || null;
        const nextEnd = categories[Math.max(startIndex, endIndex)] || null;

        if (nextStart == null || nextEnd == null) return;
        if (viewport.startValue !== nextStart || viewport.endValue !== nextEnd) {
          onViewportChange?.({ startValue: nextStart, endValue: nextEnd });
        }
        if (Math.min(startIndex, endIndex) <= 25) onNeedOlderData?.();
      },
    }),
    [categories, categoryIndexMap, onNeedOlderData, onViewportChange, viewport.endValue, viewport.startValue]
  );

  return <ReactECharts option={option} style={{ height: "100%", width: "100%" }} notMerge={false} lazyUpdate={true} onEvents={onEvents} />;
}
