import React, { useMemo, useState } from "react";

const TYPES = ["EMA", "SMA", "RSI", "MACD", "BB"];

function defaultParamsFor(type) {
  if (type === "MACD") return { fast: 12, slow: 26, signal: 9 };
  if (type === "BB") return { period: 20, stddev: 2 };
  return { period: 14 };
}

export default function IndicatorManagerModal({
  open,
  indicators,
  onClose,
  onAdd,
  onRemove,
  onToggle,
  onUpdate,
}) {
  const [newType, setNewType] = useState("EMA");
  const [newParams, setNewParams] = useState(defaultParamsFor("EMA"));

  const previewLabel = useMemo(() => {
    if (newType === "MACD") return `fast=${newParams.fast}, slow=${newParams.slow}, signal=${newParams.signal}`;
    if (newType === "BB") return `period=${newParams.period}, stddev=${newParams.stddev}`;
    return `period=${newParams.period}`;
  }, [newType, newParams]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <h3>Indicator Manager</h3>
          <button onClick={onClose}>Close</button>
        </div>

        <div className="modal-add">
          <select
            value={newType}
            onChange={(event) => {
              const type = event.target.value;
              setNewType(type);
              setNewParams(defaultParamsFor(type));
            }}
          >
            {TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>

          {newType === "MACD" ? (
            <>
              <input
                type="number"
                min="2"
                value={newParams.fast}
                onChange={(event) => setNewParams((prev) => ({ ...prev, fast: Number(event.target.value) || 12 }))}
                placeholder="fast"
              />
              <input
                type="number"
                min="2"
                value={newParams.slow}
                onChange={(event) => setNewParams((prev) => ({ ...prev, slow: Number(event.target.value) || 26 }))}
                placeholder="slow"
              />
              <input
                type="number"
                min="2"
                value={newParams.signal}
                onChange={(event) => setNewParams((prev) => ({ ...prev, signal: Number(event.target.value) || 9 }))}
                placeholder="signal"
              />
            </>
          ) : (
            <input
              type="number"
              min="2"
              step={newType === "BB" ? "0.1" : "1"}
              value={newType === "BB" ? newParams.stddev : newParams.period}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (newType === "BB") {
                  setNewParams((prev) => ({ ...prev, stddev: value || 2 }));
                } else {
                  setNewParams((prev) => ({ ...prev, period: value || 14 }));
                }
              }}
              placeholder={newType === "BB" ? "stddev" : "period"}
            />
          )}

          {newType === "BB" ? (
            <input
              type="number"
              min="2"
              value={newParams.period}
              onChange={(event) => setNewParams((prev) => ({ ...prev, period: Number(event.target.value) || 20 }))}
              placeholder="period"
            />
          ) : null}

          <button onClick={() => onAdd(newType, newParams)}>Add</button>
          <span className="preview">{previewLabel}</span>
        </div>

        <div className="modal-list">
          {indicators.map((indicator) => (
            <div className="indicator-row" key={indicator.id}>
              <label className="toggle">
                <input type="checkbox" checked={indicator.enabled} onChange={() => onToggle(indicator.id)} />
                <span>{indicator.type}</span>
              </label>

              {"period" in (indicator.params || {}) ? (
                <input
                  type="number"
                  min="2"
                  value={indicator.params.period}
                  onChange={(event) =>
                    onUpdate(indicator.id, {
                      params: { period: Number(event.target.value) || 14 },
                    })
                  }
                />
              ) : null}

              {"fast" in (indicator.params || {}) ? (
                <input
                  type="number"
                  min="2"
                  value={indicator.params.fast}
                  onChange={(event) =>
                    onUpdate(indicator.id, {
                      params: { fast: Number(event.target.value) || 12 },
                    })
                  }
                />
              ) : null}

              {"slow" in (indicator.params || {}) ? (
                <input
                  type="number"
                  min="2"
                  value={indicator.params.slow}
                  onChange={(event) =>
                    onUpdate(indicator.id, {
                      params: { slow: Number(event.target.value) || 26 },
                    })
                  }
                />
              ) : null}

              {"signal" in (indicator.params || {}) ? (
                <input
                  type="number"
                  min="2"
                  value={indicator.params.signal}
                  onChange={(event) =>
                    onUpdate(indicator.id, {
                      params: { signal: Number(event.target.value) || 9 },
                    })
                  }
                />
              ) : null}

              {"stddev" in (indicator.params || {}) ? (
                <input
                  type="number"
                  min="0.5"
                  step="0.1"
                  value={indicator.params.stddev}
                  onChange={(event) =>
                    onUpdate(indicator.id, {
                      params: { stddev: Number(event.target.value) || 2 },
                    })
                  }
                />
              ) : null}

              <input
                type="color"
                value={indicator.color}
                onChange={(event) => onUpdate(indicator.id, { color: event.target.value })}
              />

              <button className="danger" onClick={() => onRemove(indicator.id)}>
                Remove
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
