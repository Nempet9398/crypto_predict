# crypto_predict (15m Single-Source Trading Dashboard)

`raw.eth_ohlcv`를 단일 Source of Truth로 사용합니다.

- 수집: 거래소에서 `15m`만 수집 (ccxt)
- 파생 타임프레임: DB 리샘플(`30m`, `1h`, `6h`, `24h`)
- API: FastAPI
- UI: React + Vite + Apache ECharts + zustand

## Architecture

### Data Collection (Airflow)

- DAG: `airflow/dags/ingestion_dag.py`
- 스케줄: `*/5 * * * *` (5분마다)
- 수집 스크립트: `pipelines/ingestion/binance_ohlcv.py`
- 동작:
  - `timeframe='15m'`만 조회
  - `MIN(ts)~MAX(ts)` gap 탐지
  - gap 구간 백필 + 최신 구간 업데이트
  - `ON CONFLICT ... DO UPDATE` UPSERT

### Resampling (Postgres SQL)

지원 타임프레임:

- `15m` (직접 조회)
- `30m` (2 x 15m)
- `1h` (4 x 15m)
- `6h` (24 x 15m)
- `24h` (96 x 15m)

집계 규칙:

- `open`: bucket 내 첫 open (ts asc)
- `high`: max(high)
- `low`: min(low)
- `close`: bucket 내 마지막 close (ts asc 기준)
- `volume`: sum(volume)

## API

### Core

- `GET /health`
- `GET /data/status?timeframe=15m|30m|1h|6h|24h&tz=Asia/Seoul`

### Prices

- `GET /prices/history?timeframe=15m|30m|1h|6h|24h&start=<iso>&end=<iso>&limit=<n>&tz=<IANA>`
- `GET /prices/latest?timeframe=15m|30m|1h|6h|24h&limit=<n>&tz=<IANA>`

### Model

- `POST /train`
  - 예시 body:
    - `lookback_days`, `horizon_hours`, `symbol`, `exchange`, `timeframe`, `auto_order`
- `GET /forecast?horizon_hours=6&timeframe=15m|30m|1h|6h|24h&tz=Asia/Seoul`
  - 선택 타임프레임에 맞춰 horizon을 step으로 환산해 예측
- `GET /model/status`

## Frontend UX

- 상단 바: Symbol, Timeframe, EMA 설정, Refresh, Train
- 메인 차트: Candlestick + EMA + Forecast + Confidence band + Forecast boundary
- 서브패널: Volume
- 우측 패널:
  - 현재 timeframe
  - last price
  - forecast direction
  - confidence range
  - active model id
  - trained_at
  - MAE/MAPE

### UX/Performance Rules

- 차트 줌/팬 유지: dataZoom 상태를 zustand에 저장
- 자동 갱신: `/prices/latest`로 마지막 캔들만 patch (full reload 없음)
- 타임프레임 전환: 차트 컴포넌트 재마운트 없이 option 업데이트
- 차트 업데이트: `setOption` (`notMerge=false`) 기반

## Run

```bash
docker compose up -d --build
```

- API: `http://localhost:8000`
- Web: `http://localhost:3000`
- Airflow: `http://localhost:8080`

## Quick Checks

1. `GET /data/status?timeframe=15m`
2. `GET /prices/history?timeframe=30m&limit=100`
3. `GET /prices/latest?timeframe=15m&limit=3`
4. `POST /train` (timeframe 지정)
5. `GET /forecast?horizon_hours=6&timeframe=1h`
