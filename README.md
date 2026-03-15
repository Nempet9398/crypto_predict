# crypto_predict — 암호화폐 트레이딩 신호 대시보드

> Binance 15분 캔들을 기반으로 기술 지표, 머신러닝, ARIMA 예측을 앙상블해 **LONG/SHORT 신호**와 **동적 TP/SL**을 자동으로 생성하는 풀스택 트레이딩 대시보드입니다.

---

## 전체 흐름 한눈에 보기

```
Binance OHLCV (15m)
        ↓
  PostgreSQL 저장 (raw.eth_ohlcv)
        ↓
  Postgres SQL 리샘플 → 1h, 4h 파생
        ↓
  기술 지표 계산 (RSI, MACD, BB, ATR, OBV...)
        ↓
  ┌─────────────────┐    ┌─────────────────┐
  │  ARIMA 예측     │    │  ML 분류 모델    │
  │ (가격 밴드 예측) │    │ (XGBoost / LGB) │
  │                 │    │  방향 확률 출력  │
  └────────┬────────┘    └────────┬────────┘
           │                     │
           └──────── 앙상블 ──────┘
                      ↓
           멀티타임프레임 확인 (1h, 4h)
                      ↓
         최종 신호: LONG / SHORT / HOLD
         + ATR 기반 동적 TP / SL
         + Half-Kelly 포지션 사이징
                      ↓
              FastAPI → React UI
```

---

## 디렉토리 구조

```
crypto_predict/
├── airflow/
│   └── dags/
│       ├── ingestion_dag.py         # 5분마다 Binance 데이터 수집
│       ├── feature_pipeline_dag.py  # 매시간 기술 지표 계산
│       └── ml_retrain_dag.py        # 4시간마다 ML 모델 재학습
├── db/
│   ├── schema.sql                   # 테이블 정의
│   └── migrations/                  # DB 마이그레이션
├── ml/
│   ├── train_arima.py               # ARIMA 학습
│   ├── train_ml_classifier.py       # XGBoost / LightGBM 학습 및 선택
│   ├── train.py                     # 통합 학습 엔트리포인트
│   ├── compute_ensemble_signals.py  # 앙상블 신호 계산
│   └── ml_classifier_service.py     # ML 예측 서비스
├── pipelines/
│   ├── ingestion/
│   │   └── binance_ohlcv.py         # Binance ccxt 수집
│   ├── features/
│   │   └── technical_indicators.py  # 기술 지표 계산
│   └── processing/
│       └── resample.py              # DB 내 타임프레임 리샘플
├── services/
│   ├── api/
│   │   └── app/
│   │       ├── core/
│   │       │   ├── arima_service.py     # ARIMA 예측 서비스
│   │       │   ├── ensemble_service.py  # 앙상블 신호 생성
│   │       │   ├── ohlcv.py             # OHLCV 데이터 조회
│   │       │   └── model.py             # 모델 상태 관리
│   │       └── routers/
│   │           ├── prices.py            # 가격 API
│   │           ├── model.py             # 학습/예측 API
│   │           ├── features.py          # 지표 API
│   │           └── data.py              # 데이터 상태 API
│   └── web/
│       └── src/
│           ├── components/
│           │   ├── TradingChart.jsx     # 캔들차트 메인
│           │   ├── SignalPanel.jsx      # 앙상블 신호 패널
│           │   ├── RightPanel.jsx       # 예측 정보 패널
│           │   ├── TopBar.jsx           # 상단 설정 바
│           │   └── IndicatorManagerModal.jsx
│           └── store/                   # zustand 전역 상태
├── docker-compose.yml
└── .env.example
```

---

## 핵심 모델 설명

### 1. 데이터 수집 — 단일 소스 전략

`raw.eth_ohlcv` 테이블 하나를 **단일 진실 공급원(Single Source of Truth)** 으로 씁니다.

- Binance에서 **15m 캔들만** 직접 수집
- 1h, 4h, 6h, 24h는 DB에서 SQL 집계로 파생

**집계 규칙:**
```
open   → bucket 내 첫 번째 open
high   → MAX(high)
low    → MIN(low)
close  → bucket 내 마지막 close
volume → SUM(volume)
```

