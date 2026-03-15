import { createChart } from "lightweight-charts";
import { useEffect, useRef } from "react";

const UP_COLOR = "#26a69a";
const DOWN_COLOR = "#ef5350";
const GRID_COLOR = "rgba(255,255,255,0.05)";
const TEXT_COLOR = "#d1d5db";
const BG_COLOR = "#131722";
const BORDER_COLOR = "rgba(255,255,255,0.1)";

function toUnixTime(ts) {
  if (!ts) return null;
  return Math.floor(new Date(ts).getTime() / 1000);
}

export default function TradingChart({ history = [], signal = null, indicators = {} }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef({});

  // ── 차트 초기화 ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: BG_COLOR },
        textColor: TEXT_COLOR,
        fontSize: 12,
        fontFamily: "'Inter', 'SF Pro Display', sans-serif",
      },
      grid: {
        vertLines: { color: GRID_COLOR },
        horzLines: { color: GRID_COLOR },
      },
      crosshair: {
        mode: 1,
        vertLine: { color: "rgba(255,255,255,0.3)", width: 1, style: 3, labelBackgroundColor: "#2a2e39" },
        horzLine: { color: "rgba(255,255,255,0.3)", width: 1, style: 3, labelBackgroundColor: "#2a2e39" },
      },
      rightPriceScale: {
        borderColor: BORDER_COLOR,
        scaleMargins: { top: 0.1, bottom: 0.25 },
      },
      timeScale: {
        borderColor: BORDER_COLOR,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 5,
        barSpacing: 8,
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    });

    // 캔들스틱
    const candleSeries = chart.addCandlestickSeries({
      upColor: UP_COLOR,
      downColor: DOWN_COLOR,
      borderUpColor: UP_COLOR,
      borderDownColor: DOWN_COLOR,
      wickUpColor: UP_COLOR,
      wickDownColor: DOWN_COLOR,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });

    // 거래량
    const volumeSeries = chart.addHistogramSeries({
      color: "rgba(38, 166, 154, 0.4)",
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    seriesRef.current = { chart, candleSeries, volumeSeries };
    chartRef.current = chart;

    // ResizeObserver
    const observer = new ResizeObserver((entries) => {
      const e = entries[0];
      if (e && chart) {
        chart.applyOptions({ width: e.contentRect.width, height: e.contentRect.height });
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = {};
    };
  }, []);

  // ── 캔들 데이터 업데이트 ──────────────────────────────────────────────────
  useEffect(() => {
    const { candleSeries, volumeSeries } = seriesRef.current;
    if (!candleSeries || !history.length) return;

    const candles = history
      .map((b) => {
        const time = toUnixTime(b.ts);
        if (!time) return null;
        return { time, open: Number(b.open), high: Number(b.high), low: Number(b.low), close: Number(b.close) };
      })
      .filter(Boolean)
      .sort((a, b) => a.time - b.time);

    const volumes = history
      .map((b) => {
        const time = toUnixTime(b.ts);
        if (!time) return null;
        const isUp = Number(b.close) >= Number(b.open);
        return { time, value: Number(b.volume), color: isUp ? "rgba(38,166,154,0.5)" : "rgba(239,83,80,0.5)" };
      })
      .filter(Boolean)
      .sort((a, b) => a.time - b.time);

    try { candleSeries.setData(candles); } catch {}
    try { volumeSeries.setData(volumes); } catch {}
  }, [history]);

  // ── 지표 오버레이 ──────────────────────────────────────────────────────────
  useEffect(() => {
    const { chart } = seriesRef.current;
    if (!chart || !history.length) return;

    // 기존 지표 시리즈 제거
    for (const key of Object.keys(seriesRef.current)) {
      if (key.startsWith("ind_")) {
        try { chart.removeSeries(seriesRef.current[key]); } catch {}
        delete seriesRef.current[key];
      }
    }
    if (!indicators) return;

    const closes = history.map((b) => Number(b.close));
    const times = history.map((b) => toUnixTime(b.ts)).filter(Boolean);

    const addLine = (key, values, color, width = 1.5) => {
      const s = chart.addLineSeries({ color, lineWidth: width, crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false });
      const data = values.map((v, i) => ({ time: times[i], value: v })).filter((d) => d.time && d.value != null && !isNaN(d.value));
      try { s.setData(data); } catch {}
      seriesRef.current[key] = s;
    };

    if (indicators.ema20) addLine("ind_ema20", calcEMA(closes, 20), "#f59e0b");
    if (indicators.ema50) addLine("ind_ema50", calcEMA(closes, 50), "#8b5cf6");
    if (indicators.ema200) addLine("ind_ema200", calcEMA(closes, 200), "#3b82f6");
    if (indicators.sma20) addLine("ind_sma20", calcSMA(closes, 20), "#10b981", 1);
    if (indicators.bb) {
      const { upper, middle, lower } = calcBB(closes, 20, 2);
      addLine("ind_bb_upper", upper, "rgba(148,163,184,0.6)", 1);
      addLine("ind_bb_mid",   middle, "rgba(148,163,184,0.4)", 1);
      addLine("ind_bb_lower", lower,  "rgba(148,163,184,0.6)", 1);
    }
  }, [history, indicators]);

  // ── 신호 마커 ──────────────────────────────────────────────────────────────
  useEffect(() => {
    const { candleSeries } = seriesRef.current;
    if (!candleSeries || !signal || !history.length) return;
    const last = history[history.length - 1];
    if (!last) return;
    const time = toUnixTime(last.ts);
    if (!time) return;

    const markers = [];
    if (signal.signal === "long") {
      markers.push({ time, position: "belowBar", color: UP_COLOR, shape: "arrowUp", text: "LONG", size: 2 });
    } else if (signal.signal === "short") {
      markers.push({ time, position: "aboveBar", color: DOWN_COLOR, shape: "arrowDown", text: "SHORT", size: 2 });
    }
    try { candleSeries.setMarkers(markers); } catch {}
  }, [signal, history]);

  return <div ref={containerRef} style={{ width: "100%", height: "100%", position: "relative" }} />;
}

// ── 지표 계산 ──────────────────────────────────────────────────────────────
function calcEMA(closes, period) {
  const k = 2 / (period + 1);
  const result = new Array(closes.length).fill(null);
  let ema = null;
  for (let i = 0; i < closes.length; i++) {
    if (ema === null) {
      if (i < period - 1) continue;
      ema = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
    } else {
      ema = closes[i] * k + ema * (1 - k);
    }
    result[i] = ema;
  }
  return result;
}

function calcSMA(closes, period) {
  return closes.map((_, i) => {
    if (i < period - 1) return null;
    return closes.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0) / period;
  });
}

function calcBB(closes, period = 20, stddev = 2) {
  const sma = calcSMA(closes, period);
  const upper = closes.map((_, i) => {
    if (sma[i] == null) return null;
    const slice = closes.slice(Math.max(0, i - period + 1), i + 1);
    const variance = slice.reduce((acc, v) => acc + (v - sma[i]) ** 2, 0) / slice.length;
    return sma[i] + stddev * Math.sqrt(variance);
  });
  const lower = closes.map((_, i) => {
    if (sma[i] == null) return null;
    const slice = closes.slice(Math.max(0, i - period + 1), i + 1);
    const variance = slice.reduce((acc, v) => acc + (v - sma[i]) ** 2, 0) / slice.length;
    return sma[i] - stddev * Math.sqrt(variance);
  });
  return { upper, middle: sma, lower };
}
