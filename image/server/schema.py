"""Schema loading and argument coercion for the control plane (from schema.json)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SchemaError(ValueError):
    pass


class Schema:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.commands: dict[str, dict[str, Any]] = {c["name"]: c for c in data["commands"]}
        self.order_flags: dict[str, int] = dict(data["order_flags"])

    @classmethod
    def load(cls, path: str | Path) -> Schema:
        return cls(json.loads(Path(path).read_text()))

    def get(self, name: str) -> dict[str, Any]:
        try:
            return self.commands[name]
        except KeyError:
            raise SchemaError(f"unknown command {name!r}; see `ottd docs`") from None

    def coerce(self, name: str, raw: dict[str, Any] | None) -> dict[str, Any]:
        """Validate and coerce arguments; flag lists become the engine's bit mask."""
        spec = self.get(name)
        raw = dict(raw or {})
        out: dict[str, Any] = {}
        known = {a["name"] for a in spec["args"]}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise SchemaError(f"{name}: unknown argument(s) {unknown}; accepted: {sorted(known)}")
        for arg in spec["args"]:
            key = arg["name"]
            if key not in raw or raw[key] is None:
                if arg["required"]:
                    raise SchemaError(f"{name}: missing required argument {key!r}")
                if arg["default"] is None:
                    continue
                value = arg["default"]
            else:
                value = raw[key]
            out[key] = self._coerce_value(name, arg, value)
        return out

    def _coerce_value(self, cmd: str, arg: dict[str, Any], value: Any) -> Any:
        kind = arg["type"]
        key = arg["name"]
        if kind == "str_list" and key == "flags":
            names = value if isinstance(value, list) else [value]
            mask = 0
            for item in names:
                if isinstance(item, int):
                    mask |= item
                    continue
                item = str(item)
                if item not in self.order_flags:
                    raise SchemaError(f"{cmd}: unknown order flag {item!r}; known: {sorted(self.order_flags)}")
                mask |= self.order_flags[item]
            return mask
        if kind == "int":
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, str):
                try:
                    return int(value, 10)
                except ValueError:
                    raise SchemaError(f"{cmd}: argument {key!r} must be an integer, got {value!r}") from None
            raise SchemaError(f"{cmd}: argument {key!r} must be an integer, got {value!r}")
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                return value.lower() in ("1", "true", "yes", "y", "on")
            raise SchemaError(f"{cmd}: argument {key!r} must be a boolean")
        if kind == "str":
            value = str(value)
            choices = arg.get("choices")
            if choices and value not in choices:
                raise SchemaError(f"{cmd}: argument {key!r} must be one of {choices}, got {value!r}")
            return value
        if kind == "int_list":
            if not isinstance(value, list):
                raise SchemaError(f"{cmd}: argument {key!r} must be a list of integers")
            return [int(v) for v in value]
        if kind == "str_list":
            if not isinstance(value, list):
                value = [value]
            return [str(v) for v in value]
        raise SchemaError(f"{cmd}: unsupported argument type {kind!r}")


__all__ = ["Schema", "SchemaError"]
