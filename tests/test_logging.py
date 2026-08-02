import logging

from cas_hosting_adapter.logging import invocation_logger


def test_info_does_not_emit_raw_payload(caplog):
    caplog.set_level(logging.INFO, logger="cas_hosting_adapter")
    logger = invocation_logger(user_id="user", session_id="session", run_id="run")
    logger.info("running")
    logger.debug_payload("prompt", "sensitive prompt")
    assert "sensitive prompt" not in caplog.text
    assert caplog.records[0].user_id == "user"
    assert caplog.records[0].session_id == "session"


def test_debug_emits_raw_payload(caplog):
    caplog.set_level(logging.DEBUG, logger="cas_hosting_adapter")
    invocation_logger(user_id="user").debug_payload("tool_input", {"raw": "value"})
    assert "tool_input={'raw': 'value'}" in caplog.text
