# Ethereum Data Pipeline & Serving System

Binance의 ETH/USDT 1시간(1h) OHLCV 데이터를 주기적으로 수집하고, 가공/피처 생성/모델 학습을 자동화한 뒤 FastAPI로 서빙하고 React UI로 시각화하는 Docker 기반 데이터 엔지니어링 프로젝트입니다. 로컬에는 Docker만 설치되어 있으면 됩니다.

## 구성 요소
- PostgreSQL: raw/processed/features 스키마와 테이블 저장
- Airflow: 수집 → 처리 → 피처 생성 → 모델 학습 파이프라인 오케스트레이션
- FastAPI: 데이터/피처/예측 API 제공
- React UI: 가격 차트, 피처 테이블, 예측값 표시

## 실행 전 준비
1. Docker Desktop 설치 및 실행
2. 환경 변수 파일 생성

```bash
cp .env.example .env
```

## 실행 방법
```bash
docker compose up --build
```

## 서비스 접속 주소
- Airflow UI: http://localhost:8080
  - 로그인: `.env`의 `AIRFLOW_ADMIN_USER / AIRFLOW_ADMIN_PASSWORD`
- API: http://localhost:8000
- Web UI: http://localhost:3000

## Airflow DAG 실행 방법(처음 데이터 생성 시)
처음에는 데이터가 없으므로 아래 방법 중 하나로 DAG를 실행합니다.

1) 자동 스케줄 대기
- `@hourly` 스케줄에 따라 1시간마다 실행됨

2) 수동 실행(권장)
- Airflow UI 접속 후 DAG ON
- `ingestion_dag` → Trigger DAG
- `feature_pipeline_dag` → Trigger DAG

## API 엔드포인트
- `GET /health`
- `GET /prices/latest`
- `GET /prices/history?limit=100`
- `GET /features/latest`
- `POST /predict`

## 데이터 흐름
1. `ingestion_dag`
   - `pipelines/ingestion/binance_ohlcv.py` 실행
   - Binance OHLCV → `raw.eth_ohlcv` UPSERT
2. `feature_pipeline_dag`
   - `pipelines/processing/resample.py` 실행
   - raw → `processed.eth_ohlcv_1h` UPSERT
3. 피처 생성
   - `pipelines/features/technical_indicators.py` 실행
   - 기술지표 계산 → `features.eth_features` UPSERT
4. 모델 학습
   - `ml/train.py` 실행
   - 결과 모델 저장: `ml/model_registry/eth_price_model.joblib`
5. 서빙
   - FastAPI가 DB에서 데이터를 읽고 모델을 로드해 예측 제공

## 스키마/테이블
- 스키마: `raw`, `processed`, `features`
- 테이블:
  - `raw.eth_ohlcv`
  - `processed.eth_ohlcv_1h`
  - `features.eth_features`

모든 테이블은 PK 기반 UPSERT로 idempotent(중복 실행 안전)하게 동작합니다.

## 참고
- 이 프로젝트는 ML 정확도보다 데이터 파이프라인 구조와 운영 가능성에 초점을 둡니다.
- Mac이 꺼지거나 잠자면 컨테이너도 멈춥니다. 다시 켠 뒤 `docker compose up -d`로 재시작하면 됩니다.