수집 주기: 5분마다 Airflow DAG 실행, gap 구간 자동 백필

---

### 2. 기술 지표 계산 (`pipelines/features/technical_indicators.py`)

매시간 Airflow가 아래 15개 이상의 지표를 계산해 DB에 저장합니다.

| 지표 | 설명 |
|------|------|
| RSI(14) | 과매수/과매도 판단 (70↑: 과매수, 30↓: 과매도) |
| MACD | 추세 방향과 모멘텀 강도 |
| Bollinger Bands | 가격의 표준편차 기반 상/중/하단 밴드 |
| ATR(14) | 현재 시장 평균 변동량 |
| OBV | 거래량 기반 매수/매도 세기 |
| EMA 20/50/200 | 단기/중기/장기 이동평균 |
| Stochastic | 모멘텀 지표 |
| Williams %R | 단기 과매수/과매도 |
| CCI | 추세 사이클 지표 |
| 거래량 변화율 | 급등락 거래량 탐지 |

**변동성 레짐 분류:**
```
ATR% = ATR / 현재가

Low Vol    → 하위 33%: 신호 필터 강화 (조건을 더 엄격하게)
Medium Vol → 중간 구간: 정상 조건
High Vol   → 상위 33%: 노이즈 많음, 신호 신뢰도 낮춤
```

---

### 3. ML 분류 모델 (`ml/train_ml_classifier.py`)

**목표:** "다음 1시간 후 가격이 오를까, 내릴까?"를 분류하는 이진 분류기

**모델 선택:**
- XGBoost와 LightGBM 동시에 학습
- Cross-validation F1 스코어 비교 후 **더 좋은 모델 자동 선택**

**학습 방식 (시계열 무결성 유지):**
```
과거 ──────────────────→ 미래
[학습 구간] [검증 구간]   (항상 이 순서)

절대로 미래 데이터로 과거를 학습하지 않음 (Data Leakage 방지)
```

**출력 예시:**
```json
{
  "direction": "up",
  "prob_up": 0.71,
  "prob_down": 0.29
}
```

---

### 4. ARIMA 예측 (`ml/train_arima.py`)

가격의 **다음 N스텝 밴드**를 예측합니다.

```
현재가:   2,300
예측 밴드: 2,285 ~ 2,340 (신뢰구간 95%)
방향 확률: Up 62%, Down 38%
```

차트에 주황색 점선(예측선) + 반투명 밴드(신뢰구간)로 시각화됩니다.

---

### 5. 앙상블 신호 (`services/api/app/core/ensemble_service.py`)

ARIMA, ML, 멀티타임프레임 세 가지를 **가중 합산**해 최종 신호를 생성합니다.

```
최종 스코어 = 0.30 × ARIMA 신호
            + 0.40 × ML 신호
            + 0.20 × 1h MTF 신호
            + 0.10 × 4h MTF 신호

예시 계산:
  ARIMA up 62% → +0.186
  ML    up 71% → +0.284
  1h    상승   → +0.100
  4h    상승   → +0.050
  ─────────────────────
  스코어 = +0.620

스코어 > 0  → LONG
스코어 < 0  → SHORT
스코어 ≈ 0  → HOLD (신호 불명확)
```

---

### 6. 멀티타임프레임 확인 (MTF)

15분 신호가 더 큰 추세와 같은 방향인지 확인합니다.

```
1시간봉: EMA20 vs EMA50 비교
  EMA20 > EMA50 → 상승 추세 (Bull)
  EMA20 < EMA50 → 하락 추세 (Bear)

4시간봉도 동일 방법으로 확인

결과:
  1h Bull + 4h Bull → 신호 강화 (+)
  1h Bear + 4h Bear → 신호 약화 (-)
  엇갈리면          → 중립 (신호 보정)
```

---

### 7. ATR 기반 동적 TP/SL

고정 퍼센트 대신 **시장 변동성(ATR)에 맞춰 자동 조정**합니다.

