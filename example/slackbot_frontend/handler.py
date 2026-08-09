"""Slack message を共通 ChatService へ接続するバックグラウンドハンドラー。"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from example.chat import ChatService, normalize_events

from .store import SlackThreadKey, SlackThreadSessionStore, application_user_id

LOGGER = logging.getLogger(__name__)
MAX_SLACK_TEXT_LENGTH = 3900


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
        self,
        event: dict[str, Any],
        ack: Callable[[], None],
        client: Any,
        *,
        team_id: str,
        event_id: str | None = None,
    ) -> None:
        """ack は run 開始前に呼び、長時間処理は worker へ渡す。"""
        ack()
        received_event_id = event_id or event.get("event_id") or event.get("ts")
        LOGGER.info(
            "slack.event.received event_id=%s type=%s channel=%s user=%s",
            received_event_id,
            event.get("type"),
            event.get("channel"),
            event.get("user"),
        )
        if self._is_self_event(event):
            LOGGER.info("slack.event.ignored_self event_id=%s", received_event_id)
            return
        user_id = event.get("user")
        text = event.get("text")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        received_event_id = received_event_id or event.get("client_msg_id")
        values = (user_id, text, channel_id, thread_ts, received_event_id)
        if not all(isinstance(value, str) and value.strip() for value in values):
            LOGGER.warning("slack.event.ignored_invalid event_id=%s", received_event_id)
            return
        key = SlackThreadKey(team_id, channel_id, thread_ts)
        self._executor.submit(self._run, key, user_id, text, received_event_id, client)

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
            LOGGER.info(
                "slack.run.started event_id=%s session_id=%s run_id=%s",
                event_id,
                started.session_id,
                started.run_id,
            )
            response = self._post(client, key, "受け付けました。処理中です…")
            self._consume(service, started.run, client, key, response)
        except Exception:
            LOGGER.exception("slack run failed team=%s channel=%s", key.team_id, key.channel_id)
            self._post(client, key, "処理を開始できませんでした。設定と権限を確認してください。")

    def _consume(
        self, service: ChatService, run: Any, client: Any, key: SlackThreadKey, response: Any
    ) -> None:
        streamed_text = ""
        final_text: str | None = None
        activity: list[str] = []
        tool_names: dict[str, str] = {}
        terminal_state: str | None = None
        last_update = 0.0
        for event in service.stream(run):
            now = time.monotonic()
            if event.type in {"tool_started", "tool_completed", "agent", "final", "terminal"}:
                LOGGER.info(
                    "slack.stream.event run_id=%s type=%s content_length=%d",
                    run.id,
                    event.type,
                    len(event.content or ""),
                )
            if event.type == "agent" and event.content:
                streamed_text += event.content
            elif event.type == "final" and event.content:
                # Agent chunks are useful while streaming, but the final event
                # is the canonical answer and must not be appended twice.
                final_text = event.content
            elif event.type == "tool_started":
                name = str(event.payload.get("name") or "tool")
                tool_id = str(event.payload.get("tool_id") or "")
                if tool_id:
                    tool_names[tool_id] = name
                activity.append(f"ツール開始: {name}")
            elif event.type == "tool_completed":
                tool_id = str(event.payload.get("tool_id") or "")
                name = tool_names.get(tool_id, tool_id or "tool")
                suffix = "（エラー）" if event.payload.get("is_error") else ""
                activity.append(f"ツール完了: {name}{suffix}")
            elif event.type == "progress" and event.content:
                activity.append(f"進捗: {event.content}")
            elif event.type == "terminal":
                terminal_state = str(event.payload.get("state", "unknown"))
            elif event.type == "unknown":
                activity.append(f"イベント: {event.raw_type}")
            terminal = event.type in {"final", "terminal"}
            message = self._compose_message(
                activity, final_text or streamed_text, terminal_state
            )
            if message and (now - last_update >= self._update_interval or terminal):
                self._update(client, response, message)
                LOGGER.info("slack.reply.updated run_id=%s length=%d", run.id, len(message))
                last_update = now
        if not final_text and hasattr(service, "events"):
            # A Firestore watch may close at the same moment as terminal state
            # reconciliation. Read the durable history once more so the final
            # answer is not lost at that boundary.
            try:
                for saved in normalize_events(service.events(run.id)):
                    if saved.type == "final" and saved.content:
                        final_text = saved.content
                    elif saved.type == "agent" and saved.content and not streamed_text:
                        streamed_text += saved.content
                    elif saved.type == "tool_started":
                        activity.append(f"ツール開始: {saved.payload.get('name') or 'tool'}")
                    elif saved.type == "tool_completed":
                        activity.append("ツール完了")
                LOGGER.info(
                    "slack.stream.history_fallback run_id=%s final_length=%d",
                    run.id,
                    len(final_text or ""),
                )
            except Exception:
                LOGGER.exception("slack.stream.history_fallback_failed run_id=%s", run.id)
        if terminal_state is None and hasattr(service, "get_run"):
            try:
                terminal_state = service.get_run(run.session_id, run.id).state.value
            except Exception:
                LOGGER.debug("slack.run.state_unavailable run_id=%s", run.id)
        message = self._compose_message(activity, final_text or streamed_text, terminal_state)
        final_message = message or "処理が完了しました。"
        self._update(client, response, final_message)
        LOGGER.info("slack.reply.updated run_id=%s length=%d", run.id, len(final_message))

    @staticmethod
    def _compose_message(
        activity: list[str], result: str, terminal_state: str | None
    ) -> str:
        """作業履歴と結果を Slack の安全な長さへ整形する。"""
        activity_text = "\n".join(dict.fromkeys(activity))
        prefix = f"作業内容:\n{activity_text}" if activity_text else ""
        result_text = f"最終結果:\n{result}" if result else ""
        state_text = f"実行終了: {terminal_state}" if terminal_state else ""
        suffix = "\n\n".join(part for part in (result_text, state_text) if part)
        if prefix and suffix:
            available = MAX_SLACK_TEXT_LENGTH - len(suffix) - 2
            prefix = SlackMessageHandler._truncate(prefix, max(0, available))
            message = f"{prefix}\n\n{suffix}" if prefix else suffix
        else:
            message = prefix or suffix
        return SlackMessageHandler._truncate(message, MAX_SLACK_TEXT_LENGTH)

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        if limit <= 1:
            return "…"[:limit]
        return text[: limit - 1] + "…"

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
