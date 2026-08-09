from __future__ import annotations

import json

from .models import Grade, TaskSpec


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
    candidates: list[object] = []
    stripped = final_answer.strip()
    if stripped:
        try:
            whole_value = json.loads(stripped)
            if not isinstance(whole_value, (dict, list)):
                candidates.append(whole_value)
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    located: list[tuple[int, int, object]] = []
    for index, character in enumerate(final_answer):
        if character not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(final_answer[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            located.append((index, index + end, value))
    # Later values are treated as more final. For equal end positions, prefer
    # the outermost structure instead of an object nested inside it.
    candidates.extend(value for _, _, value in sorted(located, key=lambda item: (item[1], -item[0])))
    unique: list[object] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


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
        matches = actual_value == expected_value
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
    passed = actual == expected
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
