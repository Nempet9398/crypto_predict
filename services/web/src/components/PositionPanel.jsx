import React, { useState, useEffect } from "react";

function num(v, fallback = "") {
  if (v == null || v === "") return fallback;
  return Number(v).toFixed(2);
}

export default function PositionPanel({ signal = null }) {
  const [accountSize, setAccountSize] = useState("10000");
  const [leverage, setLeverage] = useState("10");
  const [riskPct, setRiskPct] = useState("1");

  // signal에서 자동 채우기
  const entry = signal?.current_close ? Number(signal.current_close).toFixed(2) : "";
  const [entryPrice, setEntryPrice] = useState(entry);
  const [tpPrice, setTpPrice] = useState("");
  const [slPrice, setSlPrice] = useState("");

  useEffect(() => {
    if (signal?.current_close) setEntryPrice(Number(signal.current_close).toFixed(2));
    if (signal?.tp_price) setTpPrice(Number(signal.tp_price).toFixed(2));
    if (signal?.sl_price) setSlPrice(Number(signal.sl_price).toFixed(2));
  }, [signal?.current_close, signal?.tp_price, signal?.sl_price]);

  // 계산
  const acc = parseFloat(accountSize) || 0;
  const lev = parseFloat(leverage) || 1;
  const risk = parseFloat(riskPct) / 100 || 0;
  const ep = parseFloat(entryPrice) || 0;
  const tp = parseFloat(tpPrice) || 0;
  const sl = parseFloat(slPrice) || 0;

  const riskAmount = acc * risk;
  const slDist = ep && sl ? Math.abs(ep - sl) : 0;
  const slPct = ep ? slDist / ep : 0;

  const positionSizeUSDT = slPct > 0 ? riskAmount / slPct : 0;
  const positionSizeETH = ep > 0 ? positionSizeUSDT / ep : 0;
  const marginRequired = positionSizeUSDT / lev;
  const tpDist = tp && ep ? Math.abs(tp - ep) : 0;
  const potentialProfit = positionSizeETH * tpDist;
  const potentialLoss = positionSizeETH * slDist;
  const rr = slDist > 0 ? tpDist / slDist : 0;
  const feePct = 0.0005; // 0.05% taker
  const fees = positionSizeUSDT * feePct * 2; // entry + exit
  const netProfit = potentialProfit - fees;
  const netLoss = potentialLoss + fees;

  // 청산가 추정 (격리 마진 기준)
  const liqDist = ep / lev * 0.9;
  const liqPrice = signal?.signal === "long"
    ? ep - liqDist
    : signal?.signal === "short"
    ? ep + liqDist
    : null;

  return (
    <div className="panel position-panel">
      <div className="panel-header">
        <span className="panel-title">포지션 계산기</span>
        {signal?.signal && signal.signal !== "no-trade" && (
          <span className={"pos-dir-badge " + signal.signal}>
            {signal.signal === "long" ? "▲ LONG" : "▼ SHORT"}
          </span>
        )}
      </div>

      <div className="pos-inputs">
        <div className="pos-input-row">
          <label>계좌 잔고 (USDT)</label>
          <input
            type="number"
            value={accountSize}
            onChange={(e) => setAccountSize(e.target.value)}
            placeholder="10000"
          />
        </div>
        <div className="pos-input-row">
          <label>레버리지</label>
          <input
            type="number"
            value={leverage}
            onChange={(e) => setLeverage(e.target.value)}
            placeholder="10"
            min="1"
            max="125"
          />
        </div>
        <div className="pos-input-row">
          <label>리스크 (%)</label>
          <input
            type="number"
            value={riskPct}
            onChange={(e) => setRiskPct(e.target.value)}
            placeholder="1"
            step="0.1"
          />
        </div>

        <div className="pos-divider" />

        <div className="pos-input-row">
          <label>진입가</label>
          <input
            type="number"
            value={entryPrice}
            onChange={(e) => setEntryPrice(e.target.value)}
            placeholder="자동"
          />
        </div>
        <div className="pos-input-row">
          <label>목표가 (TP)</label>
          <input
            type="number"
            value={tpPrice}
            onChange={(e) => setTpPrice(e.target.value)}
            placeholder="자동"
          />
        </div>
        <div className="pos-input-row">
          <label>손절가 (SL)</label>
          <input
            type="number"
            value={slPrice}
            onChange={(e) => setSlPrice(e.target.value)}
            placeholder="자동"
          />
        </div>
      </div>

      {positionSizeUSDT > 0 && (
        <div className="pos-results">
          <div className="pos-result-row">
            <span className="res-label">포지션 크기</span>
            <span className="res-value">${positionSizeUSDT.toFixed(0)} <small>({positionSizeETH.toFixed(4)} ETH)</small></span>
          </div>
          <div className="pos-result-row">
            <span className="res-label">필요 증거금</span>
            <span className="res-value">${marginRequired.toFixed(2)}</span>
          </div>
          <div className="pos-result-row up">
            <span className="res-label">예상 수익</span>
            <span className="res-value">+${netProfit.toFixed(2)}</span>
          </div>
          <div className="pos-result-row down">
            <span className="res-label">최대 손실</span>
            <span className="res-value">-${netLoss.toFixed(2)}</span>
          </div>
          <div className="pos-result-row">
            <span className="res-label">위험보상비</span>
            <span className={"res-value " + (rr >= 2 ? "up" : rr < 1 ? "down" : "")}>
              1 : {rr.toFixed(2)}
            </span>
          </div>
          <div className="pos-result-row">
            <span className="res-label">수수료 (예상)</span>
            <span className="res-value">${fees.toFixed(2)}</span>
          </div>
          {liqPrice && (
            <div className="pos-result-row down">
              <span className="res-label">추정 청산가</span>
              <span className="res-value">${liqPrice.toFixed(2)}</span>
            </div>
          )}
        </div>
      )}

      {signal?.position_size_pct > 0 && (
        <div className="kelly-hint">
          Half-Kelly 권장 크기: <strong>{signal.position_size_pct}%</strong>
        </div>
      )}
    </div>
  );
}
