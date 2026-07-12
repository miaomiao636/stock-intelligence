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

    def is_available(self) -> bool:
        return requests is not None and bool(self.webhook_url or self.is_interactive_available())

    def is_interactive_available(self) -> bool:
        return requests is not None and all((self.app_id, self.app_secret, self.receive_id, self.verification_token))

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
        title = "盘前智能推荐" if report_type == "morning" else "盘后复盘报告"
        return self.send_message(title, report_content)

    def send_trade_plan(self, order: Dict) -> Dict:
        """发送带确认、否决和暂停按钮的最终订单卡片。"""
        if not self.is_interactive_available():
            return {
                "status": "error",
                "message": "交易卡片需要配置飞书自建应用、接收ID和Verification Token",
            }
        return self._send_app_card(self.build_trade_card(order))

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

    def verify_callback(self, payload: Dict) -> bool:
        token = payload.get("token") or payload.get("header", {}).get("token")
        return bool(self.verification_token) and token == self.verification_token

    @staticmethod
    def parse_card_action(payload: Dict) -> Dict:
        event = payload.get("event", {})
        action = event.get("action", {})
        value = action.get("value", {})
        if isinstance(value, str):
            value = json.loads(value)
        operator = event.get("operator", {})
        return {
            "action": value.get("action"),
            "order_id": value.get("order_id"),
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
            return {"status": "success", "data": data.get("data")} if data.get("code") == 0 else {"status": "error", "message": data.get("msg")}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def _post_with_retry(url: str, payload: Dict) -> Dict:
        last_error = ""
        for attempt in range(2):
            try:
                response = requests.post(url, json=payload, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data.get("code", 0) == 0:
                    return {"status": "success"}
                last_error = data.get("msg", "未知错误")
            except Exception as exc:
                last_error = str(exc)
            if attempt == 0:
                time.sleep(1)
        return {"status": "error", "message": last_error}