**고정 방식의 문제:**
```
어떤 날이든: TP = 입장가 × 1.015, SL = 입장가 × 0.985
→ 고변동성 날엔 SL에 바로 걸림
→ 저변동성 날엔 TP가 너무 멀어 도달 못함
```

**ATR 동적 방식:**
```
ATR(14) = 최근 14캔들 평균 변동폭

TP = 입장가 + (2.0 × ATR)   ← 2배 변동폭만큼 위
SL = 입장가 - (1.0 × ATR)   ← 1배 변동폭만큼 아래
RR 비율 = 2:1 (고정)

저변동성 시장 → ATR 작음 → TP/SL 좁게 (빠른 청산)
고변동성 시장 → ATR 큼   → TP/SL 넓게 (흔들림에 강함)
```

---

### 8. Half-Kelly 포지션 사이징

"이 거래에 자금의 몇 %를 투입해야 수학적으로 최적인가?"를 계산합니다.

```
Kelly 공식:
  K = (승률 × RR - 패율) / RR

예시:
  승률 = 65%, RR = 2:1, 패율 = 35%
  K = (0.65 × 2 - 0.35) / 2 = 47.5%

Half-Kelly (보수적 적용):
  권장 사이징 = 47.5% ÷ 2 = 23.75%
```

UI의 SignalPanel에서 "Position Sizing: 23.75%" 형태로 표시됩니다.

---

### 9. 백테스트 성과 지표

단순 수익률 외에 다양한 리스크 조정 지표를 제공합니다.

| 지표 | 의미 |
|------|------|
| Win Rate | 전체 거래 중 이익을 낸 비율 |
| Profit Factor | 총 이익 ÷ 총 손실 (2.0↑ 우수) |
| Payoff Ratio | 평균 이익 ÷ 평균 손실 |
| Sharpe Ratio | 위험 대비 수익 (1.5↑ 양호) |
| Sortino Ratio | 하방 위험만 반영한 Sharpe |
| Calmar Ratio | 최대 낙폭(MDD) 대비 연 수익률 |
| Max Drawdown | 고점 대비 최대 손실 낙폭 |
| MAE / MAPE | 예측 가격 오차 |

---

## UI 구성

### 상단 바 (TopBar)

```
[ ETH/USDT ▼ ]  [ 15m ▼ ]  [ EMA 설정 ]  [ 새로고침 ]  [ Train 모델 ]
```

심볼, 타임프레임 전환 및 EMA 기간 커스텀, 모델 재학습 버튼

---

### 메인 차트 (TradingChart)

Apache ECharts 기반 캔들스틱 차트입니다.

```
┌─────────────────────────────────────────────────────────┐
│   ║  ║  ╫  ╫  ║  ║  (캔들스틱)                         │
│   EMA 20 ─── EMA 50 ─── EMA 200 ───                    │
│                               ~~~~~ (주황 예측선)       │
│                          [===========] (신뢰구간 밴드)  │
│─────────────────────────────────────────────────────────│
│   Volume ▌▌▌▌▌▌▌▌▌▌ (서브패널)                         │
└─────────────────────────────────────────────────────────┘
```

- 줌/팬 상태 zustand에 저장 → 새로고침 후에도 유지
- 타임프레임 전환 시 차트 재마운트 없이 부드럽게 전환
- `setOption(notMerge=false)` 기반 부분 업데이트

---

### 신호 패널 (SignalPanel)

```
┌───────────────────────────────────┐
│          SIGNAL (Ensemble)        │
│              🟢 LONG              │
├───────────────────────────────────┤
│  Score          +0.523            │
│  Confidence     52.3%             │
├───────────────────────────────────┤
│  ARIMA                            │
│    Prob Up      68%               │
│    Prob Down    32%               │
├───────────────────────────────────┤
│  ML Model                         │
│    Direction    ▲ Up              │
│    Prob Up      71%               │
├───────────────────────────────────┤
│  Multi-Timeframe                  │
│    1h Signal    ▲ Bull            │
│    4h Signal    ▲ Bull            │
│    Vol Regime   🟢 Low Vol        │
├───────────────────────────────────┤
│  TP / SL (ATR 기반)               │
│    Entry        2,300             │
│    TP (2×ATR)   2,330   🟢        │
│    SL (1×ATR)   2,285   🔴        │
│    RR Ratio     2.0               │
│    ATR(14)      15                │
├───────────────────────────────────┤
│  Position Sizing                  │
│    Size (Kelly) 23.75%            │
└───────────────────────────────────┘
```

