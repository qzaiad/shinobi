"""Shinobi's event filters on object boxes, re-implemented to predict what an event keeps.

Port of checkEventFilters() (libs/events/utils.js, pinned image) for the per-box fields; checked
against the live system (docs/notes/16-detection-quality.md §5, tests/test_shinobi_filters.py):

    for each enabled filter:
      for each condition (where row):
        for each box still present, position 0..n:
          box matches the condition?
            position > 0 and halt == "1"  -> box DELETED (array hole; later conditions skip it)
            otherwise                     -> condition = true
      expression over the conditions (&&, ||, brackets) true?
            halt "1" -> event dropped        (decided by box 0 alone, since 1..n were deleted)
      false and halt "2" -> event dropped
    holes removed; the event keeps the remaining boxes

Values as stored: Shinobi JSON-parses every string it can on save (cleanStringsInObject), and
only top-level details are turned back into strings at monitor start. So a filter's p3 "50" is
the NUMBER 50 at run time and `width === 50` matches; p3 "traffic light" stays a string.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

BOX_FIELDS = frozenset({"tag", "confidence", "x", "y", "width", "height"})


def stored(value: Any) -> Any:
    """A filter value as Shinobi holds it after a save: numeric strings become numbers."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, int | float) and not isinstance(parsed, bool):
            return parsed
    return value


def _matches(value: Any, op: str, ref: Any) -> bool:
    match op:
        case "indexOf":
            return str(ref) in str(value)
        case "!indexOf":
            return str(ref) not in str(value)
        case "===":  # JS strict equality (one number type: 50 === 50.0)
            return _strict_eq(value, ref)
        case "!==":
            return not _strict_eq(value, ref)
        case ">=":
            return float(value) >= float(ref)
        case ">":
            return float(value) > float(ref)
        case "<":
            return float(value) < float(ref)
        case "<=":
            return float(value) <= float(ref)
    return False


def _strict_eq(a: Any, b: Any) -> bool:
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num):
        return not isinstance(a, bool) and not isinstance(b, bool) and a == b
    return type(a) is type(b) and a == b


def _evaluate(tokens: list[str]) -> bool:
    """evaluateBooleanTokens: && binds tighter than ||, brackets; errors -> False."""
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def factor() -> bool:
        nonlocal pos
        if peek() == "(":
            pos += 1
            v = expr()
            if peek() == ")":
                pos += 1
            return v
        tok = peek()
        pos += 1
        return tok == "true"

    def term() -> bool:
        nonlocal pos
        v = factor()
        while peek() == "&&":
            pos += 1
            v = factor() and v
        return v

    def expr() -> bool:
        nonlocal pos
        v = term()
        while peek() == "||":
            pos += 1
            v = term() or v
        return v

    try:
        return expr()
    except IndexError:
        return False


def apply(
    filters: Mapping[str, Mapping[str, Any]], matrices: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]] | None:
    """Pure: boxes of one object event (plugin order) -> boxes Shinobi keeps, None = dropped.
    `filters` = monitor details.detector_filters (scripts/detection.shinobi_filters shape)."""
    boxes: list[dict[str, Any] | None] = [dict(m) for m in matrices]
    halt = False
    for flt in filters.values():
        if str(flt.get("enabled", "1")) == "0":
            continue
        halt_state = str(flt.get("actions", {}).get("halt") or "0")
        tokens: list[str] = []
        opened = closed = 0
        where = flt.get("where", [])
        for n, cond in enumerate(where):
            p1, p2, p3 = cond["p1"], cond["p2"], stored(cond["p3"])
            if p1 not in BOX_FIELDS:
                raise ValueError(f"filter field {p1!r} not emulated (only per-box fields)")
            ok = False
            for position, box in enumerate(boxes):
                if box is None or not _matches(box.get(p1), p2, p3):
                    continue
                if position and halt_state == "1":
                    boxes[position] = None
                else:
                    ok = True
            if str(cond.get("openBracket")) == "1":
                tokens.append("(")
                opened += 1
            tokens.append("true" if ok else "false")
            if str(cond.get("closeBracket")) == "1":
                tokens.append(")")
                closed += 1
            if n < len(where) - 1:
                tokens.append("||" if cond.get("p4") == "||" else "&&")
        if (opened + closed) % 2:
            tokens = [t for t in tokens if t not in "()"]  # odd brackets: Shinobi ignores them
        if _evaluate(tokens):
            if halt_state == "1":
                halt = True
        elif halt_state == "2":
            halt = True
    if halt or not boxes:
        return None
    return [b for b in boxes if b is not None]
