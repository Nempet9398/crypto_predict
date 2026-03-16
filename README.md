# ETH Trading Dashboard

> Binance 15분 캔들 기반 이더리움 트레이딩 대시보드.
> 기술지표 + ML 분류/회귀 앙상블로 LONG/SHORT 신호와 수익률 예측 구간(CI), ATR 기반 TP/SL을 제공합니다.
> 실제 주문 집행 없이 트레이딩 의사결정 지원용입니다.

---

## 전체 흐름

```
Binance 15m OHLCV
        ↓
  raw.eth_ohlcv (PostgreSQL)
        ↓ resample
  processed.eth_ohlcv_1h
        ↓ 기술지표 계산 (incremental HWM)
  features.eth_features
  (RSI, MACD, BB, ATR, OBV, VWAP, Stoch, vol_regime, signal_1h/4h,
   target_direction, target_return_1h/3h/6h)
        ↓
  ┌─────────────────────┐   ┌──────────────────────────────┐
  │  기술지표 합성 스코어  │   │  ML 예측 모델                 │
  │  RSI/MACD/BB/Stoch  │   │  ① 분류: 방향 확률 (XGB/LGB)  │
  │  EMA 정렬            │   │  ② 회귀: 수익률 + CI (XGB/LGB)│
  │  35% 가중치          │   │     1h/3h/6h 분위수 회귀      │
  └──────────┬──────────┘   └──────────────┬───────────────┘
             │                             │
             └──────── 앙상블 스코어 ───────┘
                   + MTF 1h 13% / 4h 7%
                         ↓
          최종 신호: LONG / SHORT / NO-TRADE
          + ATR 기반 TP/SL (2×ATR / 1×ATR)
          + Half-Kelly 포지션 사이징
          + CI 폭 기반 신뢰도 조정
                         ↓
          FastAPI → React 대시보드
```

---

## 주요 기능

### 신호 생성 (`signal_service.py`)

| 구성요소 | 내용 | 가중치 |
|----------|------|--------|
| 기술지표 합성 스코어 | RSI(14), MACD 히스토그램, Bollinger Band %B, Stochastic %K, EMA 정렬 | 35% |
| ML 회귀 스코어 | XGBoost/LightGBM 1h 예상 수익률 기반, CI 폭으로 dampening | 45% |
| MTF 1h 신호 | 1h 봉 EMA20 vs EMA50 크로스오버 방향 | 13% |
| MTF 4h 신호 | 4h 봉 EMA20 vs EMA50 크로스오버 방향 | 7% |

- **변동성 레짐 필터**: ATR% 30일 퍼센타일로 low/medium/high 분류. high 레짐 시 임계값 자동 강화
- **ATR 기반 TP/SL**: TP = 진입가 ± 2×ATR14, SL = ± 1×ATR14, RR = 2.0
- **Half-Kelly 포지션 사이징**: 승률 기반 Kelly formula × 0.5, 최대 25% cap

### ML 회귀 예측 (`train_ml_regressor.py` + `ml_regressor_service.py`)

- **대상 변수**: 1h, 3h, 6h 후 수익률 (float)
  - `target_return_1h = (close[t+4] - close[t]) / close[t]`
  - `target_return_3h = (close[t+12] - close[t]) / close[t]`
  - `target_return_6h = (close[t+24] - close[t]) / close[t]`
- **분위수 회귀**: XGBoost `reg:quantileerror`, LightGBM `quantile` (q = 0.1, 0.5, 0.9)
  - 중앙값(q=0.5) = 예측 수익률, [q=0.1, q=0.9] = 신뢰구간(CI)
  - CI 폭이 좁을수록 예측 신뢰도 높음 → 앙상블 스코어 강화
- **Walk-forward CV**: TimeSeriesSplit 5-fold, MAE 기준 최적 알고리즘 선택
- **품질 게이트**: `cv_dir_acc > 0.50` (랜덤 대비 방향 정확도) 만족 시에만 active 승격
- **피처셋**: RSI, MACD, BB, ATR%, Stochastic, lag returns(1~8봉), hour/dow cyclic encoding

