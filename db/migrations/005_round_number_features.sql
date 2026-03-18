-- Migration 005: 심리적 지지/저항선 피처
-- 암호화폐 트레이더들이 참조하는 100/500/1000단위 심리가격 반영

-- 연속형: 최근접 round level까지의 정규화 거리 (0=딱 붙음, 0.5=중간)
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS dist_to_round_100  NUMERIC; -- 100단위 거리
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS dist_to_round_500  NUMERIC; -- 500단위 거리
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS dist_to_round_1000 NUMERIC; -- 1000단위 거리

-- 이벤트형: 심리선 근접 플래그 (0/1)
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS near_round_100  SMALLINT; -- 100단위 ±1% 이내
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS near_round_500  SMALLINT; -- 500단위 ±1% 이내
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS near_round_1000 SMALLINT; -- 1000단위 ±1.5% 이내

-- 이벤트형: 100단위 돌파/이탈 순간 (0/1)
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS broke_round_100_up   SMALLINT; -- 100단위 상향 돌파
ALTER TABLE features.eth_features ADD COLUMN IF NOT EXISTS broke_round_100_down SMALLINT; -- 100단위 하향 이탈

COMMENT ON COLUMN features.eth_features.dist_to_round_100  IS '(close % 100) 기준 최근접 100단위까지 정규화 거리 [0, 0.5]. 0=딱 붙음';
COMMENT ON COLUMN features.eth_features.dist_to_round_500  IS '(close % 500) 기준 최근접 500단위까지 정규화 거리 [0, 0.5]';
COMMENT ON COLUMN features.eth_features.dist_to_round_1000 IS '(close % 1000) 기준 최근접 1000단위까지 정규화 거리 [0, 0.5]';
COMMENT ON COLUMN features.eth_features.near_round_100     IS '1=가격이 100단위 ±1% 이내 (예: 1980~2020)';
COMMENT ON COLUMN features.eth_features.near_round_500     IS '1=가격이 500단위 ±1% 이내 (예: 1990~2010 or 2490~2510)';
COMMENT ON COLUMN features.eth_features.near_round_1000    IS '1=가격이 1000단위 ±1.5% 이내 (예: 1970~2030) — 더 강한 심리선';
COMMENT ON COLUMN features.eth_features.broke_round_100_up   IS '1=이번 봉에서 100단위 경계를 위로 돌파 (예: 1999→2001)';
COMMENT ON COLUMN features.eth_features.broke_round_100_down IS '1=이번 봉에서 100단위 경계를 아래로 이탈 (예: 2001→1999)';
