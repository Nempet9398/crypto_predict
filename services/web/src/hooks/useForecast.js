import { useCallback, useState } from "react";

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = body?.detail ? `: ${body.detail}` : "";
    } catch {
      // ignore
    }
    throw new Error(`API error (${response.status})${detail}`);
  }
  return response.json();
}

export function useForecast(apiUrl) {
  const [history, setHistory] = useState([]);
  const [forecastMap, setForecastMap] = useState({});
  const [signal, setSignal] = useState(null);
  const [backtest, setBacktest] = useState(null);
  const [dataStatus, setDataStatus] = useState(null);
  const [modelStatus, setModelStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadCore = useCallback(
    async ({ start, end, tz, horizons, baseHorizon, upPct, downPct, upThreshold, downThreshold }) => {
      setLoading(true);
      setError("");
      try {
        const params = new URLSearchParams({ tz, limit: "1200" });
        if (start) params.set("start", start);
        if (end) params.set("end", end);

        const [statusRes, historyRes, modelRes] = await Promise.all([
          fetchJson(`${apiUrl}/data/status?tz=${encodeURIComponent(tz)}`),
          fetchJson(`${apiUrl}/prices/history?${params.toString()}`),
          fetchJson(`${apiUrl}/model/status`),
        ]);

        setDataStatus(statusRes);
        setHistory(historyRes.points || []);
        setModelStatus(modelRes);

        const overlayForecasts = await Promise.all(
          horizons.map((h) =>
            fetchJson(`${apiUrl}/forecast?horizon_hours=${h}&include_history_tail=72&tz=${encodeURIComponent(tz)}`)
              .then((result) => ({ horizon: h, ok: true, result }))
              .catch((e) => ({ horizon: h, ok: false, error: e.message }))
          )
        );

        const mapped = {};
        for (const item of overlayForecasts) {
          mapped[item.horizon] = item.ok ? item.result : { points: [], history_tail: [], error: item.error };
        }
        setForecastMap(mapped);

        try {
          const signalRes = await fetchJson(
            `${apiUrl}/signals?horizon_hours=${baseHorizon}&up_pct=${upPct}&down_pct=${downPct}&up_prob_threshold=${upThreshold}&down_prob_threshold=${downThreshold}&tz=${encodeURIComponent(tz)}`
          );
          setSignal(signalRes);
        } catch (signalError) {
          setSignal({ error: signalError.message });
        }
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    },
    [apiUrl]
  );

  const train = useCallback(
    async (payload) => {
      setError("");
      return fetchJson(`${apiUrl}/train`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    },
    [apiUrl]
  );

  const runBacktest = useCallback(
    async ({ lookbackDays, horizonHours, upThreshold, downThreshold, upPct, downPct, feeBps, slippageBps, tz }) => {
      setError("");
      try {
        const query = new URLSearchParams({
          lookback_days: String(lookbackDays),
          horizon_hours: String(horizonHours),
          up_prob_threshold: String(upThreshold),
          down_prob_threshold: String(downThreshold),
          up_pct: String(upPct),
          down_pct: String(downPct),
          fee_bps: String(feeBps),
          slippage_bps: String(slippageBps),
          tz,
        });
        const result = await fetchJson(`${apiUrl}/backtest/strategy?${query.toString()}`);
        setBacktest(result);
        return result;
      } catch (e) {
        setError(e.message);
        throw e;
      }
    },
    [apiUrl]
  );

  return {
    history,
    forecastMap,
    signal,
    backtest,
    dataStatus,
    modelStatus,
    loading,
    error,
    loadCore,
    train,
    runBacktest,
  };
}
