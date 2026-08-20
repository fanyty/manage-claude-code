import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "skills" / "manage-claude-code" / "scripts" / "claude_task.py"


class ClaudeTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.calls = self.base / "calls.jsonl"
        self.fake = self.base / "claude"
        self.fake.write_text(
            """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['FAKE_CALLS'], 'a', encoding='utf-8') as f:
    f.write(json.dumps(args) + '\\n')
if args == ['--version']:
    print('2.1.test')
elif args == ['auth', 'status']:
    print(json.dumps({'loggedIn': True, 'authMethod': 'test'}))
elif args[:2] == ['agents', '--json']:
    print(os.environ.get('FAKE_AGENTS', '[]'))
elif args and args[0] == 'logs':
    print('planning\\nimplemented feature\\ntests passed')
elif args and args[0] == 'stop':
    print('stopped')
elif '--background' in args:
    print('background session started')
else:
    print(json.dumps({'args': args}))
""",
            encoding="utf-8",
        )
        self.fake.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            {
                "CLAUDE_BIN": str(self.fake),
                "CLAUDE_MANAGER_STATE_DIR": str(self.base / "state"),
                "FAKE_CALLS": str(self.calls),
                "FAKE_AGENTS": "[]",
            }
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_manager(self, *args):
        result = subprocess.run(
            [sys.executable, str(MANAGER), *args],
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_full_lifecycle(self):
        doctor = self.run_manager("doctor")
        self.assertTrue(doctor["ready"])

        started = self.run_manager(
            "start",
            "--project",
            str(self.project),
            "--title",
            "Export customers",
            "--goal",
            "Add a customer export",
            "--done",
            "Tests pass and a preview is available",
            "--deployment-scope",
            "test",
        )
        task_id = started["task"]["id"]
        self.assertEqual(started["task"]["deployment_scope"], "test")

        listing = self.run_manager("list")
        self.assertEqual(len(listing["tasks"]), 1)

        status = self.run_manager("status", task_id[:8])
        self.assertIn("tests passed", status["logs"]["tail"])

        attachment = self.run_manager("attach", task_id[:8], "--print-only")
        self.assertIn("claude attach", attachment["command"])

        resumed = self.run_manager(
            "resume", task_id[:8], "--instruction", "Fix the final check"
        )
        self.assertEqual(resumed["task"]["status"], "resumed")

        stopped = self.run_manager("stop", task_id[:8])
        self.assertEqual(stopped["task"]["status"], "stopped")

        ledger = json.loads(
            (self.base / "state" / "tasks.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(ledger["tasks"]), 1)
        self.assertEqual(ledger["tasks"][0]["id"], task_id)


if __name__ == "__main__":
    unittest.main()
