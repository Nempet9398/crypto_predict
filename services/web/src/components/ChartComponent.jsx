import React, { useEffect, useMemo, useRef } from "react";
import { ColorType, createChart } from "lightweight-charts";

const HORIZON_COLORS = {
  3: "#f59e0b",
  6: "#22d3ee",
  12: "#38bdf8",
  24: "#a78bfa",
};

const LEVEL_SCALE = {
  0.5: 0.344,
  0.7: 0.529,
  0.9: 0.839,
};

function toUnixSeconds(iso) {
  return Math.floor(new Date(iso).getTime() / 1000);
}

function buildFan(points, level) {
  return points.map((point) => {
    const mean = Number(point.pred_close);
    const low95 = Number(point.pred_low);
    const high95 = Number(point.pred_high);
    const ratio = LEVEL_SCALE[level] || 1.0;
    const half95 = (high95 - low95) / 2;
    const half = half95 * ratio;
    return {
      time: toUnixSeconds(point.ts),
      upper: mean + half,
      lower: mean - half,
      mean,
    };
  });
}

export default function ChartComponent({
  history,
  forecastMap,
  visibleHorizons,
  fanLevels,
  selectedHorizon,
  signal,
  onCrosshairMove,
}) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef([]);

  const activeForecast = useMemo(() => forecastMap?.[selectedHorizon]?.points || [], [forecastMap, selectedHorizon]);

  useEffect(() => {
    if (!containerRef.current || chartRef.current) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 520,
      layout: {
        textColor: "#d1d5db",
        background: { type: ColorType.Solid, color: "#0a0f1a" },
      },
      grid: {
        vertLines: { color: "rgba(148, 163, 184, 0.15)" },
        horzLines: { color: "rgba(148, 163, 184, 0.12)" },
      },
      rightPriceScale: {
        borderColor: "rgba(148, 163, 184, 0.25)",
      },
      timeScale: {
        borderColor: "rgba(148, 163, 184, 0.25)",
        timeVisible: true,
      },
      crosshair: {
        vertLine: { color: "rgba(148, 163, 184, 0.5)" },
        horzLine: { color: "rgba(148, 163, 184, 0.5)" },
      },
    });

    chart.subscribeCrosshairMove((param) => {
      if (!param?.time || !onCrosshairMove) return;
      onCrosshairMove(param);
    });

    const resize = new ResizeObserver(() => {
      chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    resize.observe(containerRef.current);

    chartRef.current = chart;
    return () => {
      resize.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [onCrosshairMove]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    for (const series of seriesRef.current) {
      chart.removeSeries(series);
    }
    seriesRef.current = [];

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });
    seriesRef.current.push(candleSeries);

    candleSeries.setData(
      history.map((candle) => ({
        time: toUnixSeconds(candle.ts),
        open: Number(candle.open),
        high: Number(candle.high),
        low: Number(candle.low),
        close: Number(candle.close),
      }))
    );

    for (const horizon of visibleHorizons) {
      const forecast = forecastMap?.[horizon]?.points || [];
      if (!forecast.length) continue;

      const color = HORIZON_COLORS[horizon] || "#60a5fa";
      const lineSeries = chart.addLineSeries({
        color,
        lineWidth: 2,
        lineStyle: 2,
        title: `${horizon}h forecast`,
      });
      seriesRef.current.push(lineSeries);
      lineSeries.setData(
        forecast.map((point) => ({
          time: toUnixSeconds(point.ts),
          value: Number(point.pred_close),
        }))
      );

      for (const level of fanLevels) {
        const fan = buildFan(forecast, level);
        const opacity = level === 0.9 ? 0.08 : level === 0.7 ? 0.12 : 0.18;

        const upperArea = chart.addAreaSeries({
          lineColor: "rgba(0,0,0,0)",
          topColor: `rgba(56, 189, 248, ${opacity})`,
          bottomColor: "rgba(0,0,0,0)",
          priceLineVisible: false,
          lastValueVisible: false,
        });
        seriesRef.current.push(upperArea);
        upperArea.setData(fan.map((point) => ({ time: point.time, value: point.upper })));

        const lowerLine = chart.addLineSeries({
          color: `rgba(56, 189, 248, ${opacity + 0.1})`,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        seriesRef.current.push(lowerLine);
        lowerLine.setData(fan.map((point) => ({ time: point.time, value: point.lower })));
      }
    }

    const markers = [];
    const lastHistory = history[history.length - 1];
    if (lastHistory && activeForecast.length) {
      markers.push({
        time: toUnixSeconds(lastHistory.ts),
        position: "aboveBar",
        color: "#94a3b8",
        shape: "circle",
        text: "Forecast start",
      });
    }

    if (signal?.signal === "long" && lastHistory) {
      markers.push({
        time: toUnixSeconds(lastHistory.ts),
        position: "belowBar",
        color: "#22c55e",
        shape: "arrowUp",
        text: "LONG",
      });
    } else if (signal?.signal === "short" && lastHistory) {
      markers.push({
        time: toUnixSeconds(lastHistory.ts),
        position: "aboveBar",
        color: "#ef4444",
        shape: "arrowDown",
        text: "SHORT",
      });
    }

    candleSeries.setMarkers(markers);
    chart.timeScale().fitContent();
  }, [history, forecastMap, visibleHorizons, fanLevels, selectedHorizon, signal, activeForecast.length]);

  return <div className="chart-container" ref={containerRef} />;
}
