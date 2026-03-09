export function calculateSMA(values, period) {
  if (!Array.isArray(values) || !values.length || period <= 1) return [];
  const result = new Array(values.length).fill(null);
  let rollingSum = 0;
  for (let index = 0; index < values.length; index += 1) {
    const value = Number(values[index]);
    rollingSum += value;
    if (index >= period) {
      rollingSum -= Number(values[index - period]);
    }
    if (index >= period - 1) {
      result[index] = rollingSum / period;
    }
  }
  return result;
}

export function calculateEMA(values, period) {
  if (!Array.isArray(values) || values.length === 0 || period <= 1) return [];
  const multiplier = 2 / (period + 1);
  const result = new Array(values.length).fill(null);
  let seedSum = 0;

  for (let index = 0; index < values.length; index += 1) {
    const value = Number(values[index]);
    if (!Number.isFinite(value)) continue;

    if (index < period) {
      seedSum += value;
      if (index === period - 1) {
        result[index] = seedSum / period;
      }
      continue;
    }

    result[index] = (value - result[index - 1]) * multiplier + result[index - 1];
  }

  return result;
}

export function calculateRSI(values, period = 14) {
  if (!Array.isArray(values) || values.length < period + 1) return new Array(values.length).fill(null);
  const result = new Array(values.length).fill(null);
  let gainSum = 0;
  let lossSum = 0;

  for (let index = 1; index <= period; index += 1) {
    const delta = Number(values[index]) - Number(values[index - 1]);
    gainSum += Math.max(delta, 0);
    lossSum += Math.max(-delta, 0);
  }

  let avgGain = gainSum / period;
  let avgLoss = lossSum / period;
  result[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  for (let index = period + 1; index < values.length; index += 1) {
    const delta = Number(values[index]) - Number(values[index - 1]);
    const gain = Math.max(delta, 0);
    const loss = Math.max(-delta, 0);
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    result[index] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }

  return result;
}

export function calculateMACD(values, fast = 12, slow = 26, signal = 9) {
  const fastEma = calculateEMA(values, fast);
  const slowEma = calculateEMA(values, slow);
  const macdLine = values.map((_, index) => {
    if (fastEma[index] == null || slowEma[index] == null) return null;
    return fastEma[index] - slowEma[index];
  });

  const compactMacd = macdLine.map((value) => (value == null ? 0 : value));
  const signalRaw = calculateEMA(compactMacd, signal);
  const signalLine = macdLine.map((value, index) => (value == null || signalRaw[index] == null ? null : signalRaw[index]));
  const histogram = macdLine.map((value, index) => (value == null || signalLine[index] == null ? null : value - signalLine[index]));
  return { macdLine, signalLine, histogram };
}

export function calculateBollinger(values, period = 20, stddev = 2) {
  const middle = calculateSMA(values, period);
  const upper = new Array(values.length).fill(null);
  const lower = new Array(values.length).fill(null);

  for (let index = period - 1; index < values.length; index += 1) {
    const window = values.slice(index - period + 1, index + 1).map((value) => Number(value));
    const mean = middle[index];
    if (mean == null) continue;
    const variance = window.reduce((sum, value) => sum + (value - mean) ** 2, 0) / period;
    const deviation = Math.sqrt(variance);
    upper[index] = mean + stddev * deviation;
    lower[index] = mean - stddev * deviation;
  }

  return { middle, upper, lower };
}
