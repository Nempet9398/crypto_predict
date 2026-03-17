"""
앙상블 설정 라우터
- GET  /ensemble/config        — 현재 앙상블 가중치 + 설명 조회
- PUT  /ensemble/config        — 앙상블 가중치 업데이트
- POST /ensemble/config/reset  — 기본값으로 초기화
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, validator

from app.core.db import fetch_one, fetch_all, db_conn_cursor
from app.core.signal_service import reload_ensemble_config

router = APIRouter(tags=["ensemble"])


# ── 가중치 메타데이터 (UI 설명용) ──────────────────────────────────────────────
WEIGHT_META = {
    "w_tech": {
        "label": "기술지표 합성 스코어",
        "description": (
            "RSI, MACD, 볼린저밴드, 스토캐스틱, EMA 정렬 5개 지표를 "
            "[-1, +1]로 정규화한 가중 평균입니다. "
            "순수 기술적 분석 신호를 반영합니다. "
            "높일수록 전통 기술지표 신호를 더 신뢰합니다."
        ),
        "default": 0.35,
        "min": 0.0,
        "max": 1.0,
    },
    "w_ml_regressor": {
        "label": "ML 회귀 스코어 (수익률 예측)",
        "description": (
            "XGBoost/LightGBM Quantile Regression이 예측한 1h 수익률을 기반으로 합니다. "
            "신뢰구간(CI) 폭이 좁을수록 신호 강도가 높아집니다 (CI dampening). "
            "높일수록 ML 수익률 예측을 더 신뢰합니다."
        ),
        "default": 0.30,
        "min": 0.0,
        "max": 1.0,
    },
    "w_ml_classifier": {
        "label": "ML 분류 스코어 (방향 예측)",
        "description": (
            "XGBoost/LightGBM Classifier의 상승/하락 확률 차이(prob_up - prob_down)입니다. "
            "Optuna AutoML로 튜닝된 모델을 사용합니다. "
            "높일수록 방향 분류 모델을 더 신뢰합니다."
        ),
        "default": 0.15,
        "min": 0.0,
        "max": 1.0,
    },
    "w_mtf_1h": {
        "label": "MTF 1시간 신호",
        "description": (
            "15분 봉을 1시간으로 리샘플링한 후 EMA20 > EMA50이면 +1, "
            "EMA20 < EMA50이면 -1 신호입니다. "
            "중기 추세를 반영합니다. "
            "높일수록 1시간 추세 필터를 강하게 적용합니다."
        ),
        "default": 0.13,
        "min": 0.0,
        "max": 1.0,
    },
    "w_mtf_4h": {
        "label": "MTF 4시간 신호",
        "description": (
            "15분 봉을 4시간으로 리샘플링한 후 EMA20 > EMA50이면 +1, "
            "EMA20 < EMA50이면 -1 신호입니다. "
            "장기 추세 방향을 반영합니다. "
            "높일수록 4시간 추세 필터를 강하게 적용합니다."
        ),
        "default": 0.07,
        "min": 0.0,
        "max": 1.0,
    },
    "threshold_normal": {
        "label": "진입 임계값 (일반 변동성)",
        "description": (
            "일반(low/medium) 변동성 국면에서 앙상블 스코어가 이 값 이상이어야 "
            "LONG/SHORT 신호가 발생합니다. "
            "낮출수록 신호가 더 자주 발생하고, 높일수록 고확신 신호만 발생합니다."
        ),
        "default": 0.08,
        "min": 0.01,
        "max": 0.5,
    },
    "threshold_high_vol": {
        "label": "진입 임계값 (고변동성)",
        "description": (
            "ATR 백분위가 67% 이상인 고변동성 국면에서 사용되는 강화된 임계값입니다. "
            "고변동성 시 더 높은 확신을 요구해 오신호를 줄입니다."
        ),
        "default": 0.12,
        "min": 0.01,
        "max": 0.5,
    },
    "min_ml_prob_normal": {
        "label": "ML 최소 확률 (일반 변동성)",
        "description": (
            "LONG/SHORT 신호 발생을 위한 ML 분류 모델의 최소 방향 확률입니다. "
            "0.45 = ML이 45% 이상 확신해야 신호 발생. "
            "높일수록 ML 확신이 높은 경우만 거래합니다."
        ),
        "default": 0.45,
        "min": 0.33,
        "max": 0.8,
    },
    "min_ml_prob_high_vol": {
        "label": "ML 최소 확률 (고변동성)",
        "description": (
            "고변동성 국면에서 강화된 ML 최소 확률 임계값입니다. "
            "변동성이 클 때 더 높은 ML 확신을 요구합니다."
        ),
        "default": 0.52,
        "min": 0.33,
        "max": 0.8,
    },
}

DEFAULT_CONFIG = {k: v["default"] for k, v in WEIGHT_META.items()}


class EnsembleConfigUpdate(BaseModel):
    w_tech: float = Field(0.35, ge=0.0, le=1.0)
    w_ml_regressor: float = Field(0.30, ge=0.0, le=1.0)
    w_ml_classifier: float = Field(0.15, ge=0.0, le=1.0)
    w_mtf_1h: float = Field(0.13, ge=0.0, le=1.0)
    w_mtf_4h: float = Field(0.07, ge=0.0, le=1.0)
    threshold_normal: float = Field(0.08, ge=0.01, le=0.5)
    threshold_high_vol: float = Field(0.12, ge=0.01, le=0.5)
    min_ml_prob_normal: float = Field(0.45, ge=0.33, le=0.8)
    min_ml_prob_high_vol: float = Field(0.52, ge=0.33, le=0.8)

    @validator("w_tech", "w_ml_regressor", "w_ml_classifier", "w_mtf_1h", "w_mtf_4h")
    def check_non_negative(cls, v):
        if v < 0:
            raise ValueError("Weight must be >= 0")
        return round(v, 4)

    def weight_sum(self) -> float:
        return self.w_tech + self.w_ml_regressor + self.w_ml_classifier + self.w_mtf_1h + self.w_mtf_4h


def _get_config_from_db() -> dict:
    """DB에서 설정 로드. 없으면 기본값 반환."""
    try:
        rows = fetch_all("""
            SELECT key, value FROM features.ensemble_config
        """)
        if rows:
            return {r["key"]: float(r["value"]) for r in rows}
    except Exception:
        pass
    return DEFAULT_CONFIG.copy()


def _save_config_to_db(config: dict) -> None:
    """앙상블 설정을 DB에 upsert."""
    with db_conn_cursor() as (conn, cur):
        for key, value in config.items():
            cur.execute("""
                INSERT INTO features.ensemble_config (key, value, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = NOW()
            """, (key, value))
        conn.commit()


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/ensemble/config")
def get_ensemble_config():
    """
    현재 앙상블 가중치 + 임계값 조회.
    각 파라미터에 대한 설명(description), 기본값(default), 범위(min/max)를 함께 반환합니다.
    """
    current = _get_config_from_db()
    result = {}
    for key, meta in WEIGHT_META.items():
        result[key] = {
            "value": current.get(key, meta["default"]),
            "label": meta["label"],
            "description": meta["description"],
            "default": meta["default"],
            "min": meta["min"],
            "max": meta["max"],
        }

    # 가중치 합계 정보 추가 (UI에서 100% 표시용)
    weight_keys = ["w_tech", "w_ml_regressor", "w_ml_classifier", "w_mtf_1h", "w_mtf_4h"]
    weight_sum = sum(current.get(k, WEIGHT_META[k]["default"]) for k in weight_keys)
    result["_meta"] = {
        "weight_sum": round(weight_sum, 4),
        "weight_sum_warning": None if abs(weight_sum - 1.0) < 0.01 else
            f"가중치 합계가 {weight_sum:.3f}입니다. 1.0에 가깝게 설정하는 것을 권장합니다.",
    }
    return result


@router.put("/ensemble/config")
def update_ensemble_config(body: EnsembleConfigUpdate):
    """
    앙상블 가중치 + 임계값 업데이트.
    가중치 합계는 1.0에 가깝게 설정하는 것을 권장합니다 (강제는 아님).
    변경 즉시 신호 계산에 반영됩니다.
    """
    config = body.dict()
    weight_sum = body.weight_sum()

    try:
        _save_config_to_db(config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB 저장 실패: {e}")

    # 신호 서비스 캐시 무효화 (즉시 반영)
    reload_ensemble_config()

    return {
        "status": "updated",
        "config": config,
        "weight_sum": round(weight_sum, 4),
        "warning": None if abs(weight_sum - 1.0) < 0.01 else
            f"가중치 합계: {weight_sum:.3f} (1.0 권장)",
    }


@router.post("/ensemble/config/reset")
def reset_ensemble_config():
    """앙상블 설정을 기본값으로 초기화합니다."""
    try:
        _save_config_to_db(DEFAULT_CONFIG)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB 저장 실패: {e}")

    reload_ensemble_config()
    return {
        "status": "reset",
        "config": DEFAULT_CONFIG,
        "message": "앙상블 설정이 기본값으로 초기화되었습니다.",
    }