### ML 분류 예측 (`train_ml_classifier.py` / `train_ml_predictor.py`)

- XGBoost / LightGBM 방향 분류 (up / down / neutral)
- 결과: `ml_prob_up`, `ml_prob_down` → 앙상블 보조 스코어

### 기술지표 파이프라인 (`technical_indicators.py`)

| 지표 | 구현 방식 |
|------|-----------|
| RSI(14) | Wilder's smoothing EWM |
| MACD(12,26,9) | EMA 차이 + signal line |
| Bollinger Bands(20, 2σ) | rolling mean ± 2σ, %B, width |
| ATR(14) | True Range EWM |
| OBV | 거래량 방향 누적 |
| VWAP | 96봉(24h) rolling TP×volume |
| Stochastic %K/%D(14, 3) | rolling min/max |
| Vol Regime | ATR% 30일 퍼센타일 → low/medium/high |
| MTF 1h/4h | pandas resample + EMA20 vs EMA50 |

- **Incremental HWM 모드** (`--incremental`): `pipeline.water_marks` 테이블의 `last_ts`를 읽어 신규 행만 처리. WARMUP_BARS=2880(30일) 버퍼로 rolling 지표 정확도 보장

### 백테스트 (`BacktestPanel.jsx` + `/backtest/strategy`)

- Lookback 7/14/30/60일, Horizon 1/3/6/12/24h 설정
- 결과 지표: Sharpe, Sortino, Calmar, Profit Factor, Win Rate, Payoff Ratio, 누적 PnL, Max Drawdown, MDD 기간, 거래 횟수
- 누적 PnL 스파크라인 차트

### 예측 정확도 추적 (`AccuracyPanel`)

- 예측값 + 실제 결과 DB 저장 (`features.ml_predictions`)
- 기간 / 예측 범위(1h, 4h, 8h) 설정 가능
- LONG / SHORT 방향별 정확도 분리 표시

---

## Airflow DAG 구성

| DAG | 스케줄 | 설명 |
|-----|--------|------|
| `ingestion_dag` | 5분 | Binance 15m OHLCV 수집 (갭 채움 포함) |
| `feature_pipeline_dag` | 매시간 | resample → 기술지표(incremental) → [ARIMA, ML분류, ML회귀] → 앙상블 신호 |
| `ml_retrain_dag` | 4시간 | ML 분류 + 회귀 모델 재학습 → 품질 게이트 → active 승격 |
| `data_quality_dag` | 30분 | 데이터 신선도·갭·피처 지연 자동 체크 |
| `prediction_actualize_dag` | 매시간 | 과거 예측의 `actual_return` 자동 채움 (백테스트 데이터 누적) |

### `feature_pipeline_dag` 태스크 체인

```
ingest_ohlcv
    → resample_1h
    → generate_features (--incremental)
    → [train_ml_classifier, train_ml_regressor, train_arima_model]
    → compute_ensemble_signals
```

### `ml_retrain_dag` 태스크 체인

```
[train_ml_classifier, train_ml_regressor]
    → validate_model_quality  (cv_dir_acc > 0.50 체크)
    → promote_if_quality_ok   (통과 모델만 is_active=TRUE)
```

### `data_quality_dag` 체크 항목

- **freshness**: `raw.eth_ohlcv` 최신 ts가 30분 이상 오래됐으면 경고
- **feature_lag**: `features.eth_features` vs raw 시간 차이 2h 이상 시 경고
- **gap_count**: 최근 24h 내 누락된 15m 슬롯 3개 초과 시 경고

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| 데이터 수집 | Python, CCXT (Binance) |
| 파이프라인 스케줄 | Apache Airflow |
| DB | PostgreSQL 15 |
| 백엔드 API | FastAPI |
| ML | XGBoost, LightGBM, scikit-learn, joblib |
| 프론트엔드 | React 18, lightweight-charts |
| 인프라 | Docker Compose |

