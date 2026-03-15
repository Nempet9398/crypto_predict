# crypto_predict — ETH 선물 거래 의사결정 대시보드

> Binance 15분 캔들을 기반으로 기술 지표, 머신러닝, ARIMA 예측을 앙상블해 **LONG/SHORT 신호**와 **동적 TP/SL**을 자동으로 생성하는 풀스택 트레이딩 대시보드입니다.
> **실제 주문 집행 없이** 분석·의사결정 지원에 집중합니다.

---

## 전체 흐름

```
Binance OHLCV (15m)
        ↓
  PostgreSQL 저장 (raw.eth_ohlcv)
        ↓
  SQL 리샘플 → 1h, 4h 파생
        ↓
  기술 지표 계산 (RSI, MACD, BB, ATR, OBV, VWAP, Stochastic ...)
        ↓
  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
  │  ARIMA 예측     │    │  ML 분류 모델    │    │ 멀티타임프레임   │
  │  30% 가중치     │    │  40% (XGB/LGB)  │    │  30% (1h, 4h)   │
  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘
           └──────────────── 앙상블 ──────────────────────┘
                              ↓
               최종 신호: LONG / SHORT / NO-TRADE
               + ATR 기반 동적 TP / SL
               + Half-Kelly 포지션 사이징
                              ↓
               FastAPI → React 대시보드
```

---

## 대시보드 레이아웃

