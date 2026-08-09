"""Slack message を共通 ChatService へ接続するバックグラウンドハンドラー。"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from example.chat import ChatService

from .store import SlackThreadKey, SlackThreadSessionStore, application_user_id

LOGGER = logging.getLogger(__name__)


class SlackMessageHandler:
    def __init__(
        self,
        service_factory: Callable[[str], ChatService],
        store: SlackThreadSessionStore,
        *,
        bot_user_id: str,
        update_interval: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._store = store
        self._bot_user_id = bot_user_id
        self._update_interval = max(0.0, update_interval)
        self._sleep = sleep
        self._executor = executor or ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="slack-chat"
        )

    def handle(
        self, event: dict[str, Any], ack: Callable[[], None], client: Any, *, team_id: str
    ) -> None:
        """ack は run 開始前に呼び、長時間処理は worker へ渡す。"""
        ack()
        if self._is_self_event(event):
            return
        user_id = event.get("user")
        text = event.get("text")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        event_id = event.get("event_id") or event.get("client_msg_id") or event.get("ts")
        values = (user_id, text, channel_id, thread_ts, event_id)
        if not all(isinstance(value, str) and value.strip() for value in values):
            return
        key = SlackThreadKey(team_id, channel_id, thread_ts)
        self._executor.submit(self._run, key, user_id, text, event_id, client)

    def _is_self_event(self, event: dict[str, Any]) -> bool:
        return bool(
            event.get("bot_id")
            or event.get("subtype") == "bot_message"
            or event.get("user") == self._bot_user_id
        )

    def _run(
        self,
        key: SlackThreadKey,
        slack_user_id: str,
        prompt: str,
        event_id: str,
        client: Any,
    ) -> None:
        try:
            binding = self._store.get(key)
            app_user_id = application_user_id(key.team_id, slack_user_id)
            service = self._service_factory(app_user_id)
            idempotency_key = f"slack-event:{event_id}"
            if binding is None:
                started = service.start(prompt, idempotency_key=idempotency_key)
                binding = self._store.create_if_absent(
                    key,
                    application_user_id=app_user_id,
                    session_id=started.session_id,
                )
            else:
                if binding.application_user_id != app_user_id:
                    self._post(client, key, "このスレッドは別の Slack 利用者に紐付いています。")
                    return
                started = service.start(
                    prompt,
                    session_id=binding.session_id,
                    idempotency_key=idempotency_key,
                )
            response = self._post(client, key, "受け付けました。処理中です…")
            self._consume(service, started.run, client, key, response)
        except Exception:
            LOGGER.exception("slack run failed team=%s channel=%s", key.team_id, key.channel_id)
            self._post(client, key, "処理を開始できませんでした。設定と権限を確認してください。")

    def _consume(
        self, service: ChatService, run: Any, client: Any, key: SlackThreadKey, response: Any
    ) -> None:
        text = ""
        last_update = 0.0
        for event in service.stream(run):
            now = time.monotonic()
            if event.type == "agent" and event.content:
                text += event.content
            elif event.type == "tool_started":
                text = f"{text}\n\n_ツール実行中: {event.payload.get('name', 'tool')}_"
            elif event.type == "progress" and event.content:
                text = f"{text}\n\n_進捗: {event.content}_"
            elif event.type == "terminal":
                state = event.payload.get("state", "unknown")
                text = f"{text}\n\n実行終了: {state}"
            terminal = event.type in {"final", "terminal"}
            if text and (now - last_update >= self._update_interval or terminal):
                self._update(client, response, text or "処理が完了しました。")
                last_update = now
        if not text:
            text = "処理が完了しました。"
        self._update(client, response, text)

    def _post(self, client: Any, key: SlackThreadKey, text: str) -> Any:
        return self._call_with_retry(
            client.chat_postMessage,
            channel=key.channel_id,
            thread_ts=key.thread_ts,
            text=text,
        )

    def _update(self, client: Any, response: Any, text: str) -> Any:
        channel = response.get("channel") if isinstance(response, dict) else response["channel"]
        timestamp = response.get("ts") if isinstance(response, dict) else response["ts"]
        return self._call_with_retry(
            client.chat_update,
            channel=channel,
            ts=timestamp,
            text=text,
        )

    def _call_with_retry(self, method: Callable[..., Any], **kwargs: Any) -> Any:
        for attempt in range(4):
            try:
                return method(**kwargs)
            except Exception as error:
                retry_after = self._retry_after(error)
                if retry_after is None or attempt == 3:
                    raise
                self._sleep(retry_after)
        raise RuntimeError("Slack API call failed")

    @staticmethod
    def _retry_after(error: Exception) -> float | None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        data = getattr(response, "data", None)
        if status != 429 and not (isinstance(data, dict) and data.get("error") == "ratelimited"):
            return None
        headers = getattr(response, "headers", {}) or {}
        try:
            return max(0.0, float(headers.get("Retry-After", 1)))
        except (TypeError, ValueError):
            return 1.0
