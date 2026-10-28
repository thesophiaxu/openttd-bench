"""Model-activity evidence for intercepted inference and native Codex OAuth."""
from typing import Any


def has_model_activity(calls: list, info: dict[str, Any]) -> bool:
    if calls:
        return True
    native = info.get("harness_session") or {}
    return (native.get("store") == "codex_cli"
            and native.get("sessions", 0) > 0
            and native.get("output_tokens", 0) > 0
            and (native.get("assistant_messages", 0) > 0 or native.get("tool_calls", 0) > 0))
