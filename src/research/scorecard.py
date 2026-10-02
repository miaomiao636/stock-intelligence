"""Descriptive direction accuracy, matched baseline and explicit coverage."""

from collections import Counter

from src.research.maturity import PROTOCOL_VERSION, cohort_key


def _summary(rows: list[dict]) -> dict:
    counts = Counter(row["status"] for row in rows)
    result = {name: counts[name] for name in ("correct", "incorrect", "pending", "unavailable", "excluded", "duplicate")}
    evaluable = counts["correct"] + counts["incorrect"]
    eligible = evaluable + counts["pending"] + counts["unavailable"]
    actual_up = sum(row.get("actual_direction") == "up" for row in rows if row["status"] in {"correct", "incorrect"})
    result.update(records_total=len(rows), eligible=eligible, evaluable=evaluable,
                  accuracy_pct=round(100 * counts["correct"] / evaluable, 2) if evaluable else None,
                  always_up_accuracy_pct=round(100 * actual_up / evaluable, 2) if evaluable else None,
                  coverage_pct=round(100 * evaluable / eligible, 2) if eligible else None)
    return result


def build_scorecard(judgments: list[dict], reviews: list[dict], *, as_of: str) -> dict:
    """Input rows must already be filtered on both known_at and as_of.

    First recorded forecast per stock / Shanghai judgment day / horizon wins.
    Later calls, revisions or direction reversals cannot add independent trials.
    Different days still overlap and are not statistically independent either.
    """
    latest = {}
    for review in reviews:
        if review.get("review_kind") == PROTOCOL_VERSION and str(review.get("review_id", "")).startswith("maturity-"):
            latest.setdefault(review["judgment_id"], review)
    seen, rows, exclusions = set(), [], Counter()
    # Stable input row order breaks exact timestamp ties; random IDs must not
    # select a different opinion than the maturity runner's first-record rule.
    for item in sorted(judgments, key=lambda x: (x["as_of"], x["known_at"])):
        spec = item.get("forecast_spec") or {}
        row = {"horizon_sessions": spec.get("horizon_sessions"), "status": "pending"}
        key = cohort_key(item)
        if spec.get("status") != "registered":
            row["status"] = "excluded"
            exclusions[spec.get("reason") or "explicit_forecast_missing"] += 1
        elif key in seen:
            row["status"] = "duplicate"
            exclusions["same_stock_day_horizon_repeat"] += 1
        else:
            seen.add(key)
            if item.get("history_incomplete"):
                row["status"] = "excluded"
                exclusions["history_incomplete"] += 1
            elif item["judgment_id"] in latest:
                review = latest[item["judgment_id"]]
                if review.get("prediction_status") in {"correct", "incorrect"} and review.get("actual_direction") in {"up", "down", "flat"}:
                    row.update(status=review["prediction_status"], actual_direction=review["actual_direction"])
                else:
                    row["status"] = "unavailable"
                    exclusions[review.get("exclusion_reason") or "target_close_unavailable"] += 1
        rows.append(row)
    horizons = sorted({row["horizon_sessions"] for row in rows if row["horizon_sessions"] is not None})
    if any(row["horizon_sessions"] is None for row in rows):
        horizons.append(None)
    return {"as_of": as_of, "protocol_version": PROTOCOL_VERSION,
            "summary": _summary(rows),
            "by_horizon": [{"horizon_sessions": horizon,
                "summary": _summary([row for row in rows if row["horizon_sessions"] == horizon])} for horizon in horizons],
            "exclusion_reasons": dict(exclusions), "affects_trading": False,
            "sample_dependence": "overlapping_windows_not_independent",
            "cohort_policy": "first_forecast_per_stock_shanghai_day_horizon",
            "baseline_scope": "always_up_on_identical_evaluable_cohort",
            "limitations": ["方向命中不等于可成交、扣费后收益或盈利承诺。",
                "仅纳入当日留档且原始证据和版本齐全的显式预测；历史缺口不回填猜测。",
                "登记须在判断资料时点后 15 分钟内；盘中引用需新鲜，盘前可用明确时间的前收盘价。",
                "同股票同日同期限只计最早一条，跨日或不同期限仍可能相关，样本不是独立试验。",
                "覆盖率为已评估 / 可登记的去重样本（包含待到期和缺行情）；应与准确率一起看。",
                "待到期或未运行复核均暂列 pending；可评估样本为空时准确率是未知，不是 0%。",
                "这是描述性前向记录，不是经过样本外验证的策略；置信评分不是上涨概率。"]}
