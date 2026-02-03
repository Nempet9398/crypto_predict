import React, { useEffect, useMemo, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

function buildPath(points, width, height, padding) {
  if (points.length === 0) return "";
  const closes = points.map((p) => p.close);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1;
  return points
    .map((p, idx) => {
      const x = padding + (idx / (points.length - 1)) * (width - padding * 2);
      const y = padding + ((max - p.close) / span) * (height - padding * 2);
      return `${idx === 0 ? "M" : "L"}${x} ${y}`;
    })
    .join(" ");
}

export default function App() {
  const [history, setHistory] = useState([]);
  const [latest, setLatest] = useState(null);
  const [features, setFeatures] = useState(null);
  const [prediction, setPrediction] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [historyRes, latestRes, featuresRes, predictRes] = await Promise.all([
          fetch(`${API_URL}/prices/history?limit=100`),
          fetch(`${API_URL}/prices/latest`),
          fetch(`${API_URL}/features/latest`),
          fetch(`${API_URL}/predict`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({})
          })
        ]);

        if (!historyRes.ok || !latestRes.ok || !featuresRes.ok || !predictRes.ok) {
          throw new Error("API not ready");
        }

        const historyData = await historyRes.json();
        const latestData = await latestRes.json();
        const featureData = await featuresRes.json();
        const predictData = await predictRes.json();

        setHistory(historyData.points || []);
        setLatest(latestData.price);
        setFeatures(featureData.features);
        setPrediction(predictData.predicted_close);
      } catch (err) {
        setError(err.message);
      }
    };

    load();
  }, []);

  const chartPath = useMemo(() => buildPath(history, 600, 240, 16), [history]);

  return (
    <div className="app">
      <header className="header">
        <h1>Ethereum Data Pipeline</h1>
        <p>ETH/USDT 1h data served from FastAPI</p>
      </header>

      {error && <div className="error">{error}</div>}

      <section className="card">
        <h2>Price Chart</h2>
        <svg width="100%" height="240" viewBox="0 0 600 240" className="chart">
          <path d={chartPath} fill="none" stroke="#1976d2" strokeWidth="2" />
        </svg>
        {latest && (
          <div className="latest">
            Latest close: <strong>{Number(latest.close).toFixed(2)}</strong>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Features</h2>
        {features ? (
          <table>
            <thead>
              <tr>
                <th>returns_1h</th>
                <th>sma_5</th>
                <th>sma_10</th>
                <th>ema_10</th>
                <th>volatility_10</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{Number(features.returns_1h).toFixed(6)}</td>
                <td>{Number(features.sma_5).toFixed(2)}</td>
                <td>{Number(features.sma_10).toFixed(2)}</td>
                <td>{Number(features.ema_10).toFixed(2)}</td>
                <td>{Number(features.volatility_10).toFixed(6)}</td>
              </tr>
            </tbody>
          </table>
        ) : (
          <p>No features available.</p>
        )}
      </section>

      <section className="card">
        <h2>Prediction</h2>
        {prediction !== null ? (
          <div className="prediction">Predicted next close: {Number(prediction).toFixed(2)}</div>
        ) : (
          <p>No prediction available.</p>
        )}
      </section>
    </div>
  );
}