```
┌─────────────────────────────────────────────────────────────────┐
│ TopBar: 심볼 / 타임프레임 / 모델 선택 / 학습 / 자동갱신          │
├─────────────────────────────────────────────────────────────────┤
│ MarketStatsBar: 현재가 · 24h변화 · 고/저 · ATR · 변동성레짐     │
├───────────────────────────────────┬─────────────────────────────┤
│                                   │ SignalPanel                 │
│  TradingChart                     │ - 앙상블 신호 (L/S/NT)      │
│  - 캔들스틱 + EMA/SMA/BB          │ - TP/SL 가격               │
│  - 예측선 + 신뢰구간 밴드          │ - Kelly 포지션 %            │
│  - 과거 신호 마커 ▲▼              ├─────────────────────────────┤
│                                   │ MultiTimeframePanel         │
│                                   │ - 15m / 1h / 4h 신호 정렬  │
│                                   ├─────────────────────────────┤
│                                   │ TechnicalSummary            │
│                                   │ - RSI / MACD / BB / Stoch  │
│                                   ├─────────────────────────────┤
│                                   │ RightPanel                  │
│                                   │ - 모델 예측값 비교           │
├───────────────────────────────────┴─────────────────────────────┤
│ 하단 탭: [백테스트] [신호히스토리] [리스크계산기] [모델&데이터]   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 디렉토리 구조

```
crypto_predict/
├── airflow/
│   └── dags/
│       ├── ingestion_dag.py          # 5분마다 Binance 데이터 수집
│       ├── feature_pipeline_dag.py   # 매시간 기술 지표 계산
│       └── ml_retrain_dag.py         # 4시간마다 ML 모델 재학습
├── db/
│   └── schema.sql                    # 테이블 정의 (초기 생성 전용)
├── ml/
│   ├── train_arima.py                # ARIMA 학습
│   ├── train_ml_classifier.py        # XGBoost / LightGBM 학습 및 선택
│   ├── train.py                      # 통합 학습 엔트리포인트
│   ├── compute_ensemble_signals.py   # 앙상블 신호 계산
│   └── ml_classifier_service.py      # ML 예측 서비스
├── pipelines/
│   ├── ingestion/
│   │   └── binance_ohlcv.py          # Binance ccxt 수집
│   ├── features/
│   │   └── technical_indicators.py   # 기술 지표 계산
│   └── processing/
│       └── resample.py               # DB 내 타임프레임 리샘플
├── services/
│   ├── api/
│   │   └── app/
│   │       ├── core/
│   │       │   ├── db.py                 # DB 연결 유틸
│   │       │   ├── migrations.py         # 시작 시 자동 마이그레이션
│   │       │   ├── arima_service.py      # ARIMA 예측 서비스
│   │       │   ├── ensemble_service.py   # 앙상블 신호 생성 + DB 저장
│   │       │   ├── ohlcv.py              # OHLCV 데이터 조회
│   │       │   └── model.py              # 모델 상태 관리
│   │       ├── routers/
│   │       │   ├── prices.py             # 가격 API
│   │       │   ├── model.py              # 학습/예측 API
│   │       │   ├── features.py           # 기술지표 최신값 API
│   │       │   ├── signals_history.py    # 신호 히스토리 API
│   │       │   ├── data.py               # 데이터 상태 API
│   │       │   └── health.py             # 헬스체크
│   │       └── main.py                   # FastAPI 앱 + 자동 마이그레이션
│   └── web/
│       └── src/
│           ├── components/
│           │   ├── TradingChart.jsx          # 캔들차트 + 신호 마커 ▲▼
│           │   ├── MarketStatsBar.jsx         # 현재가 · ATR · 변동성 레짐
│           │   ├── SignalPanel.jsx            # 앙상블 신호 패널
│           │   ├── MultiTimeframePanel.jsx    # 15m/1h/4h 신호 정렬
│           │   ├── TechnicalSummary.jsx       # RSI/MACD/BB/Stoch 요약
│           │   ├── RightPanel.jsx             # 모델 예측 비교
│           │   ├── BacktestPanel.jsx          # 백테스트 P&L 차트
│           │   ├── SignalHistoryPanel.jsx      # 과거 신호 테이블
│           │   ├── RiskCalculator.jsx         # 선물 리스크 계산기
│           │   ├── ModelDataStatusPanel.jsx   # 모델·데이터 상태
│           │   ├── BottomTabs.jsx             # 하단 탭 컨테이너
│           │   ├── TopBar.jsx                 # 상단 설정 바
│           │   └── IndicatorManagerModal.jsx  # 지표 설정 모달
│           ├── store/
│           │   └── dashboardStore.js          # zustand 전역 상태
│           └── App.jsx                        # 메인 앱 조립
├── docker-compose.yml
└── .env.example
```

---

## 핵심 모델 설명

### 1. 데이터 수집 — 단일 소스 전략

`raw.eth_ohlcv` 테이블 하나를 **단일 진실 공급원**으로 사용합니다.

- Binance에서 **15m 캔들만** 직접 수집
- 1h, 4h, 6h, 24h는 DB에서 SQL 집계로 파생

수집 주기: 5분마다 Airflow DAG, gap 구간 자동 백필

---

### 2. 기술 지표 (`pipelines/features/technical_indicators.py`)

매시간 계산 후 `features.eth_features` 테이블에 저장됩니다.

| 지표 | 설명 |
|------|------|
| RSI(14) | 과매수/과매도 (70↑: 과매수, 30↓: 과매도) |
| MACD | 추세 방향 + 모멘텀 강도 |
| Bollinger Bands | 상/중/하단 밴드 + %B |
| ATR(14) | 시장 평균 변동폭 (TP/SL 기준) |
| OBV | 거래량 기반 매수/매도 세기 |
| VWAP | 거래량 가중 평균 가격 |
| Stochastic %K/%D | 모멘텀 과매수/과매도 |
| EMA 20/50/200 | 단기/중기/장기 추세 |
| 변동성 레짐 | ATR% 기준 Low / Mid / High 분류 |

---

### 3. ML 분류 모델 (`ml/train_ml_classifier.py`)

**목표:** "다음 N시간 후 가격이 오를까 내릴까?" 이진 분류

- XGBoost / LightGBM 동시 학습 후 Cross-validation F1 기준 자동 선택
- 시계열 무결성 유지: 항상 과거→미래 방향으로 학습/검증 분할

---

### 4. ARIMA 예측 (`ml/train_arima.py`)

다음 N스텝의 가격 밴드를 예측하고 차트에 점선 + 신뢰구간으로 표시합니다.

---

### 5. 앙상블 신호 (`services/api/app/core/ensemble_service.py`)

```
최종 스코어 = 0.30 × ARIMA 방향
            + 0.40 × ML 방향
            + 0.20 × 1h MTF 신호
            + 0.10 × 4h MTF 신호

스코어 > threshold  → LONG
스코어 < -threshold → SHORT
그 외               → NO-TRADE
```

계산 결과는 `features.ensemble_signals` 테이블에 자동 저장되며, 신호 히스토리 탭과 차트 마커(▲▼)에 활용됩니다.

---

### 6. ATR 기반 동적 TP/SL

```
ATR(14) = 최근 14캔들 평균 변동폭

TP = 입장가 + (2.0 × ATR)
SL = 입장가 - (1.0 × ATR)
RR = 2:1 고정

