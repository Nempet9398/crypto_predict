import React from "react";

const INDICATOR_OPTIONS = [
  { key: "ema20", label: "EMA 20" },
  { key: "ema50", label: "EMA 50" },
  { key: "ema200", label: "EMA 200" },
  { key: "sma20", label: "SMA 20" },
  { key: "bb", label: "Bollinger Bands" },
];

export default function IndicatorManagerModal({ open, indicators = {}, onChange, onClose }) {
  if (!open) return null;
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <span>차트 지표 설정</span>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          {INDICATOR_OPTIONS.map(({ key, label }) => (
            <label key={key} className="indicator-toggle">
              <input
                type="checkbox"
                checked={!!indicators[key]}
                onChange={(e) => onChange?.({ ...indicators, [key]: e.target.checked })}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}
