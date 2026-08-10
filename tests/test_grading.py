import unittest

from app.grading import grade_task, submitted_answer_text
from app.models import TaskSpec


class ExpectedAnswerGradingTests(unittest.TestCase):
    def test_accepts_exact_json_from_final_answer(self):
        task = TaskSpec(
            id="one",
            prompt="Return the count",
            expected_answer={"count": 2, "labels": ["a", "b"]},
        )

        grade = grade_task(task, '```json\n{"labels":["a","b"],"count":2}\n```')

        self.assertEqual(grade.status, "passed")
        self.assertEqual(grade.method, "exact_final_json")
        self.assertTrue(all(check["passed"] for check in grade.checks))

    def test_reports_matching_and_mismatching_fields(self):
        task = TaskSpec(
            id="one",
            prompt="Return the count",
            expected_answer={"count": 2, "labels": ["a", "b"]},
        )

        grade = grade_task(task, '{"count":2,"labels":["b","a"]}')

        checks = {check["name"]: check for check in grade.checks}
        self.assertEqual(grade.status, "failed")
        self.assertTrue(checks["Field 'count'"]["passed"])
        self.assertFalse(checks["Field 'labels'"]["passed"])
        self.assertEqual(grade.actual, {"count": 2, "labels": ["b", "a"]})

    def test_invalid_final_answer_fails_format_check(self):
        task = TaskSpec(
            id="one",
            prompt="Return the count",
            expected_answer={"count": 2},
        )

        grade = grade_task(task, "I could not complete the task.")

        self.assertEqual(grade.status, "failed")
        self.assertFalse(grade.checks[0]["passed"])
        self.assertIsNone(grade.actual)

    def test_only_last_json_value_is_treated_as_submitted_answer(self):
        task = TaskSpec(
            id="one",
            prompt="Return the count",
            expected_answer={"count": 2},
        )

        grade = grade_task(
            task,
            'I considered {"count": 2}, but my submitted answer is {"count": 3}.',
        )

        self.assertEqual(grade.status, "failed")
        self.assertEqual(grade.actual, {"count": 3})

    def test_submitted_answer_removes_surrounding_narrative(self):
        output = submitted_answer_text(
            'I considered {"count": 1}. Final answer: {"count": 2}'
        )

        self.assertEqual(output, '{\n  "count": 2\n}')

    def test_boolean_does_not_equal_number(self):
        task = TaskSpec(id="one", prompt="Return the count", expected_answer={"count": 1})

        grade = grade_task(task, '{"count": true}')

        self.assertEqual(grade.status, "failed")

    def test_duplicate_object_keys_are_not_accepted(self):
        task = TaskSpec(id="one", prompt="Return the count", expected_answer={"count": 2})

        grade = grade_task(task, '{"count": 1, "count": 2}')

        self.assertEqual(grade.status, "failed")
        self.assertFalse(grade.checks[0]["passed"])

    def test_invalid_outer_json_cannot_pass_via_a_valid_nested_object(self):
        task = TaskSpec(id="one", prompt="Return the count", expected_answer={"count": 2})

        grade = grade_task(task, '{"answer":{"count":2},"answer":{"count":3}}')

        self.assertEqual(grade.status, "failed")
        self.assertFalse(grade.checks[0]["passed"])

    def test_nonstandard_json_numbers_are_not_accepted(self):
        task = TaskSpec(id="one", prompt="Return the count", expected_answer={"count": 2})

        grade = grade_task(task, '{"count": NaN}')

        self.assertEqual(grade.status, "failed")
        self.assertFalse(grade.checks[0]["passed"])


if __name__ == "__main__":
    unittest.main()
