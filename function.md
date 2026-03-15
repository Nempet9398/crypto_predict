# 기능 목록

> 지울 것: 앞에 [x] 표시
> 수정할 것: 아래 EDIT 섹션에 따로 정리

---

## 백엔드 API

- [ ] GET /health — 서버 살아있는지 확인
- [ ] GET /data/status — DB에 캔들 몇 개 있는지, 갭 있는지, 마지막 수집 시간
- [ ] GET /prices/history — 캔들스틱 히스토리 (타임프레임/기간 지정)
- [ ] GET /prices/latest — 최신 캔들 N개 (15초 새로고침용)
- [ ] POST /train — ARIMA 모델 수동 학습 트리거
- [ ] GET /forecast — ARIMA 예측값 + 신뢰구간 반환
- [ ] GET /models — 학습된 모델 목록
- [ ] GET /model/status — 현재 활성 모델 정보 (order, 학습시간, MAE)
- [ ] GET /signals/ensemble — 현재 매매 신호 (LONG/SHORT/NO-TRADE + TP/SL + 포지션 크기)
- [ ] GET /signals/history — 과거 신호 기록 테이블
- [ ] GET /features/latest — 현재 RSI, MACD, ATR 등 지표값
- [ ] GET /backtest/strategy — 과거 데이터로 전략 시뮬레이션 (수익률, Sharpe 등)

---

## 모델 / 알고리즘

- [X] ARIMA 자동 order 탐색 — p,d,q 조합 grid search로 최적 파라미터 자동 선택
- [X] SARIMAX — 계절성 포함한 ARIMA 확장 버전
- [X] XGBoost 3-class 분류기 — 가격 방향 (하락/중립/상승) 예측 ML 모델
- [X] LightGBM 3-class 분류기 — XGBoost 대안, F1 높은 쪽 자동 선택
- [X] 앙상블 가중 스코어링 — ARIMA 30% + ML 40% + 1h MTF 20% + 4h MTF 10% 합산
- [ ] ATR 기반 TP/SL — TP = 진입가 ± 2×ATR, SL = 진입가 ± 1×ATR
- [ ] Half-Kelly 포지션 사이징 — 켈리 공식 절반값, 최대 25% cap
- [ ] 신호 임계값 필터 — score > 0.08이면 LONG, < -0.08이면 SHORT, 그 사이는 NO-TRADE
- [ ] 변동성 레짐 필터 — 고변동성 구간에서 임계값 자동 강화
- [ ] 멀티타임프레임 agreement 필터 — 1h/4h 방향 일치할 때만 신호 허용
- [ ] model versioning / model registry — 학습할 때마다 버전 저장, 이전 모델 복구 가능
- [ ] 자동 재학습 — 일정 시간 경과 또는 새 데이터 N개 누적 시 자동 재학습

---

## 기술 지표

- [ ] RSI(14) — 과매수/과매도 모멘텀 지표
- [ ] MACD(12,26,9) — 추세 방향 및 전환 포착
- [ ] Bollinger Bands(20,2) — 가격 변동 범위, 돌파 신호
- [ ] ATR(14) — 평균 변동폭, TP/SL 기준값으로 사용
- [ ] Stochastic %K/%D(14,3) — 단기 과매수/과매도
- [ ] OBV — 거래량 기반 추세 확인
- [ ] VWAP — 거래량 가중 평균 가격
- [ ] EMA 20/50/200 — 단기/중기/장기 지수 이동평균
- [ ] SMA 5/10 — 단순 이동평균
- [ ] 변동성 레짐 분류 — ATR 백분위로 low/medium/high 구분
- [ ] 멀티타임프레임 신호 — 15m 데이터를 1h/4h로 집계해서 EMA 크로스오버 계산
- [ ] 4-bar forward target labeling — 4캔들 후 가격 방향을 ML 학습 정답 레이블로 사용

---

## 데이터 파이프라인

- [ ] Binance 15m 캔들 수집 — CCXT로 5분마다 최신 캔들 가져와서 DB 저장
- [ ] 갭 감지 및 백필 — 빠진 시간대 자동 감지 후 소급 수집
- [ ] raw.eth_ohlcv 저장 — 원본 15m 캔들 테이블
- [X] 기술지표 계산 후 features.eth_features 저장 — 1시간마다 지표 계산해서 별도 테이블에 저장
- [X] processed.eth_ohlcv_1h 리샘플링 테이블 — 15m → 1h 집계 결과 별도 저장
- [ ] Airflow DAG — 수집/지표계산/재학습 각각 스케줄 자동 실행
- [ ] data-updater 별도 컨테이너 — 데이터 수집 + 재학습 전담 백그라운드 프로세스
- [ ] 자동 재학습 데몬 — updater.py가 상시 실행되며 조건 충족 시 재학습 트리거

---

