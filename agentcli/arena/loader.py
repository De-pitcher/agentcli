"""Task loader and built-in benchmark task repository for AgentCLI Arena."""

from __future__ import annotations

import json
from pathlib import Path

from agentcli.arena.task import BenchmarkTask, TaskCategory


def get_builtin_core_tasks() -> list[BenchmarkTask]:
    """Get built-in core benchmark tasks."""
    return [
        BenchmarkTask(
            id="humaneval_001_has_close_elements",
            title="HumanEval 001: Has Close Elements",
            category=TaskCategory.CODE_GEN,
            description="Check if in given list of numbers, are any two numbers closer to each other than given threshold.",
            prompt=(
                "Create a file named `solution.py` containing a function `has_close_elements(numbers: list[float], threshold: float) -> bool` "
                "that checks if any two distinct elements in the list have an absolute difference strictly less than the threshold."
            ),
            workspace_setup={},
            expected_files={"solution.py": r"def has_close_elements"},
            test_command="python -m unittest test_solution.py",
            test_files={
                "test_solution.py": (
                    "import unittest\n"
                    "from solution import has_close_elements\n\n"
                    "class TestSolution(unittest.TestCase):\n"
                    "    def test_basic(self):\n"
                    "        self.assertTrue(has_close_elements([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.3))\n"
                    "        self.assertFalse(has_close_elements([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.05))\n"
                    "        self.assertTrue(has_close_elements([1.0, 2.0, 5.9, 4.0, 5.0], 0.95))\n"
                    "        self.assertFalse(has_close_elements([1.0, 2.0, 5.9, 4.0, 5.0], 0.8))\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
            },
            timeout_seconds=60,
            max_iterations=6,
            tags=["core", "quick", "code_gen", "offline"],
        ),
        BenchmarkTask(
            id="swe_bugfix_off_by_one",
            title="SWE Bugfix: Window Average Off-by-One",
            category=TaskCategory.BUG_FIX,
            description="Fix an off-by-one boundary defect in moving window calculation.",
            prompt=(
                "The file `window.py` contains a bug in `moving_average(data, window_size)` where the last window is omitted. "
                "Inspect the file, fix the bug so all valid windows are computed, and save the fixed `window.py`."
            ),
            workspace_setup={
                "window.py": (
                    "def moving_average(data: list[float], window_size: int) -> list[float]:\n"
                    "    if not data or window_size <= 0 or len(data) < window_size:\n"
                    "        return []\n"
                    "    result = []\n"
                    "    # BUG: len(data) - window_size misses the final complete window\n"
                    "    for i in range(len(data) - window_size):\n"
                    "        window = data[i : i + window_size]\n"
                    "        result.append(sum(window) / float(window_size))\n"
                    "    return result\n"
                )
            },
            expected_files={"window.py": r"def moving_average"},
            test_command="python -m unittest test_window.py",
            test_files={
                "test_window.py": (
                    "import unittest\n"
                    "from window import moving_average\n\n"
                    "class TestWindow(unittest.TestCase):\n"
                    "    def test_window(self):\n"
                    "        res = moving_average([1.0, 2.0, 3.0, 4.0, 5.0], 3)\n"
                    "        self.assertEqual(len(res), 3)\n"
                    "        self.assertEqual(res, [2.0, 3.0, 4.0])\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
            },
            timeout_seconds=60,
            max_iterations=6,
            tags=["core", "quick", "bug_fix", "offline"],
        ),
        BenchmarkTask(
            id="tool_use_count_json_records",
            title="Tool Use: Aggregate JSON Records",
            category=TaskCategory.TOOL_USE,
            description="Inspect a data file and generate summary metrics into a target file.",
            prompt=(
                "Read the records in `data/events.json`. Calculate the total count of events and the sum of `duration_ms`. "
                "Write the result to `data/summary.json` as JSON object with keys `total_count` (int) and `total_duration_ms` (int)."
            ),
            workspace_setup={
                "data/events.json": (
                    json.dumps([
                        {"id": "evt_1", "name": "click", "duration_ms": 120},
                        {"id": "evt_2", "name": "scroll", "duration_ms": 45},
                        {"id": "evt_3", "name": "submit", "duration_ms": 300},
                        {"id": "evt_4", "name": "navigate", "duration_ms": 85},
                    ], indent=2)
                )
            },
            expected_files={"data/summary.json": r"total_count"},
            test_command="python -m unittest test_summary.py",
            test_files={
                "test_summary.py": (
                    "import json, os, unittest\n\n"
                    "class TestSummary(unittest.TestCase):\n"
                    "    def test_summary(self):\n"
                    "        self.assertTrue(os.path.exists('data/summary.json'))\n"
                    "        with open('data/summary.json', 'r', encoding='utf-8') as f:\n"
                    "            data = json.load(f)\n"
                    "        self.assertEqual(data.get('total_count'), 4)\n"
                    "        self.assertEqual(data.get('total_duration_ms'), 550)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
            },
            timeout_seconds=60,
            max_iterations=6,
            tags=["core", "quick", "tool_use", "offline"],
        ),
        BenchmarkTask(
            id="refactor_extract_clean_calculator",
            title="Refactor: Extract Modular Calculator Class",
            category=TaskCategory.REFACTOR,
            description="Refactor monolithic script into an object-oriented Calculator class.",
            prompt=(
                "Refactor the procedural functions in `legacy_calc.py` into a clean class `Calculator` inside `calculator.py`. "
                "The class should support methods `add(a, b)`, `subtract(a, b)`, `multiply(a, b)`, and `divide(a, b)` (raising ValueError on divide by zero)."
            ),
            workspace_setup={
                "legacy_calc.py": (
                    "def do_calc(op, a, b):\n"
                    "    if op == '+': return a + b\n"
                    "    elif op == '-': return a - b\n"
                    "    elif op == '*': return a * b\n"
                    "    elif op == '/':\n"
                    "        if b == 0: raise ValueError('zero')\n"
                    "        return a / b\n"
                )
            },
            expected_files={"calculator.py": r"class Calculator"},
            test_command="python -m unittest test_calc.py",
            test_files={
                "test_calc.py": (
                    "import unittest\n"
                    "from calculator import Calculator\n\n"
                    "class TestCalculator(unittest.TestCase):\n"
                    "    def test_operations(self):\n"
                    "        c = Calculator()\n"
                    "        self.assertEqual(c.add(10, 5), 15)\n"
                    "        self.assertEqual(c.subtract(10, 5), 5)\n"
                    "        self.assertEqual(c.multiply(10, 5), 50)\n"
                    "        self.assertEqual(c.divide(10, 5), 2.0)\n"
                    "        with self.assertRaises(ValueError):\n"
                    "            c.divide(10, 0)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
            },
            timeout_seconds=60,
            max_iterations=6,
            tags=["core", "refactor", "offline"],
        ),
    ]


class TaskLoader:
    """Loader and manager for benchmark task suites."""

    def __init__(self, custom_suites_dir: Path | None = None) -> None:
        self.custom_suites_dir = custom_suites_dir

    def get_suites(self) -> dict[str, list[BenchmarkTask]]:
        """Return all registered suites mapped by suite name."""
        suites: dict[str, list[BenchmarkTask]] = {
            "core": get_builtin_core_tasks(),
        }
        if self.custom_suites_dir and self.custom_suites_dir.exists():
            for file_path in self.custom_suites_dir.glob("*.json"):
                suite_name = file_path.stem
                try:
                    tasks = self.load_from_json(file_path)
                    suites[suite_name] = tasks
                except Exception:  # noqa: S112, BLE001
                    continue
        return suites

    def load_from_json(self, file_path: Path) -> list[BenchmarkTask]:
        """Load benchmark tasks from a JSON file."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [BenchmarkTask.from_dict(item) for item in data]
        elif isinstance(data, dict) and "tasks" in data:
            return [BenchmarkTask.from_dict(item) for item in data["tasks"]]
        return []

    def filter_tasks(
        self,
        tasks: list[BenchmarkTask],
        category: TaskCategory | None = None,
        tag: str | None = None,
        task_id: str | None = None,
    ) -> list[BenchmarkTask]:
        """Filter a list of tasks based on criteria."""
        filtered = tasks
        if category:
            filtered = [t for t in filtered if t.category == category]
        if tag:
            filtered = [t for t in filtered if tag.lower() in [x.lower() for x in t.tags]]
        if task_id:
            filtered = [t for t in filtered if t.id == task_id or task_id.lower() in t.id.lower()]
        return filtered
