"""Tiny JSON-shape validator for LLM JSON output (D-26: no jsonschema).

Shape DSL:
    {"type": "object", "required": [names], "properties": {name: shape}}
    {"type": "str"} {"type": "int"} {"type": "float"} {"type": "bool"}
    {"type": "list", "items": shape}
    {"type": "enum", "values": [...]}

`validate(obj, shape)` returns every violation as "<path>: <message>" (paths
start at "$"); [] when valid. Exactly one error per violation. Never raises on
bad data; a malformed shape raises ValueError. Pure: logs nothing.
"""
from __future__ import annotations

_SCALARS = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
}


def validate(obj, shape) -> list[str]:
    errors: list[str] = []
    _check(obj, shape, "$", errors)
    return errors


def _type_name(value) -> str:
    return type(value).__name__


def _check(obj, shape, path: str, errors: list[str]) -> None:
    if not isinstance(shape, dict) or "type" not in shape:
        raise ValueError(f"malformed shape at {path}: {shape!r}")
    kind = shape["type"]
    if kind in _SCALARS:
        if not _SCALARS[kind](obj):
            errors.append(f"{path}: expected {kind}, got {_type_name(obj)}")
    elif kind == "enum":
        values = shape.get("values")
        if not isinstance(values, (list, tuple)):
            raise ValueError(f"malformed enum shape at {path}: values must be a list")
        if obj not in values:
            errors.append(f"{path}: {obj!r} not one of {list(values)!r}")
    elif kind == "list":
        if "items" not in shape:
            raise ValueError(f"malformed list shape at {path}: missing items")
        if not isinstance(obj, list):
            errors.append(f"{path}: expected list, got {_type_name(obj)}")
            return
        for index, item in enumerate(obj):
            _check(item, shape["items"], f"{path}[{index}]", errors)
    elif kind == "object":
        if not isinstance(obj, dict):
            errors.append(f"{path}: expected object, got {_type_name(obj)}")
            return
        properties = shape.get("properties", {})
        for name in shape.get("required", ()):
            if name not in obj:
                errors.append(f"{path}.{name}: missing required field")
        for name, sub in properties.items():
            if name in obj:
                _check(obj[name], sub, f"{path}.{name}", errors)
    else:
        raise ValueError(f"malformed shape at {path}: unknown type {kind!r}")