## 프론트엔드 컴포넌트

- [X] TopBar — 상단바: 종목/타임프레임/모델 선택, 학습 버튼, 자동새로고침 토글, horizon 슬라이더
- [ ] MarketStatsBar — 현재가, 24h 등락률, ATR값, 변동성 레짐, 데이터 수집 상태 표시
- [ ] TradingChart — 메인 캔들차트: MA 오버레이, ARIMA 예측선, 신호 마커(▲▼), RSI/MACD 서브패널
- [X] SignalPanel — 현재 신호 패널: LONG/SHORT/NO-TRADE 뱃지, 스코어, 확률, TP/SL 가격, 포지션 크기
- [X] MultiTimeframePanel — 15m/1h/4h 각 타임프레임 신호 방향 정렬 상태 표시
- [ ] TechnicalSummary — 현재 RSI/MACD/BB/Stochastic 값 한눈에 보는 요약 테이블
- [X] RightPanel — 여러 모델의 예측값 나란히 비교
- [X] BacktestPanel — 백테스트 UI: lookback/수수료 설정, 실행 버튼, P&L 차트, 거래 목록, Sharpe/Sortino
- [X] SignalHistoryPanel — 과거 신호 20개 테이블, LONG/SHORT/NO-TRADE 필터
- [X] RiskCalculator — 계좌금액/레버리지/리스크% 입력 → 포지션 크기, 청산가, 기대값 자동 계산
- [ ] ModelDataStatusPanel — DB 상태(행 수, 갭, 최신 시간), 모델 학습 상태 표시
- [X] BottomTabs — 하단 탭: Backtest / Signal History / Risk / Model Status 전환
- [ ] IndicatorManagerModal — 차트에 표시할 지표 추가/제거하는 팝업
- [ ] 15초 자동 새로고침 — 마지막 3캔들만 가져와서 기존 데이터에 병합 (전체 재로드 없이)
- [ ] Zustand 전역 상태 관리 — 심볼/타임프레임/지표 설정/모델 선택 등 앱 전체 상태 관리

---

## 인프라

- [ ] Docker Compose — postgres, api, web, airflow, data-updater 6개 컨테이너 일괄 실행
- [ ] FastAPI 시작 시 자동 마이그레이션 — 서버 켤 때 누락된 DB 컬럼 자동 추가
- [ ] 타임존 변환 — 모든 시간 응답을 Asia/Seoul 기준으로 변환

---

## EDIT (수정할 것)

<!-- 수정하고 싶은 기능은 여기에 내용이랑 같이 써줘 -->

- [] 차트 줌/팬 + 과거 데이터 추가 로딩 — 왼쪽으로 스크롤 시 더 오래된 캔들 자동 요청 : 이거 실제 그냥 트레이딩 웹사이트에서 사용하는 방식과 똑같이 해 뭔 스크롤시 더 오래된 캔들 자동 요청 말고 그냥 일일히 방식을 정하지말고 그냥 정말 트레이딩 웹사이트 서비스와 똑같은 차트를 만들 수 있도록 하시오.


---

## ADD (새로 추가할 것)

<!-- 없던 기능인데 새로 원하는 거 써줘 -->

- 모든 지표 및 데이터 15m 단위로 수집해서 알아서 계산되게 진행
- 각 캔들 몇퍼센트 상승 / 몇퍼센트 하락했는지 보여주기
- 관련 지표 추가 및 
- 대시보드화 : 다양한 지표 뿐만 아니라 다양한 상태 여러 패널로 만들기 (패널 안 수치는 아직 미정)
- 추후 시계열 모델 확장(현재는 미정) 예측 값을 보여주는 패널 추가
- 예측 값을 통해 포지션을 만들어내고 해당 포지션에 대한 손절가 / 매도가 등 설정 패널 
- 기타 UI적으로 실제 트레이딩 및 거래를 지원하는 대시보드를 위한 창으로 변경
- UI 좀 더 밝게 변경
- 실제 기능 작동시 모두 상태를 파악할 수 있게 진행 (현재 버튼 누를 시 뭐가 진행되는지 모름)
- 추가적으로 머신러닝 모델 xgboost / lightgbm 등등 모델을 사용하여 시계열 예측을 할 수 있게 db 적재
- 추후 예측 값을 만들어내고 해당 모델로 테스트 하였을 때 성공률 등을 초회할 수 있는 패널 추가 (예측 기간 설정 / 기간 설정하여 직접 성공률 측정)
- 해당 기능 필요시 추가한 것들 구현
- 이제는 더이 상 필요없는 기능 및 데이터는 모두 삭제 하여 효율적 운영 진행
- 실제 트레이딩 지원 서비스 - 이더리움 거래를 위한 대시보드 구현하여 실제 서비스 및 구독자를 모을 수 있는 수준의 서비스를 직접 만들어 낼것