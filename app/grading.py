from __future__ import annotations

import json

from .models import Grade, TaskSpec


class DuplicateKeyError(ValueError):
    pass


class InvalidJSONConstant(ValueError):
    pass


def _reject_json_constant(value: str) -> object:
    raise InvalidJSONConstant(value)


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateKeyError(key)
        value[key] = item
    return value


STRICT_DECODER = json.JSONDecoder(
    object_pairs_hook=_object_without_duplicate_keys,
    parse_constant=_reject_json_constant,
)


def _json_equal(left: object, right: object) -> bool:
    """Compare JSON values without Python's bool/int equality ambiguity."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def grade_task(task: TaskSpec, final_answer: str) -> Grade:
    if task.expected_answer is None:
        return Grade(
            "needs_review",
            0.0,
            "No golden answer was supplied for this task.",
            method="manual_review",
            actual=final_answer or None,
            checks=[{
                "name": "Golden answer configured",
                "passed": False,
                "detail": "Add an answer value to the task JSON for deterministic grading.",
            }],
        )
    return _grade_expected_answer(task.expected_answer, final_answer)


def _json_candidates(final_answer: str) -> list[object]:
    stripped = final_answer.strip()
    if stripped:
        try:
            return [STRICT_DECODER.decode(stripped)]
        except (json.JSONDecodeError, DuplicateKeyError, InvalidJSONConstant):
            pass

    candidates: list[object] = []
    index = 0
    while index < len(final_answer):
        if final_answer[index] not in "[{":
            index += 1
            continue
        try:
            value, end = STRICT_DECODER.raw_decode(final_answer[index:])
        except (json.JSONDecodeError, DuplicateKeyError, InvalidJSONConstant):
            # Skip the complete balanced structure when it is invalid. Without
            # this, a valid nested object inside duplicate-key/NaN JSON could be
            # mistaken for the submitted answer.
            end = _balanced_json_end(final_answer, index)
            index = end if end is not None else index + 1
            continue
        if isinstance(value, (dict, list)):
            candidates.append(value)
        index += end

    unique: list[object] = []
    for candidate in candidates:
        if not any(_json_equal(candidate, existing) for existing in unique):
            unique.append(candidate)
    return unique


def _balanced_json_end(text: str, start: int) -> int | None:
    """Return the end of a bracketed JSON-like span without parsing its values."""
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"}": "{", "]": "["}
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            stack.append(character)
        elif character in "}]":
            if not stack or stack.pop() != pairs[character]:
                return None
            if not stack:
                return index + 1
    return None


def submitted_answer_text(final_answer: str) -> str:
    """Return the submitted JSON value without any surrounding model narrative."""
    candidates = _json_candidates(final_answer)
    if not candidates:
        return final_answer.strip()
    return json.dumps(candidates[-1], ensure_ascii=False, indent=2)


def _preview(value: object) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return rendered if len(rendered) <= 180 else rendered[:177] + "..."


def _field_checks(expected: object, actual: object | None) -> list[dict[str, object]]:
    """Explain which top-level answer sections matched without weakening grading."""
    if not isinstance(expected, dict):
        return []
    if not isinstance(actual, dict):
        return [{
            "name": "Expected answer fields are present",
            "passed": False,
            "detail": "The observed JSON is not an object, so its fields cannot be compared.",
        }]

    checks: list[dict[str, object]] = []
    for key, expected_value in expected.items():
        if key not in actual:
            checks.append({
                "name": f"Field {key!r}",
                "passed": False,
                "detail": "This required field is missing from the observed answer.",
            })
            continue
        actual_value = actual[key]
        matches = _json_equal(actual_value, expected_value)
        checks.append({
            "name": f"Field {key!r}",
            "passed": matches,
            "detail": (
                "Observed value exactly matches the expected value."
                if matches
                else f"Expected {_preview(expected_value)}; observed {_preview(actual_value)}."
            ),
        })
    for key in actual.keys() - expected.keys():
        checks.append({
            "name": f"Unexpected field {key!r}",
            "passed": False,
            "detail": "This field is not part of the golden answer.",
        })
    return checks


def _grade_expected_answer(expected: object, final_answer: str) -> Grade:
    candidates = _json_candidates(final_answer)
    actual = candidates[-1] if candidates else None
    passed = actual is not None and _json_equal(actual, expected)
    format_ok = bool(candidates)
    evidence = (
        "The last JSON value in the final answer exactly matched the golden answer."
        if passed
        else "The last JSON value in the final answer did not exactly match the golden answer."
        if format_ok
        else "Final answer did not contain valid JSON to compare with the golden answer."
    )
    return Grade(
        "passed" if passed else "failed",
        float(passed),
        evidence,
        method="exact_final_json",
        expected=expected,
        actual=actual,
        checks=[
            {
                "name": "Final answer is valid JSON",
                "passed": format_ok,
                "detail": f"Found {len(candidates)} JSON candidate(s); compared the last one as the submitted answer.",
            },
            *_field_checks(expected, actual),
            {
                "name": "Exact structure and values",
                "passed": passed,
                "detail": "Object key order is ignored; list order, values, spelling, and membership must match.",
            },
        ],
    )
