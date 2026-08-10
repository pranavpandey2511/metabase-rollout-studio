import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.models import TaskFileError, load_tasks, parse_tasks


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

    def test_rejects_task_ids_that_are_not_safe_artifact_names(self) -> None:
        with self.assertRaisesRegex(TaskFileError, "letters, numbers"):
            parse_tasks([{"id": "one/two", "prompt": "Read"}])

    def test_rejects_duplicate_keys_in_task_files(self) -> None:
        with TemporaryDirectory() as directory:
            task_file = Path(directory) / "tasks.json"
            task_file.write_text(
                '[{"id":"one","prompt":"Read","answer":{"count":1,"count":2}}]'
            )

            with self.assertRaisesRegex(TaskFileError, "duplicate JSON key"):
                load_tasks(task_file)

    def test_rejects_explicit_null_golden_answers(self) -> None:
        with self.assertRaisesRegex(TaskFileError, "answer cannot be null"):
            parse_tasks([{"id": "one", "prompt": "Read", "answer": None}])

    def test_rejects_nonstandard_json_numbers(self) -> None:
        with TemporaryDirectory() as directory:
            task_file = Path(directory) / "tasks.json"
            task_file.write_text(
                '[{"id":"one","prompt":"Read","answer":{"count":NaN}}]'
            )

            with self.assertRaisesRegex(TaskFileError, "invalid JSON number"):
                load_tasks(task_file)

    def test_rejects_task_files_that_are_not_utf8(self) -> None:
        with TemporaryDirectory() as directory:
            task_file = Path(directory) / "tasks.json"
            task_file.write_bytes(b"\xff\xfe")

            with self.assertRaisesRegex(TaskFileError, "UTF-8"):
                load_tasks(task_file)


if __name__ == "__main__":
    unittest.main()
