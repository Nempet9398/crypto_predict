-- Migration 004: 피처 정교화 v2
-- 원칙 1 (이벤트 피처), 원칙 2 (방향성 피처), 원칙 3 (5단계 MarketRegime)
-- ADX, 방향성 피처, 크로스오버 이벤트, 5단계 시장 국면 추가

-- ADX: 추세 강도 (MarketRegime 5단계 분류에 사용)
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS adx NUMERIC;

-- 원칙 2: 방향성 피처 (이전 봉 값 → 방향성 학습)
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS rsi_prev       NUMERIC;
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS macd_hist_prev NUMERIC;
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS atr_pct_prev   NUMERIC;
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS bb_pct_prev    NUMERIC;

-- 원칙 1: 이벤트 피처 (0/1 이진 — 임계점 돌파 순간)
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS macd_golden_cross      SMALLINT; -- MACD > Signal 상향 돌파
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS macd_dead_cross        SMALLINT; -- MACD < Signal 하향 돌파
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS stoch_golden_cross     SMALLINT; -- StochK > StochD 돌파
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS stoch_dead_cross       SMALLINT; -- StochK < StochD 이탈
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS rsi_cross_30_up        SMALLINT; -- RSI 30선 상향 돌파 (과매도 탈출)
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS rsi_cross_70_down      SMALLINT; -- RSI 70선 하향 돌파 (과매수 이탈)
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS volume_above_avg       SMALLINT; -- 거래량 > 20일 평균
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS ma_regular_arrangement SMALLINT; -- EMA20 > EMA50 > EMA200 정배열

-- 원칙 3: 시장 국면 정교화
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS market_regime_detail TEXT;     -- mania/bullish/sideways/bearish/panic
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS shock_detected       SMALLINT; -- 급락 + 거래량 폭증 감지 (0/1)

-- 인덱스: market_regime_detail 기반 쿼리 성능
CREATE INDEX IF NOT EXISTS idx_eth_features_regime
    ON features.eth_features (exchange, symbol, market_regime_detail, ts DESC)
    WHERE market_regime_detail IS NOT NULL;

COMMENT ON COLUMN features.eth_features.adx                    IS 'ADX(14): 추세 강도 (25+ 강세, 50+ 극강, 25- 횡보)';
COMMENT ON COLUMN features.eth_features.rsi_prev               IS '이전 봉 RSI(14): 방향성 피처';
COMMENT ON COLUMN features.eth_features.macd_hist_prev         IS '이전 봉 MACD 히스토그램: 모멘텀 방향 피처';
COMMENT ON COLUMN features.eth_features.atr_pct_prev           IS '이전 봉 ATR%: 변동성 방향 피처';
COMMENT ON COLUMN features.eth_features.bb_pct_prev            IS '이전 봉 BB %B: 밴드 위치 변화 피처';
COMMENT ON COLUMN features.eth_features.macd_golden_cross      IS '1=MACD가 Signal을 상향 돌파한 봉';
COMMENT ON COLUMN features.eth_features.macd_dead_cross        IS '1=MACD가 Signal을 하향 돌파한 봉';
COMMENT ON COLUMN features.eth_features.stoch_golden_cross     IS '1=Stoch K가 D를 상향 돌파한 봉';
COMMENT ON COLUMN features.eth_features.stoch_dead_cross       IS '1=Stoch K가 D를 하향 돌파한 봉';
COMMENT ON COLUMN features.eth_features.rsi_cross_30_up        IS '1=RSI가 30선을 상향 돌파 (과매도 탈출)';
COMMENT ON COLUMN features.eth_features.rsi_cross_70_down      IS '1=RSI가 70선을 하향 돌파 (과매수 이탈)';
COMMENT ON COLUMN features.eth_features.volume_above_avg       IS '1=거래량이 20일 평균 초과';
COMMENT ON COLUMN features.eth_features.ma_regular_arrangement IS '1=EMA20>EMA50>EMA200 정배열';
COMMENT ON COLUMN features.eth_features.market_regime_detail   IS '5단계 시장 국면: mania/bullish/sideways/bearish/panic';
COMMENT ON COLUMN features.eth_features.shock_detected         IS '1=급락(-3%↓)+거래량 폭증(200%↑) 동시 감지';