---

### 우측 패널 (RightPanel)

```
현재 타임프레임: 1h
마지막 가격:     2,318.40
예측 방향:       ▲ Up
신뢰구간:        2,285 ~ 2,340
활성 모델 ID:    arima_eth_1h_v3
학습 시각:       2026-03-11 08:00
MAE:             12.4
MAPE:            0.54%
```

---

## Airflow DAG 스케줄

| DAG | 스케줄 | 역할 |
|-----|--------|------|
| `ingestion_dag` | `*/5 * * * *` (5분) | Binance 15m 수집 |
| `feature_pipeline_dag` | `0 * * * *` (매시간) | 기술 지표 계산 |
| `ml_retrain_dag` | `0 */4 * * *` (4시간) | ML 모델 재학습 |

---

## API 엔드포인트

### 기본

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | 서버 상태 확인 |
| GET | `/data/status?timeframe=1h&tz=Asia/Seoul` | 데이터 수집 상태 |

### 가격 데이터

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/prices/history?timeframe=1h&limit=200` | 캔들 히스토리 |
| GET | `/prices/latest?timeframe=15m&limit=1` | 최신 캔들 |

### 모델 / 신호

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/train` | 모델 학습 요청 |
| GET | `/forecast?horizon_hours=6&timeframe=1h` | 가격 예측 |
| GET | `/model/status` | 현재 모델 상태 |

### 요청 예시

```bash
# 모델 학습
curl -X POST http://localhost:8000/train \
  -H "Content-Type: application/json" \
  -d '{"symbol":"ETH/USDT","timeframe":"1h","horizon_hours":6,"lookback_days":90}'

# 예측 조회
curl "http://localhost:8000/forecast?horizon_hours=6&timeframe=1h&tz=Asia/Seoul"
```

---

## 실행 방법

### 1. 환경 설정

```bash
cp .env.example .env
# .env에 Binance API Key, DB 접속 정보 등 설정
```

### 2. 전체 실행

```bash
docker compose up -d --build
```

| 서비스 | 주소 |
|--------|------|
| Web UI | http://localhost:3000 |
| FastAPI | http://localhost:8000 |
| Airflow | http://localhost:8080 |
| PostgreSQL | localhost:5432 |

### 3. 초기 확인

```bash
# 데이터 수집 상태
curl http://localhost:8000/data/status?timeframe=15m

# 최근 캔들 확인
curl "http://localhost:8000/prices/latest?timeframe=15m&limit=3"

# 모델 학습 (첫 실행 시)
curl -X POST http://localhost:8000/train \
  -H "Content-Type: application/json" \
  -d '{"symbol":"ETH/USDT","timeframe":"1h","horizon_hours":6}'

# 예측 확인
curl "http://localhost:8000/forecast?horizon_hours=6&timeframe=1h"
```

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| 데이터 수집 | Python, ccxt (Binance) |
| 워크플로 | Apache Airflow |
| DB | PostgreSQL |
| ML | XGBoost, LightGBM, statsmodels (ARIMA) |
| 백엔드 | FastAPI, SQLAlchemy |
| 프론트엔드 | React, Vite, Apache ECharts, zustand |
| 인프라 | Docker Compose |

---

## 성능 최적화 포인트

- **자동 갱신:** 마지막 캔들만 `PATCH`로 업데이트 (전체 리로드 없음)
- **차트 전환:** `setOption(notMerge=false)`로 재마운트 없이 타임프레임 전환
- **줌 상태 유지:** zustand에 `dataZoom` 상태 저장
- **리샘플:** 거래소에 여러 타임프레임 요청 안 하고 DB에서 1회 파생
- **모델 재학습:** 별도 DAG 분리 (4시간 주기, 메인 수집 DAG와 독립)
