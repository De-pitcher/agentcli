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


def get_builtin_field_trials_tasks() -> list[BenchmarkTask]:
    """Get built-in production field trial benchmark tasks (Phase 30)."""
    return [
        BenchmarkTask(
            id="field_bugfix_auth_jwt",
            title="Field Trial: Multi-File Auth Token Header Repair",
            category=TaskCategory.BUG_FIX,
            description="Repair Authorization Bearer token extraction and whitespace handling across modules.",
            prompt=(
                "Fix `src/auth/jwt.py` so `extract_bearer_token(header: str) -> str | None` correctly handles "
                "case-insensitive 'Bearer ' prefixes, strips whitespace, and returns None for empty or invalid headers. "
                "Update `src/auth/handler.py` to authenticate requests properly using the extracted token."
            ),
            workspace_setup={
                "src/auth/jwt.py": (
                    "def extract_bearer_token(header: str | None) -> str | None:\n"
                    "    if not header:\n"
                    "        return None\n"
                    "    # BUG: only checks exact lowercase 'bearer '\n"
                    "    if header.startswith('bearer '):\n"
                    "        return header[7:]\n"
                    "    return None\n"
                ),
                "src/auth/handler.py": (
                    "from src.auth.jwt import extract_bearer_token\n\n"
                    "def authenticate_request(headers: dict[str, str]) -> dict[str, str]:\n"
                    "    auth = headers.get('Authorization') or headers.get('authorization')\n"
                    "    token = extract_bearer_token(auth)\n"
                    "    if not token:\n"
                    "        return {'status': 'unauthorized'}\n"
                    "    return {'status': 'authorized', 'token': token}\n"
                ),
            },
            expected_files={"src/auth/jwt.py": r"def extract_bearer_token"},
            test_command="python -m unittest tests/test_auth.py",
            test_files={
                "tests/test_auth.py": (
                    "import unittest\n"
                    "from src.auth.jwt import extract_bearer_token\n"
                    "from src.auth.handler import authenticate_request\n\n"
                    "class TestAuth(unittest.TestCase):\n"
                    "    def test_bearer_case_and_whitespace(self):\n"
                    "        self.assertEqual(extract_bearer_token('Bearer abc-123 '), 'abc-123')\n"
                    "        self.assertEqual(extract_bearer_token('bearer  xyz-789'), 'xyz-789')\n"
                    "        self.assertIsNone(extract_bearer_token('Basic dXNlcg=='))\n"
                    "        self.assertIsNone(extract_bearer_token(''))\n\n"
                    "    def test_authenticate_request(self):\n"
                    "        res = authenticate_request({'Authorization': 'Bearer secret-key'})\n"
                    "        self.assertEqual(res['status'], 'authorized')\n"
                    "        self.assertEqual(res['token'], 'secret-key')\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
            },
            timeout_seconds=90,
            max_iterations=6,
            tags=["field_trials", "bug_fix", "multi_file", "offline"],
        ),
        BenchmarkTask(
            id="field_refactor_lru_cache",
            title="Field Trial: LRU Cache Eviction & Capacity Limit",
            category=TaskCategory.REFACTOR,
            description="Implement an LRU cache data structure in src/cache/store.py with get, put, and eviction.",
            prompt=(
                "Create `src/cache/store.py` with a class `LRUCache(capacity: int)` supporting `get(key: str) -> Any` "
                "(returning None if missing) and `put(key: str, value: Any) -> None` which evicts the least recently used "
                "item when capacity is exceeded. Also include a `size() -> int` method."
            ),
            workspace_setup={},
            expected_files={"src/cache/store.py": r"class LRUCache"},
            test_command="python -m unittest tests/test_cache.py",
            test_files={
                "tests/test_cache.py": (
                    "import unittest\n"
                    "from src.cache.store import LRUCache\n\n"
                    "class TestLRUCache(unittest.TestCase):\n"
                    "    def test_lru_eviction(self):\n"
                    "        cache = LRUCache(capacity=2)\n"
                    "        cache.put('a', 1)\n"
                    "        cache.put('b', 2)\n"
                    "        self.assertEqual(cache.get('a'), 1)  # 'a' becomes most recently used\n"
                    "        cache.put('c', 3)  # should evict 'b'\n"
                    "        self.assertIsNone(cache.get('b'))\n"
                    "        self.assertEqual(cache.get('a'), 1)\n"
                    "        self.assertEqual(cache.get('c'), 3)\n"
                    "        self.assertEqual(cache.size(), 2)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
            },
            timeout_seconds=90,
            max_iterations=6,
            tags=["field_trials", "refactor", "offline"],
        ),
        BenchmarkTask(
            id="field_mesh_cross_dependency",
            title="Field Trial: Multi-Repo Cross-Workspace Export",
            category=TaskCategory.MESH_ORCHESTRATION,
            description="Implement slug formatting in packages/common/utils.py and consume it in packages/app/main.py.",
            prompt=(
                "Create `packages/common/utils.py` containing `slugify(text: str) -> str` which converts a string to lowercase, "
                "replaces spaces with hyphens, and removes non-alphanumeric characters (except hyphens). "
                "Then create `packages/app/main.py` with `build_endpoint(category: str, title: str) -> str` using `slugify`."
            ),
            workspace_setup={},
            expected_files={
                "packages/common/utils.py": r"def slugify",
                "packages/app/main.py": r"def build_endpoint",
            },
            test_command="python -m unittest tests/test_mesh_app.py",
            test_files={
                "tests/test_mesh_app.py": (
                    "import unittest\n"
                    "from packages.common.utils import slugify\n"
                    "from packages.app.main import build_endpoint\n\n"
                    "class TestMeshApp(unittest.TestCase):\n"
                    "    def test_slugify(self):\n"
                    "        self.assertEqual(slugify('Hello World! 2026'), 'hello-world-2026')\n"
                    "        self.assertEqual(slugify('Agent-CLI Rocks!'), 'agent-cli-rocks')\n\n"
                    "    def test_build_endpoint(self):\n"
                    "        url = build_endpoint('Blog Posts', 'My First AI Agent!')\n"
                    "        self.assertEqual(url, '/blog-posts/my-first-ai-agent')\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
            },
            timeout_seconds=90,
            max_iterations=6,
            tags=["field_trials", "mesh", "multi_file", "offline"],
        ),
        BenchmarkTask(
            id="field_tool_log_metrics",
            title="Field Trial: Log Metrics Extraction & Error Tally",
            category=TaskCategory.TOOL_USE,
            description="Inspect server log file and generate structured JSON error metrics summary.",
            prompt=(
                "Read `logs/server.log` and calculate total error occurrences and warning occurrences. "
                "Write the result to `logs/summary.json` containing `{\"error_count\": <int>, \"warning_count\": <int>}`."
            ),
            workspace_setup={
                "logs/server.log": (
                    "2026-09-06 00:01:00 [INFO] Server started on port 8080\n"
                    "2026-09-06 00:01:15 [WARNING] High memory pressure detected: 78%\n"
                    "2026-09-06 00:02:10 [ERROR] Connection timeout to database replica 2\n"
                    "2026-09-06 00:03:00 [WARNING] Disk I/O throttling in progress\n"
                    "2026-09-06 00:04:22 [ERROR] Failed to flush buffer to disk: WinError 32\n"
                    "2026-09-06 00:05:00 [INFO] Routine health check OK\n"
                )
            },
            expected_files={"logs/summary.json": r"error_count"},
            test_command="python -m unittest tests/test_log_summary.py",
            test_files={
                "tests/test_log_summary.py": (
                    "import json, os, unittest\n\n"
                    "class TestLogSummary(unittest.TestCase):\n"
                    "    def test_log_summary(self):\n"
                    "        self.assertTrue(os.path.exists('logs/summary.json'))\n"
                    "        with open('logs/summary.json', 'r', encoding='utf-8') as f:\n"
                    "            data = json.load(f)\n"
                    "        self.assertEqual(data.get('error_count'), 2)\n"
                    "        self.assertEqual(data.get('warning_count'), 2)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
            },
            timeout_seconds=90,
            max_iterations=6,
            tags=["field_trials", "tool_use", "offline"],
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
            "field_trials": get_builtin_field_trials_tasks(),
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
