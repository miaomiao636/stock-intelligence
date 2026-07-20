# -*- coding: utf-8 -*-
"""飞书报告通知与交互式交易卡片。"""

from __future__ import annotations

import json
import os
import time
from typing import Dict

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class FeishuNotifier:
    """报告可走Webhook；交易卡片必须走可回调的自建应用。"""

    API_BASE = "https://open.feishu.cn/open-apis"

    def __init__(self):
        self.webhook_url = os.getenv("FEISHU_WEBHOOK_URL")
        self.app_id = os.getenv("FEISHU_APP_ID")
        self.app_secret = os.getenv("FEISHU_APP_SECRET")
        self.receive_id = os.getenv("FEISHU_RECEIVE_ID")
        self.receive_id_type = os.getenv("FEISHU_RECEIVE_ID_TYPE", "chat_id")
        self.verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN")
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip()
        try:
            self.retry_max = min(max(int(os.getenv("NOTIFY_RETRY_MAX", "3")), 1), 5)
        except ValueError:
            self.retry_max = 3

    def is_available(self) -> bool:
        return requests is not None and bool(self.webhook_url or self.is_interactive_available())

    def is_interactive_available(self) -> bool:
        return requests is not None and all((self.app_id, self.app_secret, self.receive_id, self.verification_token))

    def interactive_missing_fields(self) -> list[str]:
        missing = []
        if not self.app_id:
            missing.append("FEISHU_APP_ID")
        if not self.app_secret:
            missing.append("FEISHU_APP_SECRET")
        if not self.receive_id:
            missing.append("FEISHU_RECEIVE_ID")
        if not self.verification_token:
            missing.append("FEISHU_VERIFICATION_TOKEN")
        return missing

    @staticmethod
    def callback_path() -> str:
        return "/api/feishu/callback"

    def callback_url(self) -> str:
        if not self.public_base_url:
            return ""
        return f"{self.public_base_url.rstrip('/')}{self.callback_path()}"

    def check_callback_reachable(self, timeout: int = 5) -> Dict:
        """优先检查官方长连接，必要时回退到公网 HTTP 回调。"""
        mode = os.getenv("FEISHU_CALLBACK_MODE", "auto").strip().lower() or "auto"
        if mode not in {"auto", "ws", "http"}:
            return {"reachable": False, "reason": f"FEISHU_CALLBACK_MODE无效: {mode}"}
        ws_health = None
        if mode in {"auto", "ws"}:
            from src.notifier.feishu_ws import get_long_connection_health

            ws_health = get_long_connection_health()
            if ws_health.get("reachable"):
                return ws_health
            if mode == "ws":
                return ws_health
            # 当前部署以官方长连接为准。旧临时隧道只有显式启用时才可回退，
            # 避免长连接短暂抖动时被过期的 PUBLIC_BASE_URL 误导。
            http_fallback = os.getenv("FEISHU_HTTP_FALLBACK_ENABLED", "false").strip().lower()
            if http_fallback != "true":
                return ws_health
        if requests is None:
            return {"reachable": False, "reason": "requests依赖不可用"}
        callback_url = self.callback_url()
        if not callback_url:
            reason = "PUBLIC_BASE_URL未配置"
            if ws_health:
                reason = f"{ws_health.get('reason')}；{reason}"
            return {"reachable": False, "reason": reason, "transport": "none"}
        if not self.verification_token:
            return {"reachable": False, "reason": "FEISHU_VERIFICATION_TOKEN未配置"}
        payload = {
            "token": self.verification_token,
            "action": {"value": {"action": "ping", "order_id": "callback-healthcheck"}},
        }
        try:
            response = requests.post(callback_url, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            toast = data.get("toast", {}) if isinstance(data, dict) else {}
            reachable = (
                toast.get("type") == "success"
                and toast.get("content") == "飞书回调链路正常"
            )
            return {
                "reachable": reachable,
                "reason": "ok" if reachable else "回调响应内容不符合预期",
                "status_code": response.status_code,
                "transport": "http",
            }
        except Exception as exc:
            return {
                "reachable": False,
                "reason": f"公网回调探测失败（{type(exc).__name__}）",
                "transport": "http",
            }

    def send_message(self, title: str, content: str) -> Dict:
        card = {
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
            "elements": [{"tag": "markdown", "content": content}],
        }
        if self.is_interactive_available():
            return self._send_app_card(card)
        if not self.webhook_url or requests is None:
            return {"status": "skip", "reason": "飞书未配置"}
        return self._post_with_retry(self.webhook_url, {"msg_type": "interactive", "card": card})

    def send_report(self, report_content: str, report_type: str = "morning") -> Dict:
        title = {
            "morning": "盘前智能推荐",
            "afternoon": "下午盘中复核",
            "closing": "盘后复盘报告",
        }.get(report_type, "股票分析报告")
        return self.send_message(title, report_content)

    def send_trade_plan(self, order: Dict) -> Dict:
        """发送带确认、否决和暂停按钮的最终订单卡片。"""
        if not self.is_interactive_available():
            return {
                "status": "error",
                "message": "交易卡片需要配置飞书自建应用、接收ID和Verification Token",
            }
        return self._send_app_card(self.build_trade_card(order))

    def send_callback_probe(self) -> Dict:
        """发送一个只用于验证飞书回调链路的测试卡片。"""
        if not self.is_interactive_available():
            return {
                "status": "error",
                "message": "回调自检卡片需要完整的飞书应用配置",
            }
        return self._send_app_card(self.build_callback_probe_card())

    @staticmethod
    def build_trade_card(order: Dict) -> Dict:
        side = "买入" if order["action"] == "buy" else "卖出"
        deadline = order.get("veto_deadline") or "发送后5分钟"
        content = (
            f"**{side} {order['name']}（{order['code']}）**\n"
            f"数量：{order['quantity']}股\n"
            f"计划价：¥{order['planned_price']:.2f}\n"
            f"允许区间：¥{(order.get('min_price') or 0):.2f} - ¥{(order.get('max_price') or 0):.2f}\n"
            f"原因：{order.get('reason') or '未提供'}\n"
            f"交互截止：{deadline}\n\n"
            "确认或无操作都必须在执行前重新取价和通过全部风控。"
        )
        def button(text: str, action: str, button_type: str):
            return {
                "tag": "button",
                "text": {"tag": "plain_text", "content": text},
                "type": button_type,
                "value": {"action": action, "order_id": order["order_id"]},
            }
        return {
            "header": {"title": {"tag": "plain_text", "content": "模拟交易最终计划"}, "template": "orange"},
            "elements": [
                {"tag": "markdown", "content": content},
                {"tag": "action", "actions": [
                    button("确认执行", "confirm", "primary"),
                    button("否决本次", "veto", "danger"),
                    button("暂停今日交易", "pause_day", "default"),
                ]},
            ],
        }

    @staticmethod
    def build_callback_probe_card() -> Dict:
        return {
            "header": {"title": {"tag": "plain_text", "content": "飞书回调自检"}, "template": "green"},
            "elements": [
                {"tag": "markdown", "content": "点击下方按钮后，如果服务端回调链路正常，你会看到成功提示。"},
                {"tag": "action", "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "验证回调"},
                    "type": "primary",
                    "value": {"action": "ping", "order_id": "feishu-probe"},
                }]},
            ],
        }

    def verify_callback(self, payload: Dict) -> bool:
        token = payload.get("token") or payload.get("header", {}).get("token")
        return bool(self.verification_token) and token == self.verification_token

    def is_legacy_card_callback(self, payload: Dict, headers: Dict | None = None) -> bool:
        headers = headers or {}
        return bool(
            payload.get("action")
            and payload.get("open_message_id")
            and payload.get("app_id") == self.app_id
            and headers.get("x-lark-signature")
            and headers.get("x-lark-request-timestamp")
        )

    @staticmethod
    def parse_card_action(payload: Dict) -> Dict:
        event = payload.get("event", {})
        action = payload.get("action") or event.get("action") or event.get("payload") or {}
        if isinstance(action, str):
            try:
                action = json.loads(action)
            except json.JSONDecodeError:
                action = {"action": action}
        value = action.get("value", {})
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = {"action": value}
        elif not isinstance(value, dict):
            value = {}
        operator = event.get("operator", {})
        if not operator and payload.get("operator"):
            operator = payload.get("operator", {})
        if not operator and payload.get("user"):
            operator = payload.get("user", {})
        return {
            "action": value.get("action") or action.get("action") or action.get("type"),
            "order_id": value.get("order_id") or value.get("id") or action.get("order_id"),
            "actor": operator.get("open_id") or operator.get("user_id") or "feishu_user",
            "event_id": payload.get("header", {}).get("event_id"),
        }

    def _tenant_token(self) -> str:
        response = requests.post(
            f"{self.API_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise RuntimeError(data.get("msg", "无法获取tenant_access_token"))
        return data["tenant_access_token"]

    def _send_app_card(self, card: Dict) -> Dict:
        last_error = ""
        for attempt in range(self.retry_max):
            try:
                token = self._tenant_token()
                url = f"{self.API_BASE}/im/v1/messages?receive_id_type={self.receive_id_type}"
                response = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "receive_id": self.receive_id,
                        "msg_type": "interactive",
                        "content": json.dumps(card, ensure_ascii=False),
                    },
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
                if data.get("code") == 0:
                    return {"status": "success", "data": data.get("data")}
                last_error = data.get("msg", "飞书返回未知错误")
            except Exception as exc:
                last_error = str(exc)
            if attempt + 1 < self.retry_max:
                time.sleep(min(2 ** attempt, 4))
        return {"status": "error", "message": last_error, "attempts": self.retry_max}

    def _post_with_retry(self, url: str, payload: Dict) -> Dict:
        last_error = ""
        for attempt in range(self.retry_max):
            try:
                response = requests.post(url, json=payload, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data.get("code", 0) == 0:
                    return {"status": "success"}
                last_error = data.get("msg", "未知错误")
            except Exception as exc:
                last_error = str(exc)
            if attempt + 1 < self.retry_max:
                time.sleep(min(2 ** attempt, 4))
        return {"status": "error", "message": last_error, "attempts": self.retry_max}
