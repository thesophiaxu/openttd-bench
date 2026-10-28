"""The single command schema shared by the client library, the control plane and the
GameScript dispatcher. Everything agent-facing (method surface, argument coercion,
`ottd docs`) derives from `COMMANDS`; a test asserts the GameScript registry matches."""

from openttd_bench.schema.commands import (
    COMMANDS,
    NAMESPACES,
    ORDER_FLAGS,
    Arg,
    Command,
    by_namespace,
    command,
    schema_dict,
    schema_json,
)

__all__ = [
    "COMMANDS",
    "NAMESPACES",
    "ORDER_FLAGS",
    "Arg",
    "Command",
    "by_namespace",
    "command",
    "schema_dict",
    "schema_json",
]
