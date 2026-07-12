# -*- coding: utf-8 -*-
"""Recommendation Tracker - tracks performance of historical recommendations"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional


class RecommendationTracker:
    """Track and evaluate historical stock recommendations"""

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path(__file__).parent.parent.parent / "data"
        self.tracker_dir = self.data_dir / "tracker"
        self.tracker_dir.mkdir(parents=True, exist_ok=True)
        self.recommendations_dir = self.data_dir / "recommendations"

    def track_daily(self, date_str: str = None) -> Dict:
        """Run daily tracking for all historical recommendations.

        For each historical recommendation:
        1. Fetch current price for each stock
        2. Compare against entry/target/stop-loss
        3. Determine status: hit_target / stopped_out / active / expired
        4. Record tracking data
        """
        if date_str is None:
            date_str = date.today().isoformat()

        all_tracks = []

        # Scan all recommendation dates
        if not self.recommendations_dir.exists():
            return {"status": "no_data", "tracks": []}

        for rec_dir in sorted(self.recommendations_dir.iterdir()):
            if not rec_dir.is_dir():
                continue
            rec_date = rec_dir.name

            # Load morning recommendation
            morning_file = rec_dir / "morning.json"
            if not morning_file.exists():
                continue

            try:
                with open(morning_file) as f:
                    rec = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue

            stocks = rec.get("stock_recommendations", [])
            if not stocks:
                continue

            # Track each stock
            for stock in stocks:
                track = self._track_stock(stock, rec_date, date_str)
                all_tracks.append(track)

        # Save tracking results
        result = {
            "tracking_date": date_str,
            "tracked_at": datetime.now().isoformat(),
            "total_tracked": len(all_tracks),
            "tracks": all_tracks,
            "summary": self._calculate_summary(all_tracks),
        }

        self._save_tracking(result, date_str)
        return result

    def _track_stock(self, stock: Dict, rec_date: str, today: str) -> Dict:
        """Track a single stock recommendation"""
        code = stock.get("code", "")
        name = stock.get("name", "")
        sector = stock.get("sector", "")
        action = stock.get("action", "")
        entry_price = stock.get("entry_price", 0) or stock.get("timing", {}).get("entry_price", 0)
        target_price = stock.get("target_price", 0) or stock.get("timing", {}).get("target_observation_price", 0)
        stop_loss = stock.get("stop_loss_price", 0) or stock.get("timing", {}).get("stop_loss_price", 0)
        target_return = stock.get("target_return_pct", 0)
        horizon = stock.get("horizon", "short")
        horizon_days = stock.get("horizon_days", 3)

        # Fetch current price
        current_price = self._fetch_price(code)
        current_price_at_rec = stock.get("current_price", 0)

        # If no entry_price in data, use current_price_at_rec or fetch it
        if not entry_price or entry_price <= 0:
            entry_price = current_price_at_rec or current_price or 0

        # If still no target/stop-loss, compute from percentages
        if entry_price > 0:
            if not target_price or target_price <= 0:
                if target_return > 0:
                    target_price = round(entry_price * (1 + target_return / 100), 2)
                else:
                    target_price = round(entry_price * 1.05, 2)  # Default +5%
            if not stop_loss or stop_loss <= 0:
                sl_pct = abs(stock.get("stop_loss_pct", 5))
                stop_loss = round(entry_price * (1 - sl_pct / 100), 2)

        # Calculate returns
        actual_return_pct = 0
        if entry_price and entry_price > 0 and current_price and current_price > 0:
            actual_return_pct = round((current_price - entry_price) / entry_price * 100, 2)

        # Calculate holding days
        try:
            rec_dt = date.fromisoformat(rec_date)
            today_dt = date.fromisoformat(today)
            holding_days = (today_dt - rec_dt).days
        except (ValueError, TypeError):
            holding_days = 0

        # Determine status
        status = "active"
        failure_reason = ""

        if current_price and current_price > 0:
            # Check target hit
            if target_price > 0 and current_price >= target_price:
                status = "hit_target"
            # Check stop-loss
            elif stop_loss > 0 and current_price <= stop_loss:
                status = "stopped_out"
                failure_reason = f"触发止损: 现价¥{current_price:.2f} ≤ 止损价¥{stop_loss:.2f}"
            elif target_return > 0 and actual_return_pct >= target_return:
                status = "hit_target"
            # Check expiry
            elif holding_days > horizon_days * 2:
                status = "expired"
                if actual_return_pct < 0:
                    failure_reason = f"过期未达标: 持仓{holding_days}天，收益{actual_return_pct}%"
                else:
                    status = "active"  # Keep tracking if profitable
            # Deep loss
            elif actual_return_pct <= -10:
                status = "deep_loss"
                failure_reason = f"深度亏损: 浮亏{actual_return_pct}%"

        # 生成反馈分析
        feedback = self._generate_feedback(
            status, actual_return_pct, target_return, holding_days,
            horizon_days, entry_price, current_price, target_price, stop_loss,
            failure_reason, stock
        )

        return {
            "code": code,
            "name": name,
            "sector": sector,
            "action": action,
            "recommendation_date": rec_date,
            "horizon": horizon,
            "horizon_days": horizon_days,
            "entry_price": entry_price,
            "target_price": target_price,
            "stop_loss_price": stop_loss,
            "current_price": current_price or 0,
            "actual_return_pct": actual_return_pct,
            "target_return_pct": target_return,
            "holding_days": holding_days,
            "status": status,
            "failure_reason": failure_reason,
            "is_met_expectation": status in ("hit_target",),
            "is_failed": status in ("stopped_out", "deep_loss", "expired"),
            "red_flag": status in ("stopped_out", "deep_loss", "expired"),  # 🔴红标
            "performance_grade": feedback["grade"],
            "feedback": feedback["analysis"],
            "adjustment_suggestion": feedback["suggestion"],
            "recommendation_reason": stock.get("reason", ""),
            "reason_news": stock.get("reason_news", ""),
            "reason_policy": stock.get("reason_policy", ""),
            "reason_technical": stock.get("reason_technical", ""),
            "reason_fund": stock.get("reason_fund", ""),
        }

    def _generate_feedback(
        self, status, actual_return, target_return, holding_days,
        horizon_days, entry_price, current_price, target_price, stop_loss,
        failure_reason, stock
    ) -> Dict:
        """生成推荐反馈分析（符合预期→延伸推荐，不符合→红标+调整建议）"""
        grade = "D"
        analysis = ""
        suggestion = ""

        if status == "hit_target":
            grade = "A"
            analysis = f"✅ 达到预期目标！推荐时预期收益{target_return}%，实际收益{actual_return}%，持仓{holding_days}天。"
            suggestion = f"基于原推荐理由({stock.get('reason','')[:30]})，可考虑：1)止盈离场 2)上调目标价继续持有 3)关注同板块同类机会"

        elif status == "active" and actual_return > 0:
            grade = "B"
            analysis = f"🟡 盈利中但未达目标。当前收益{actual_return}%，目标{target_return}%，已完成{(actual_return/target_return*100 if target_return else 0):.0f}%。"
            suggestion = "继续持有观察，设好移动止盈。若接近目标可适当减仓锁利。"

        elif status == "active" and actual_return <= 0:
            grade = "C"
            analysis = f"🟡 持仓中，当前浮亏{actual_return}%。入场价¥{entry_price}，现价¥{current_price}，止损价¥{stop_loss}。"
            suggestion = "观望为主，若接近止损价果断离场。检查原推荐理由是否仍成立。"

        elif status == "stopped_out":
            grade = "D"
            analysis = f"🔴 止损出局！{failure_reason}。预期收益{target_return}%，实际亏损{actual_return}%。"
            suggestion = f"失败分析：1)入场时机可能偏早 2)止损位可能过紧 3)市场环境可能变化。建议：避免立即追回，等待新信号。"

        elif status == "deep_loss":
            grade = "F"
            analysis = f"🔴 深度亏损！浮亏{actual_return}%，远超止损线。原推荐理由：{stock.get('reason','')[:40]}。"
            suggestion = "立即评估是否割肉。失败原因可能是：1)推荐逻辑错误 2)市场系统性风险 3)个股黑天鹅。后续避免类似逻辑推荐。"

        elif status == "expired":
            grade = "C" if actual_return >= 0 else "D"
            if actual_return >= 0:
                analysis = f"🟡 持仓到期，微利{actual_return}%但未达目标{target_return}%。持仓{holding_days}天。"
                suggestion = "考虑减仓换股，资金效率偏低。原推荐板块可继续关注但换标的。"
            else:
                analysis = f"🔴 持仓到期且亏损{actual_return}%。{failure_reason}。"
                suggestion = "到期止损离场。反思：horizon设置是否合理？市场状态是否误判？"

        return {"grade": grade, "analysis": analysis, "suggestion": suggestion}

    def _calculate_summary(self, tracks: List[Dict]) -> Dict:
        """Calculate overall tracking summary"""
        if not tracks:
            return {}

        total = len(tracks)
        hit = sum(1 for t in tracks if t["status"] == "hit_target")
        stopped = sum(1 for t in tracks if t["status"] == "stopped_out")
        active = sum(1 for t in tracks if t["status"] == "active")
        deep_loss = sum(1 for t in tracks if t["status"] == "deep_loss")
        expired = sum(1 for t in tracks if t["status"] == "expired")

        returns = [t["actual_return_pct"] for t in tracks if t["actual_return_pct"] != 0]
        avg_return = sum(returns) / len(returns) if returns else 0

        winners = [r for r in returns if r > 0]
        losers = [r for r in returns if r < 0]
        avg_win = sum(winners) / len(winners) if winners else 0
        avg_loss = sum(losers) / len(losers) if losers else 0

        win_rate = hit / total * 100 if total > 0 else 0

        # Sector analysis
        sector_stats = {}
        for t in tracks:
            s = t.get("sector", "未知")
            if s not in sector_stats:
                sector_stats[s] = {"total": 0, "hit": 0, "failed": 0, "returns": []}
            sector_stats[s]["total"] += 1
            if t["is_met_expectation"]:
                sector_stats[s]["hit"] += 1
            if t["is_failed"]:
                sector_stats[s]["failed"] += 1
            if t["actual_return_pct"] != 0:
                sector_stats[s]["returns"].append(t["actual_return_pct"])

        sector_summary = {}
        for s, data in sector_stats.items():
            sector_summary[s] = {
                "total": data["total"],
                "hit": data["hit"],
                "failed": data["failed"],
                "win_rate": round(data["hit"] / data["total"] * 100, 1) if data["total"] > 0 else 0,
                "avg_return": round(sum(data["returns"]) / len(data["returns"]), 2) if data["returns"] else 0,
            }

        return {
            "total_tracked": total,
            "hit_target": hit,
            "stopped_out": stopped,
            "active": active,
            "deep_loss": deep_loss,
            "expired": expired,
            "win_rate_pct": round(win_rate, 1),
            "avg_return_pct": round(avg_return, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "best_return": round(max(returns), 2) if returns else 0,
            "worst_return": round(min(returns), 2) if returns else 0,
            "profit_loss_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0,
            "sector_summary": sector_summary,
        }

    def _save_tracking(self, result: Dict, date_str: str):
        """保存跟踪结果到文件"""
        import os
        track_file = self.tracker_dir / f"{date_str}.json"
        temp_file = track_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            os.replace(str(temp_file), str(track_file))
        except IOError as e:
            print(f"⚠️ 保存跟踪数据失败: {e}")
            raise

    def get_tracking(self, date_str: str = None) -> Optional[Dict]:
        """Get tracking data for a date"""
        if date_str is None:
            date_str = date.today().isoformat()
        track_file = self.tracker_dir / f"{date_str}.json"
        if not track_file.exists():
            return None
        try:
            with open(track_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def get_latest_tracking(self) -> Optional[Dict]:
        """Get the most recent tracking data"""
        files = sorted(self.tracker_dir.glob("*.json"), reverse=True)
        for f in files[:3]:
            try:
                with open(f) as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, IOError):
                continue
        return None

    def get_weekly_review(self, weeks: int = 4) -> List[Dict]:
        """Generate weekly performance reviews"""
        reviews = []
        today = date.today()

        for w in range(weeks):
            # Calculate week boundaries (Monday to Friday)
            week_end = today - timedelta(days=w * 7)
            week_start = week_end - timedelta(days=6)

            week_tracks = []

            # Collect all tracks within this week
            for d in range(7):
                day = week_start + timedelta(days=d)
                track_data = self.get_tracking(day.isoformat())
                if track_data and track_data.get("tracks"):
                    week_tracks.extend(track_data["tracks"])

            if not week_tracks:
                continue

            # Deduplicate by code (keep latest)
            seen = {}
            for t in week_tracks:
                key = t.get("code", "")
                if key not in seen or t.get("recommendation_date", "") > seen[key].get("recommendation_date", ""):
                    seen[key] = t
            unique_tracks = list(seen.values())

            summary = self._calculate_summary(unique_tracks)
            summary["week_start"] = week_start.isoformat()
            summary["week_end"] = week_end.isoformat()
            summary["week_label"] = f"{week_start.isoformat()} ~ {week_end.isoformat()}"

            # Top winners and losers
            sorted_tracks = sorted(unique_tracks, key=lambda t: t.get("actual_return_pct", 0), reverse=True)
            summary["top_winners"] = [
                {"code": t["code"], "name": t["name"], "return_pct": t["actual_return_pct"]}
                for t in sorted_tracks[:3] if t["actual_return_pct"] > 0
            ]
            summary["top_losers"] = [
                {"code": t["code"], "name": t["name"], "return_pct": t["actual_return_pct"],
                 "failure_reason": t.get("failure_reason", "")}
                for t in sorted_tracks[-3:] if t["actual_return_pct"] < 0
            ]

            reviews.append(summary)

        return reviews

    def get_improvement_suggestions(self) -> List[Dict]:
        """Analyze tracking data and suggest improvements"""
        tracking = self.get_latest_tracking()
        if not tracking or not tracking.get("tracks"):
            return []

        suggestions = []
        summary = tracking.get("summary", {})
        tracks = tracking.get("tracks", [])

        # Win rate check
        win_rate = summary.get("win_rate_pct", 0)
        if win_rate < 50:
            suggestions.append({
                "type": "win_rate",
                "priority": "high",
                "message": f"胜率偏低({win_rate}%)，建议：1)提高选股门槛 2)减少短线操作 3)增加确认信号",
            })

        # Sector analysis
        sector_summary = summary.get("sector_summary", {})
        for sector, data in sector_summary.items():
            if data.get("win_rate", 100) < 30 and data.get("total", 0) >= 3:
                suggestions.append({
                    "type": "sector_weakness",
                    "priority": "medium",
                    "message": f"板块'{sector}'胜率仅{data['win_rate']}%（{data['total']}只），建议降低该板块权重",
                })

        # Deep loss check
        deep_losses = [t for t in tracks if t["status"] == "deep_loss"]
        if deep_losses:
            codes = [f"{t['name']}({t['code']})" for t in deep_losses]
            suggestions.append({
                "type": "deep_loss",
                "priority": "high",
                "message": f"存在深度亏损股: {', '.join(codes)}，建议严格执行止损",
            })

        # Average return check
        avg_return = summary.get("avg_return_pct", 0)
        if avg_return < 0:
            suggestions.append({
                "type": "negative_return",
                "priority": "high",
                "message": f"平均收益为负({avg_return}%)，建议：1)优化选股策略 2)缩短持仓周期 3)加强止损执行",
            })

        return suggestions

    def _fetch_price(self, code: str) -> Optional[float]:
        """Fetch current price (B4: 复用统一接口)"""
        if not code:
            return None
        from src.data_collectors.realtime_prices import fetch_single_price
        price = fetch_single_price(code)
        return price if price > 0 else None
        """Save tracking results"""
        track_file = self.tracker_dir / f"{date_str}.json"
        with open(track_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
