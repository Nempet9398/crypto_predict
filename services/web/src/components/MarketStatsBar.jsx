import React from "react";

function pct(val) {
  if (val == null) return "—";
  const n = Number(val);
  const sign = n >= 0 ? "+" : "";
  return sign + n.toFixed(2) + "%";
}

function price(val) {
  if (val == null) return "—";
  return Number(val).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmt(val, digits = 2) {
  if (val == null) return "—";
  return Number(val).toFixed(digits);
}

export default function MarketStatsBar({ history = [], features = null, dataStatus = null }) {
  const last = history[history.length - 1];
  const prev = history[history.length - 2];

  const currentPrice = last ? Number(last.close) : null;
  const prevClose = prev ? Number(prev.close) : null;
  const change24h = currentPrice && prevClose ? ((currentPrice - prevClose) / prevClose) * 100 : null;

  // 캔들 변화율
  const candleChange = last
    ? ((Number(last.close) - Number(last.open)) / Number(last.open)) * 100
    : null;

  const high24h = history.length
    ? Math.max(...history.slice(-96).map((b) => Number(b.high)))
    : null;
  const low24h = history.length
    ? Math.min(...history.slice(-96).map((b) => Number(b.low)))
    : null;

  const atr = features?.atr_14;
  const rsi = features?.rsi_14;
  const volRegime = features?.vol_regime;
  const vwap = features?.vwap;

  const isUp = candleChange >= 0;

  return (
    <div className="market-stats-bar">
      {/* 현재가 */}
      <div className="stat-block price-block">
        <span className="stat-label">ETH/USDT</span>
        <span className={"stat-value price " + (isUp ? "up" : "down")}>
          ${price(currentPrice)}
        </span>
        <span className={"stat-change " + (candleChange >= 0 ? "up" : "down")}>
          {pct(candleChange)}
        </span>
      </div>

      <div className="stat-divider" />

      {/* 24h 고/저 */}
      <div className="stat-block">
        <span className="stat-label">24H 고가</span>
        <span className="stat-value">${price(high24h)}</span>
      </div>
      <div className="stat-block">
        <span className="stat-label">24H 저가</span>
        <span className="stat-value">${price(low24h)}</span>
      </div>

      <div className="stat-divider" />

      {/* ATR */}
      <div className="stat-block">
        <span className="stat-label">ATR(14)</span>
        <span className="stat-value">${fmt(atr)}</span>
      </div>

      {/* RSI */}
      <div className="stat-block">
        <span className="stat-label">RSI(14)</span>
        <span className={"stat-value " + (rsi > 70 ? "down" : rsi < 30 ? "up" : "")}>
          {fmt(rsi)}
        </span>
      </div>

      {/* VWAP */}
      <div className="stat-block">
        <span className="stat-label">VWAP</span>
        <span className="stat-value">${price(vwap)}</span>
      </div>

      <div className="stat-divider" />

      {/* 변동성 레짐 */}
      <div className="stat-block">
        <span className="stat-label">변동성</span>
        <span className={"stat-value regime-badge " + (volRegime || "unknown")}>
          {volRegime === "high" ? "HIGH" : volRegime === "low" ? "LOW" : volRegime === "medium" ? "MED" : "—"}
        </span>
      </div>

      {/* 데이터 상태 */}
      {dataStatus && (
        <div className="stat-block">
          <span className="stat-label">데이터</span>
          <span className="stat-value data-status">
            {Number(dataStatus.row_count || 0).toLocaleString()}봉
            {dataStatus.gap_count > 0 && (
              <span className="gap-warn"> ({dataStatus.gap_count} gaps)</span>
            )}
          </span>
        </div>
      )}
    </div>
  );
}
