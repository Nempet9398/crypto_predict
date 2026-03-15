import React, { useState } from "react";

function fmt(v, d = 3) {
  if (v == null) return "—";
  return Number(v).toFixed(d);
}

function price(v) {
  if (v == null) return "—";
  return "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function SignalHistoryPanel({ signals = [] }) {
  const [filter, setFilter] = useState("all");

  const filtered = filter === "all"
    ? signals
    : signals.filter((s) => s.signal === filter);

  return (
    <div className="panel signal-history-panel">
      <div className="panel-header">
        <span className="panel-title">신호 이력</span>
        <div className="filter-btns">
          {["all", "long", "short", "no-trade"].map((f) => (
            <button
              key={f}
              className={"filter-btn" + (filter === f ? " active" : "")}
              onClick={() => setFilter(f)}
            >
              {f === "all" ? "전체" : f.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="signal-table-wrap">
        <table className="signal-table">
          <thead>
            <tr>
              <th>시간</th>
              <th>신호</th>
              <th>스코어</th>
              <th>신뢰도</th>
              <th>진입가</th>
              <th>TP</th>
              <th>SL</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr><td colSpan={7} className="empty-row">신호 없음</td></tr>
            )}
            {filtered.map((sig, i) => (
              <tr key={i} className={"signal-row " + sig.signal}>
                <td className="sig-ts">
                  {sig.ts ? new Date(sig.ts).toLocaleString("ko-KR", {
                    month: "2-digit", day: "2-digit",
                    hour: "2-digit", minute: "2-digit",
                  }) : "—"}
                </td>
                <td>
                  <span className={"sig-badge " + sig.signal}>
                    {sig.signal === "long" ? "▲ LONG" : sig.signal === "short" ? "▼ SHORT" : "—"}
                  </span>
                </td>
                <td className={Number(sig.score) > 0 ? "up" : "down"}>
                  {fmt(sig.score)}
                </td>
                <td>{sig.confidence ? Math.round(Number(sig.confidence) * 100) + "%" : "—"}</td>
                <td>{price(sig.current_close)}</td>
                <td className="up">{price(sig.tp_price)}</td>
                <td className="down">{price(sig.sl_price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
