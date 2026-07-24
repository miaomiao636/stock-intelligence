# -*- coding: utf-8 -*-
"""飞书官方长连接接收器与本地健康状态。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from dotenv import load_dotenv

from src.notifier.feishu import FeishuNotifier
from src.notifier.feishu_actions import ALLOWED_TRADE_ACTIONS, process_card_action

try:
    # 官方 SDK 在导入时创建其 WebSocket 事件循环，必须早于 asyncio.run()。
    from lark_channel import Events, FeishuChannel, LogLevel, SecurityConfig
except ImportError:  # pragma: no cover - 由运行时健康状态给出明确提示
    Events = FeishuChannel = LogLevel = SecurityConfig = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATUS_PATH = PROJECT_ROOT / "data" / "runtime" / "feishu_ws_status.json"
logger = logging.getLogger("stock_intelligence.feishu_ws")

_PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def configure_direct_feishu_connection() -> None:
    """长连接绕开桌面代理，避免本地代理重启导致09:35回调误判离线。"""
    for name in _PROXY_ENV_NAMES:
        os.environ.pop(name, None)
    existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    entries = [item.strip() for item in existing.split(",") if item.strip()]
    for host in ("localhost", "127.0.0.1", "open.feishu.cn", ".feishu.cn"):
        if host not in entries:
            entries.append(host)
    value = ",".join(entries)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value


def _status_path(path: Optional[Path] = None) -> Path:
    if path is not None:
        return Path(path)
    configured = os.getenv("FEISHU_WS_STATUS_PATH", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_STATUS_PATH


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _write_status(path: Path, status: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def get_long_connection_health(
    status_path: Optional[Path] = None,
    *,
    max_age_seconds: int = 45,
    now: Optional[datetime] = None,
) -> Dict:
    """读取独立长连接进程写入的心跳，不暴露凭据或原始事件。"""
    path = _status_path(status_path)
    if not path.exists():
        return {"reachable": False, "reason": "飞书长连接未启动", "transport": "ws"}
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        heartbeat = datetime.fromisoformat(status["heartbeat_at"].replace("Z", "+00:00"))
        current = now or _utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (current - heartbeat).total_seconds())
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"reachable": False, "reason": "飞书长连接状态文件无效", "transport": "ws"}

    pid = status.get("pid")
    process_alive = False
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            process_alive = True
        except PermissionError:
            process_alive = True
        except (ProcessLookupError, OSError):
            process_alive = False

    ready = (
        status.get("state") == "connected"
        and status.get("ready") is True
        and process_alive
        and age_seconds <= max_age_seconds
    )
    if ready:
        reason = "ok"
    elif not process_alive:
        reason = "飞书长连接进程未运行"
    elif age_seconds > max_age_seconds:
        reason = "飞书长连接心跳已过期"
    else:
        reason = f"飞书长连接状态异常（{status.get('state') or 'unknown'}）"
    return {
        "reachable": ready,
        "reason": reason,
        "transport": "ws",
        "state": status.get("state") or "unknown",
        "heartbeat_age_seconds": round(age_seconds, 1),
    }


def card_event_to_payload(event: Any) -> Dict:
    """把 SDK 的 CardActionEvent 转成现有解析器可消费的结构。"""
    raw = getattr(event, "raw", None)
    payload = dict(raw) if isinstance(raw, dict) else {}
    event_payload = payload.get("event")
    event_payload = dict(event_payload) if isinstance(event_payload, dict) else {}

    operator = getattr(event, "operator", None)
    operator_payload = event_payload.get("operator")
    operator_payload = dict(operator_payload) if isinstance(operator_payload, dict) else {}
    for key in ("open_id", "user_id", "name"):
        value = getattr(operator, key, None)
        if value and not operator_payload.get(key):
            operator_payload[key] = value

    sdk_action = getattr(event, "action", None)
    action_payload = event_payload.get("action")
    action_payload = dict(action_payload) if isinstance(action_payload, dict) else {}
    if "value" not in action_payload:
        action_payload["value"] = getattr(sdk_action, "value", None)
    if not action_payload.get("tag"):
        action_payload["tag"] = getattr(sdk_action, "tag", None) or ""

    event_payload["operator"] = operator_payload
    event_payload["action"] = action_payload
    payload["event"] = event_payload

    header = payload.get("header")
    header = dict(header) if isinstance(header, dict) else {}
    if not header.get("event_id"):
        raw_event_id = payload.get("event_id") or payload.get("uuid")
        identity = {
            "message_id": getattr(event, "message_id", ""),
            "chat_id": getattr(event, "chat_id", ""),
            "operator": operator_payload,
            "action": action_payload,
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()
        header["event_id"] = str(raw_event_id or f"ws-{digest}")
    payload["header"] = header
    return payload


class FeishuLongConnection:
    """在独立进程中维护飞书长连接并复用现有交易决策逻辑。"""

    def __init__(
        self,
        *,
        status_path: Optional[Path] = None,
        notifier=None,
        action_processor: Callable = process_card_action,
        execute_confirm: Optional[Callable[[], Dict]] = None,
        node_id: Optional[str] = None,
    ):
        self.status_path = _status_path(status_path)
        self.notifier = notifier or FeishuNotifier()
        self.action_processor = action_processor
        self.execute_confirm = execute_confirm or self._execute_due_orders
        self.node_id = (
            node_id
            or os.getenv("FEISHU_CALLBACK_NODE_ID", "").strip()
            or socket.gethostname()
        )
        self.stop_event = asyncio.Event()
        self.background_tasks: set[asyncio.Task] = set()
        self.status: Dict = {
            "pid": os.getpid(),
            "node_id": self.node_id,
            "state": "starting",
            "ready": False,
        }

    @staticmethod
    def _execute_due_orders() -> Dict:
        from src.paper_trading.workflow import PaperTradingWorkflow

        return PaperTradingWorkflow().execute_due_orders()

    def update_status(self, **changes) -> None:
        self.status.update(changes)
        self.status["pid"] = os.getpid()
        self.status["node_id"] = self.node_id
        self.status["heartbeat_at"] = _utc_now().isoformat()
        _write_status(self.status_path, self.status)

    def schedule_background(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    async def notify_result(self, title: str, content: str) -> None:
        try:
            await asyncio.to_thread(self.notifier.send_message, title, content)
        except Exception as exc:  # pragma: no cover - 仅记录错误类型，不写原始响应
            logger.warning("feishu_ws result_notification_failed error=%s", type(exc).__name__)

    async def execute_confirm_background(self, order_id: str) -> None:
        try:
            await asyncio.to_thread(self.execute_confirm)
            self.update_status(
                last_execution_at=_utc_now().isoformat(),
                last_execution_error=None,
            )
        except Exception as exc:  # pragma: no cover - 真实行情与网络异常
            self.update_status(last_execution_error=type(exc).__name__)
            await self.notify_result(
                "模拟订单执行失败",
                (
                    f"订单：{order_id}\n"
                    f"原因：{type(exc).__name__}\n"
                    f"处理节点：{self.node_id}"
                ),
            )
            logger.exception(
                "feishu_ws execution_failed node_id=%s error=%s",
                self.node_id,
                type(exc).__name__,
            )

    async def handle_card_action(self, event: Any) -> None:
        payload = card_event_to_payload(event)
        action = self.notifier.parse_card_action(payload)
        action_name = action.get("action")
        logger.info(
            "feishu_ws node_id=%s action=%s has_order_id=%s has_event_id=%s",
            self.node_id,
            action_name if action_name in ALLOWED_TRADE_ACTIONS | {"ping"} else "unknown",
            bool(action.get("order_id")),
            bool(action.get("event_id")),
        )
        try:
            result = await asyncio.to_thread(
                self.action_processor,
                action,
                execute_confirm=lambda: {"status": "queued"},
            )
        except Exception as exc:
            self.update_status(last_event_error=type(exc).__name__)
            self.schedule_background(
                self.notify_result(
                    "模拟订单操作失败",
                    (
                        f"订单：{action.get('order_id') or '未识别'}\n"
                        f"原因：{type(exc).__name__}\n"
                        f"处理节点：{self.node_id}"
                    ),
                )
            )
            logger.exception(
                "feishu_ws action_failed node_id=%s error=%s",
                self.node_id,
                type(exc).__name__,
            )
            return

        self.update_status(
            last_event_at=_utc_now().isoformat(),
            last_event_id=action.get("event_id"),
            last_event_error=None,
        )
        toast = result.get("toast", {})
        execution = result.get("execution") or {}
        if (
            action_name == "confirm"
            and toast.get("type") == "success"
            and execution.get("status") == "queued"
        ):
            self.schedule_background(
                self.execute_confirm_background(action.get("order_id") or "未识别")
            )
        if action_name == "ping":
            self.schedule_background(
                self.notify_result(
                    "飞书长连接自检",
                    (
                        f"{toast.get('content') or '飞书回调链路正常'}\n"
                        f"处理节点：{self.node_id}"
                    ),
                )
            )
        elif action_name in ALLOWED_TRADE_ACTIONS:
            self.schedule_background(
                self.notify_result(
                    "模拟订单操作结果",
                    (
                        f"订单：{action.get('order_id')}\n"
                        f"{toast.get('content') or '操作已处理'}\n"
                        f"处理节点：{self.node_id}"
                    ),
                )
            )

    async def run(self) -> None:
        if FeishuChannel is None:
            self.update_status(state="error", ready=False, last_error="SDKNotInstalled")
            raise RuntimeError("缺少 lark-channel-sdk，请先安装项目依赖")

        if not self.notifier.app_id or not self.notifier.app_secret:
            self.update_status(state="error", ready=False, last_error="MissingCredentials")
            raise RuntimeError("缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET")

        channel = FeishuChannel(
            app_id=self.notifier.app_id,
            app_secret=self.notifier.app_secret,
            verification_token=self.notifier.verification_token,
            transport="ws",
            # SDK 的 INFO 日志包含一次性连接参数，不应落盘。
            log_level=LogLevel.ERROR,
            security=SecurityConfig(mode="audit"),
        )
        channel.on(Events.CARD_ACTION, self.handle_card_action)
        channel.on(Events.RECONNECTING, lambda: self.update_status(state="reconnecting", ready=False))
        channel.on(Events.RECONNECTED, lambda: self.update_status(state="connected", ready=True))
        channel.on(
            Events.ERROR,
            lambda error: self.update_status(last_error=type(error).__name__),
        )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop_event.set)
            except (NotImplementedError, RuntimeError):  # pragma: no cover
                pass

        failed = False
        self.update_status(state="connecting", ready=False, last_error=None)
        try:
            await channel.connect_until_ready(timeout=30)
            self.update_status(state="connected", ready=True)
            while not self.stop_event.is_set():
                snapshot = channel.connection_snapshot()
                self.update_status(
                    state=snapshot.state,
                    ready=snapshot.ready,
                    reconnect_attempts=snapshot.reconnect_attempts,
                    last_error=type(snapshot.last_error).__name__
                    if snapshot.last_error and not isinstance(snapshot.last_error, str)
                    else ("ConnectionError" if snapshot.last_error else None),
                )
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=15)
                except asyncio.TimeoutError:
                    continue
        except Exception as exc:
            failed = True
            self.update_status(state="error", ready=False, last_error=type(exc).__name__)
            raise
        finally:
            try:
                await channel.disconnect()
            finally:
                if not failed:
                    self.update_status(state="stopped", ready=False)


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    configure_direct_feishu_connection()
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(FeishuLongConnection().run())


if __name__ == "__main__":
    main()
