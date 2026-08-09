import unittest

from app.models import TaskFileError, parse_tasks


class ParseTasksTests(unittest.TestCase):
    def test_parses_task_list(self) -> None:
        tasks = parse_tasks([{"id": "one", "prompt": "Open a dashboard"}])
        self.assertEqual(tasks[0].id, "one")

    def test_parses_wrapped_tasks(self) -> None:
        tasks = parse_tasks({"tasks": [{"id": "one", "description": "Read"}]})
        self.assertEqual(tasks[0].prompt, "Read")

    def test_supplied_answer_becomes_an_exact_grader(self) -> None:
        tasks = parse_tasks([{"id": "one", "task": "Read", "answer": "{\"count\": 2}"}])
        self.assertEqual(tasks[0].expected_answer, {"count": 2})

    def test_rejects_implicit_grading(self) -> None:
        with self.assertRaises(TaskFileError):
            parse_tasks([{"id": "one", "prompt": "Read", "grader": {"type": "sql_exists", "sql": "select 1"}}])


if __name__ == "__main__":
    unittest.main()