→ 저변동성: TP/SL 좁게 (빠른 청산)
→ 고변동성: TP/SL 넓게 (흔들림에 강함)
```

---

### 7. Half-Kelly 포지션 사이징

```
K = (승률 × RR - 패율) / RR
권장 사이징 = K ÷ 2  (보수적 적용)
```

---

### 8. 리스크 계산기 (RiskCalculator)

앙상블 신호의 진입가/TP/SL을 자동으로 불러와 실시간 계산합니다.

| 항목 | 계산 방법 |
|------|-----------|
| 포지션 크기 | 계좌 × 리스크% ÷ SL거리 |
| 증거금 필요 | 명목가치 ÷ 레버리지 |
| 청산가 추정 | Isolated margin 기준 |
| 수수료 | 테이커 0.05% × 왕복 2회 |
| 기대값 (EV) | 승률 × 이익 − 패율 × 손실 − 수수료 |

---

## API 엔드포인트

### 기본

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | 서버 상태 |
| GET | `/data/status` | 데이터 파이프라인 상태 |

### 가격 데이터

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/prices/history?timeframe=15m&limit=500` | 캔들 히스토리 |
| GET | `/prices/latest?timeframe=15m&limit=3` | 최신 캔들 |

### 모델 / 예측

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/train` | 모델 학습 |
| GET | `/forecast?horizon_hours=6&timeframe=15m` | 가격 예측 |
| GET | `/model/status` | 현재 모델 정보 |
| GET | `/models` | 등록된 모델 목록 |

### 신호 / 피처

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/signals/ensemble?horizon_hours=6&timeframe=15m` | 최신 앙상블 신호 |
| GET | `/signals/history?limit=200&signal_filter=all` | 과거 신호 목록 |
| GET | `/features/latest?exchange=binance&symbol=ETH/USDT` | 최신 기술지표 값 |

### 백테스트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/backtest/strategy?lookback_days=14&fee_bps=5` | 전략 백테스트 |

---

## 실행 방법

### 1. 환경 설정

```bash
cp .env.example .env
# .env에 DB 접속 정보, VITE_API_URL 등 설정
```

### 2. 전체 실행

```bash
docker compose up -d --build
```

> **DB 마이그레이션 자동 처리:** API 컨테이너 시작 시 `migrations.py`가 실행되어 필요한 컬럼과 테이블을 자동으로 추가합니다. 별도 SQL 실행 불필요.

| 서비스 | 주소 |
|--------|------|
| 대시보드 (Web UI) | http://localhost:3000 |
| FastAPI (Swagger) | http://localhost:8000/docs |
| Airflow | http://localhost:8080 |
| PostgreSQL | localhost:5432 |

### 3. 초기 확인

```bash
# 데이터 수집 상태 확인
curl http://localhost:8000/data/status

# 최신 캔들 확인
curl "http://localhost:8000/prices/latest?timeframe=15m&limit=3"

# 모델 학습 (첫 실행 시 필요)
curl -X POST http://localhost:8000/train \
  -H "Content-Type: application/json" \
  -d '{"symbol":"ETH/USDT","timeframe":"15m","horizon_hours":6,"lookback_days":30}'

# 앙상블 신호 확인
curl "http://localhost:8000/signals/ensemble?horizon_hours=6&timeframe=15m"
```

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| 데이터 수집 | Python, ccxt (Binance) |
| 워크플로 | Apache Airflow |
| DB | PostgreSQL 15 |
| ML | XGBoost, LightGBM, statsmodels (ARIMA) |
| 백엔드 | FastAPI, psycopg2 |
| 프론트엔드 | React, Vite, Apache ECharts, zustand |
| 인프라 | Docker Compose |

---

## Airflow DAG 스케줄

| DAG | 주기 | 역할 |
|-----|------|------|
| `ingestion_dag` | 5분 | Binance 15m 캔들 수집 |
| `feature_pipeline_dag` | 매시간 | 기술 지표 계산 + DB 저장 |
| `ml_retrain_dag` | 4시간 | ML 모델 재학습 |

---

## 성능 최적화

- **15초 자동 갱신:** 마지막 캔들 3개만 폴링해 병합 (전체 재로드 없음)
- **차트 전환:** `setOption(notMerge=false)`로 재마운트 없이 타임프레임 전환
- **줌 상태 유지:** zustand에 `dataZoom` viewport 저장
- **리샘플:** 거래소에 여러 타임프레임 요청 없이 DB에서 1회 파생
- **자동 마이그레이션:** API 시작 시 `ALTER TABLE IF NOT EXISTS`로 멱등성 보장