---

## 실행

### 환경변수 설정

```bash
cp .env.example .env
# .env 수정: DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, EXCHANGE, SYMBOL, VITE_API_URL
```

### 실행

```bash
docker compose up --build
```

| 서비스 | 주소 |
|--------|------|
| 대시보드 | http://localhost:3000 |
| API Docs | http://localhost:8000/docs |
| Airflow | http://localhost:8080 |

---

## API 주요 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/signals/current` | 현재 신호 (LONG/SHORT/NO-TRADE + ML 회귀 수익률) |
| GET | `/signals/history` | 과거 신호 목록 |
| POST | `/ml/train` | ML 모델 학습 트리거 |
| GET | `/ml/status` | ML 모델 상태 |
| GET | `/ml/accuracy` | 예측 정확도 (기간 설정) |
| GET | `/ml/predictions` | ML 예측 이력 |
| GET | `/prices/history` | 캔들스틱 히스토리 |
| GET | `/prices/latest` | 최신 캔들 |
| GET | `/features/latest` | 현재 기술지표 값 |
| GET | `/data/status` | 데이터 수집 상태 |
| GET | `/backtest/strategy` | 백테스트 실행 |

---

## DB 스키마

| 테이블 | 설명 |
|--------|------|
| `raw.eth_ohlcv` | Binance 15m 원본 OHLCV 캔들 |
| `processed.eth_ohlcv_1h` | 1h 리샘플 데이터 |
| `features.eth_features` | 15m 단위 기술지표 (RSI, MACD, ATR 등 30+ 컬럼) |
| `features.signals` | 생성된 매매 신호 이력 (signal, score, tp/sl, ml 수익률 포함) |
| `features.ml_predictions` | ML 예측값 + 실제값 (정확도 추적) |
| `features.ml_model_registry` | XGBoost/LightGBM 모델 버전 관리 (is_active, metrics) |
| `features.ensemble_signals` | 앙상블 신호 저장 + actual_return 자동 채움 |
| `pipeline.water_marks` | 각 파이프라인 단계의 High Water Mark (증분 처리용) |

---

## 프로젝트 구조

```
crypto_predict/
├── airflow/
│   └── dags/
│       ├── ingestion_dag.py          # 5분 주기 데이터 수집
│       ├── feature_pipeline_dag.py   # 매시간 피처 + ML 학습
│       ├── ml_retrain_dag.py         # 4시간 ML 재학습 + 품질 게이트
│       ├── data_quality_dag.py       # 30분 데이터 품질 모니터링
│       └── prediction_actualize_dag.py # 매시간 예측 결과 자동 채움
├── db/
│   ├── schema.sql                    # 전체 DB 스키마
│   └── migrations/
│       ├── 001_extended_features.sql # 확장 피처 컬럼
│       └── 002_pipeline_watermarks.sql # HWM 테이블 + actual_return
├── ml/
│   ├── train_ml_classifier.py        # ML 분류 모델 학습
│   ├── train_ml_predictor.py         # ML 예측 모델 학습
│   ├── train_ml_regressor.py         # ML 회귀 모델 학습 (분위수, 1h/3h/6h)
│   ├── ml_predictor_service.py       # 분류 모델 서비스
│   ├── ml_regressor_service.py       # 회귀 모델 서비스 (CI 포함)
│   └── compute_ensemble_signals.py   # 앙상블 신호 사전 계산
├── pipelines/
│   ├── ingestion/binance_ohlcv.py    # Binance OHLCV 수집
│   ├── processing/resample.py        # 1h 리샘플
│   └── features/technical_indicators.py # 기술지표 계산 (incremental 지원)
├── services/
│   ├── api/                          # FastAPI 백엔드
│   └── web/                          # React 프론트엔드
└── docker-compose.yml
```
