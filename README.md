# ETH Trading Dashboard

> Binance 15분 캔들 기반 이더리움 트레이딩 대시보드.
> 기술지표 + ML 예측(XGBoost/LightGBM) 앙상블로 LONG/SHORT 신호와 ATR 기반 TP/SL을 생성합니다.
> 실제 주문 집행 없이 트레이딩 의사결정을 지원합니다.

---

## 전체 흐름

```
Binance 15m OHLCV
        ↓
  raw.eth_ohlcv (PostgreSQL)
        ↓
  기술지표 계산 15m 단위 (RSI, MACD, BB, ATR, Stochastic, OBV, VWAP, EMA ...)
        ↓
  ┌─────────────────────┐    ┌──────────────────────┐
  │  기술지표 합성 스코어 │    │  ML 예측 모델         │
  │  RSI/MACD/BB/Stoch  │    │  XGBoost / LightGBM  │
  │  40% 가중치          │    │  40% 가중치           │
  └──────────┬──────────┘    └──────────┬───────────┘
             │                          │
             └────── 앙상블 ────────────┘
                   + MTF 1h 15% / 4h 5%
                         ↓
          최종 신호: LONG / SHORT / NO-TRADE
          + ATR 기반 TP/SL
          + Half-Kelly 포지션 사이징
                         ↓
          FastAPI → React 대시보드 (TradingView 차트)
```

---

## 주요 기능

### 신호 생성
- 기술지표 합성 스코어 (RSI, MACD, Bollinger Band, Stochastic, EMA 정렬)
- XGBoost / LightGBM 시계열 예측 (방향 분류, TimeSeriesSplit 검증)
- 멀티타임프레임 확인 (1h, 4h EMA 크로스오버)
- 변동성 레짐 필터 (고변동성 시 임계값 자동 강화)
- ATR 기반 TP/SL (TP = 진입가 ± 2×ATR, SL = ± 1×ATR)
- Half-Kelly 포지션 사이징 (최대 25% cap)

### ML 예측 정확도 추적
- 예측값 + 실제값 DB 저장 (`features.ml_predictions`)
- 기간 / 예측 범위(1h, 4h, 8h) 설정하여 정확도 직접 측정
- LONG / SHORT 별 정확도 분리 표시

### 차트
- TradingView `lightweight-charts` 기반 실제 트레이딩 차트
- EMA 20/50/200, SMA, Bollinger Bands 오버레이
- 거래량 히스토그램
- 현재 신호 마커 (▲ LONG / ▼ SHORT)
- 15초 자동 새로고침

### 포지션 계산기
- 계좌 잔고 / 레버리지 / 리스크% 입력
- 신호에서 진입가 / TP / SL 자동 채우기
- 포지션 크기, 증거금, 수익/손실, 수수료, 위험보상비, 추정 청산가 계산

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| 데이터 수집 | Python, CCXT (Binance) |
| 파이프라인 스케줄 | Apache Airflow |
| DB | PostgreSQL 15 |
| 백엔드 API | FastAPI |
| ML | XGBoost, LightGBM, scikit-learn |
| 프론트엔드 | React 18, lightweight-charts, Zustand |
| 인프라 | Docker Compose |

---

## 실행

### 환경변수 설정
```bash
cp .env.example .env
# .env 수정: DB_USER, DB_PASSWORD, DB_NAME, VITE_API_URL 등
```

### 실행
```bash
docker compose up --build
```

| 서비스 | 주소 |
|--------|------|
| 대시보드 | http://localhost:3000 |
| API | http://localhost:8000/docs |
| Airflow | http://localhost:8080 |

---

## API 주요 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/signals/current` | 현재 LONG/SHORT/NO-TRADE 신호 |
| GET | `/signals/history` | 과거 신호 목록 |
| POST | `/ml/train` | ML 모델 학습 트리거 |
| GET | `/ml/status` | ML 모델 상태 |
| GET | `/ml/accuracy` | 예측 정확도 (기간 설정) |
| GET | `/prices/history` | 캔들스틱 히스토리 |
| GET | `/features/latest` | 현재 기술지표 값 |
| GET | `/data/status` | 데이터 수집 상태 |

---

## DB 스키마

| 테이블 | 설명 |
|--------|------|
| `raw.eth_ohlcv` | Binance 15m 원본 캔들 |
| `features.eth_features` | 15m 단위 기술지표 (RSI, MACD, ATR 등 20+) |
| `features.signals` | 생성된 매매 신호 이력 |
| `features.ml_predictions` | ML 예측값 + 실제값 (정확도 추적) |
