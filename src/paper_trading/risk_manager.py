# -*- coding: utf-8 -*-
"""风控模块"""

from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class RiskManager:
    """风控管理"""

    def __init__(self, account: Dict, positions: List[Dict]):
        self.account = account
        self.positions = positions

        # 移动止盈配置：根据持仓周期动态设置
        self.trailing_stop_config = {
            "short": {"activation_pct": 3.0, "trail_pct": 2.0},   # 短期：盈利3%激活，回撤2%止盈
            "medium": {"activation_pct": 5.0, "trail_pct": 3.0},  # 中期：盈利5%激活，回撤3%止盈
            "long": {"activation_pct": 8.0, "trail_pct": 4.0},    # 长期：盈利8%激活，回撤4%止盈
        }

        # 时间止损配置（天数）
        self.time_stop_config = {
            "short": 5,    # 短期持仓超过5天触发时间止损
            "medium": 15,  # 中期持仓超过15天触发时间止损
            "long": 30,    # 长期持仓超过30天触发时间止损
        }
    
    def check_order(self, order: Dict) -> Dict:
        """检查订单是否符合风控规则（小资金自动放宽）"""

        code = order.get("code", "")
        amount = order.get("estimated_amount", 0)
        sector = order.get("sector", "")

        # Check total equity is valid (prevent division by zero)
        total_equity = self.account.get("total_equity", 0)
        if total_equity <= 0:
            return {"allowed": False, "reason": "账户总资产异常（为0或未初始化）"}

        # 检查现金是否充足
        if amount > self.account.get("cash", 0):
            return {"allowed": False, "reason": "现金不足"}

        # P1-4: 仓位上限从 strategy.yaml 单一来源读取（与 auto_trader 一致）
        from src.strategy.position_limits import get_position_limit
        max_position_pct = get_position_limit(total_equity, order.get("horizon", "short"))

        current_position = self._get_position_value(code)

        if (current_position + amount) / total_equity > max_position_pct:
            return {"allowed": False, "reason": f"单票仓位超过{max_position_pct*100:.0f}%"}

        # P1-4: 板块集中度从 strategy.yaml 单一来源读取
        from src.strategy.position_limits import get_sector_limit
        max_sector_pct = get_sector_limit(total_equity)
        current_sector_value = self._get_sector_value(sector)

        if (current_sector_value + amount) / total_equity > max_sector_pct:
            return {"allowed": False, "reason": f"板块仓位超过{max_sector_pct*100:.0f}%"}

        # P1-4: 现金保留比例从 strategy.yaml 单一来源读取
        from src.strategy.position_limits import get_cash_reserve_pct
        cash_reserve_pct = get_cash_reserve_pct(total_equity)
        min_cash = total_equity * cash_reserve_pct

        if self.account.get("cash", 0) - amount < min_cash:
            return {"allowed": False, "reason": f"现金低于保留比例{cash_reserve_pct*100:.0f}%"}

        return {"allowed": True, "reason": "通过风控检查"}
    
    def _get_position_value(self, code: str) -> float:
        """获取单个持仓市值"""
        for pos in self.positions:
            if pos.get("code") == code:
                return pos.get("market_value", 0)
        return 0
    
    def _get_sector_value(self, sector: str) -> float:
        """获取板块持仓市值"""
        total = 0
        for pos in self.positions:
            if pos.get("sector") == sector:
                total += pos.get("market_value", 0)
        return total

    def check_trailing_stop(self, position: Dict, current_price: float) -> Optional[Dict]:
        """检查移动止盈信号

        当价格上涨超过激活阈值后开始跟踪最高价，
        价格从最高点回撤超过trail_pct时触发止盈。

        Args:
            position: 持仓信息，需包含 entry_price, highest_price, period, code, name
            current_price: 当前价格

        Returns:
            止盈信号 dict 或 None
        """
        entry_price = position.get("entry_price", 0)
        if entry_price <= 0:
            return None

        period = position.get("period", "short")
        config = self.trailing_stop_config.get(period, self.trailing_stop_config["short"])

        # 计算当前收益率
        return_pct = (current_price - entry_price) / entry_price * 100

        # 更新最高价
        highest_price = position.get("highest_price", entry_price)
        if current_price > highest_price:
            highest_price = current_price
            position["highest_price"] = highest_price

        # 检查是否激活移动止盈（收益率超过激活阈值）
        if return_pct < config["activation_pct"]:
            return None

        # 已激活，检查是否从最高点回撤超过阈值
        drawdown_from_high = (highest_price - current_price) / highest_price * 100

        if drawdown_from_high >= config["trail_pct"]:
            logger.info(
                f"移动止盈触发: {position.get('code')} "
                f"最高价{highest_price:.2f}→当前{current_price:.2f} "
                f"回撤{drawdown_from_high:.1f}%>={config['trail_pct']}%"
            )
            return {
                "type": "trailing_stop",
                "code": position.get("code"),
                "name": position.get("name"),
                "highest_price": highest_price,
                "current_price": current_price,
                "drawdown_pct": round(drawdown_from_high, 2),
                "return_pct": round(return_pct, 2),
                "reason": f"移动止盈: 从最高{highest_price:.2f}回撤{drawdown_from_high:.1f}%",
            }

        return None

    def check_time_stop(self, position: Dict) -> Optional[Dict]:
        """检查时间止损信号

        持仓超过设定天数且未盈利时触发时间止损。

        Args:
            position: 持仓信息，需包含 entry_date, period, code, name, current_price, entry_price

        Returns:
            时间止损信号 dict 或 None
        """
        from datetime import datetime

        entry_date_str = position.get("entry_date")
        if not entry_date_str:
            return None

        try:
            entry_date = datetime.strptime(str(entry_date_str)[:10], "%Y-%m-%d")
            # P2: 用交易日而非自然日计算持仓天数（排除周末/节假日）
            from src.data_collectors.trading_calendar import count_trading_days
            days_held = count_trading_days(entry_date.date(), datetime.now().date())
        except (ValueError, TypeError):
            return None

        period = position.get("period", "short")
        max_days = self.time_stop_config.get(period, self.time_stop_config["short"])

        if days_held < max_days:
            return None

        # 检查是否亏损
        entry_price = position.get("entry_price", 0)
        current_price = position.get("current_price", 0)
        if entry_price <= 0 or current_price <= 0:
            return None

        return_pct = (current_price - entry_price) / entry_price * 100

        # 只有亏损或微利（<1%）时才触发时间止损
        if return_pct >= 1.0:
            return None

        logger.info(
            f"时间止损触发: {position.get('code')} "
            f"持有{days_held}天>={max_days}天 收益{return_pct:.1f}%"
        )
        return {
            "type": "time_stop",
            "code": position.get("code"),
            "name": position.get("name"),
            "days_held": days_held,
            "max_days": max_days,
            "return_pct": round(return_pct, 2),
            "reason": f"时间止损: 持有{days_held}天, 收益仅{return_pct:.1f}%",
        }

    def check_sector_concentration(self, sector: str, new_amount: float) -> Dict:
        """检查板块集中度限制

        Args:
            sector: 板块名称
            new_amount: 新增金额

        Returns:
            {"allowed": bool, "reason": str, "sector_pct": float}
        """
        total_equity = self.account.get("total_equity", 0)
        if total_equity <= 0:
            return {"allowed": False, "reason": "账户总资产异常", "sector_pct": 0}

        # P1-4: 板块集中度上限从 strategy.yaml 单一来源读取
        from src.strategy.position_limits import get_sector_limit
        max_sector_pct = get_sector_limit(total_equity)

        current_sector_value = self._get_sector_value(sector)
        new_sector_pct = (current_sector_value + new_amount) / total_equity

        if new_sector_pct > max_sector_pct:
            return {
                "allowed": False,
                "reason": f"板块[{sector}]集中度{new_sector_pct*100:.0f}%超过{max_sector_pct*100:.0f}%",
                "sector_pct": round(new_sector_pct * 100, 1),
            }

        return {
            "allowed": True,
            "reason": "板块集中度正常",
            "sector_pct": round(new_sector_pct * 100, 1),
        }
