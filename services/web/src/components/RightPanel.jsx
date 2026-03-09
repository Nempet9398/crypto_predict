import React from "react";

function formatNumber(value, digits = 4) {
  if (value == null || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

export default function RightPanel({
  timeframe,
  modelRegistry,
  forecastsByModel,
  modelColors,
  forecastVisibility,
  activeModelId,
}) {
  const activeForecast = activeModelId ? forecastsByModel[activeModelId] : null;
  const lastPoint = activeForecast?.points?.length ? activeForecast.points[activeForecast.points.length - 1] : null;
  const activeMeta = (modelRegistry || []).find((item) => item.id === activeModelId);

  return (
    <aside className="right-panel">
      <h3>Model Dashboard</h3>
      <div className="kv"><span>Timeframe</span><strong>{timeframe}</strong></div>
      <div className="kv"><span>Active Model</span><strong>{activeMeta?.name || "Auto"}</strong></div>
      <div className="kv"><span>Last Trained</span><strong>{activeMeta?.last_trained || "-"}</strong></div>
      <div className="kv"><span>Forecast Value</span><strong>{formatNumber(lastPoint?.pred_close, 2)}</strong></div>
      <div className="kv"><span>Band Range</span><strong>{`${formatNumber(lastPoint?.pred_low, 2)} ~ ${formatNumber(lastPoint?.pred_high, 2)}`}</strong></div>
      <div className="kv"><span>MAE / MAPE</span><strong>{`${formatNumber(activeMeta?.metrics?.mae, 4)} / ${formatNumber(activeMeta?.metrics?.mape, 2)}%`}</strong></div>

      <h4>Model Comparison</h4>
      <div className="model-table-wrap">
        <table className="model-table">
          <thead>
            <tr>
              <th>Visible</th>
              <th>Model</th>
              <th>Score</th>
              <th>MAE</th>
              <th>MAPE</th>
            </tr>
          </thead>
          <tbody>
            {(modelRegistry || []).map((model) => (
              <tr key={model.id} className={model.id === activeModelId ? "active-row" : ""}>
                <td>
                  <span className="color-chip" style={{ background: modelColors[model.id] || "#38bdf8", opacity: forecastVisibility[model.id] ? 1 : 0.3 }} />
                </td>
                <td>{model.name}</td>
                <td>{formatNumber(model.metrics?.selection_score, 3)}</td>
                <td>{formatNumber(model.metrics?.mae, 4)}</td>
                <td>{formatNumber(model.metrics?.mape, 2)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </aside>
  );
}
