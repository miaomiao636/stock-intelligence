import json
import pytest

from src.analysis.validators import validate_llm_output
from src.analysis.afternoon_pipeline import _merge_llm_stocks


def payload(**extra):
    return json.dumps({"sector_recommendations": [], "stock_recommendations": [
        {"code": "600000", "name": "测试", "action": "watch", "confidence": 3, **extra}]})


@pytest.mark.parametrize("extra", [
    {"forecast_direction": "buy", "forecast_horizon_sessions": 3},
    {"forecast_direction": "up", "forecast_horizon_sessions": True},
    {"forecast_direction": "up", "forecast_horizon_sessions": 0},
    {"forecast_direction": "up", "forecast_horizon_sessions": 3.5},
    {"forecast_direction": "up", "forecast_horizon_sessions": 61},
    {"forecast_direction": "up"},
])
def test_invalid_explicit_forecast_is_rejected(extra):
    assert validate_llm_output(payload(**extra))[0] is False


def test_legacy_missing_forecast_stays_missing_not_inferred():
    valid, result, _ = validate_llm_output(payload())
    assert valid
    assert "forecast_direction" not in result["stock_recommendations"][0]
    assert validate_llm_output(payload(forecast_direction="up", forecast_horizon_sessions=5))[0]
    assert validate_llm_output(payload(forecast_direction="unknown", forecast_horizon_sessions=None))[0]


def test_afternoon_does_not_silently_recycle_old_prediction():
    original = [{"code": "600000", "forecast_direction": "up", "forecast_horizon_sessions": 5}]
    assert _merge_llm_stocks(original, [{"code": "600000", "action": "watch"}], {})[0]["forecast_direction"] == "unknown"
    updated = _merge_llm_stocks(original, [{"code": "600000", "forecast_direction": "down", "forecast_horizon_sessions": 3}], {})[0]
    assert updated["forecast_direction"] == "down"
    assert updated["forecast_horizon_sessions"] == 3
