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
        self.osascript_calls = self.base / "osascript-calls.jsonl"
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
    print('planning\\x1b[31m\\nimplemented feature\\ntests passed\\x1b[0m')
elif args and args[0] == 'stop':
    print('stopped')
elif '--background' in args:
    print('backgrounded · abc12345 · Export customers')
elif '-p' in args:
    print('READY')
else:
    print(json.dumps({'args': args}))
""",
            encoding="utf-8",
        )
        self.fake.chmod(0o755)
        self.fake_osascript = self.base / "osascript"
        self.fake_osascript.write_text(
            """#!/usr/bin/env python3
import json, os, sys
with open(os.environ['FAKE_OSASCRIPT_CALLS'], 'a', encoding='utf-8') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
print('window 1 of application Terminal')
""",
            encoding="utf-8",
        )
        self.fake_osascript.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            {
                "CLAUDE_BIN": str(self.fake),
                "CLAUDE_MANAGER_STATE_DIR": str(self.base / "state"),
                "FAKE_CALLS": str(self.calls),
                "FAKE_OSASCRIPT_CALLS": str(self.osascript_calls),
                "CLAUDE_MANAGER_OSASCRIPT_BIN": str(self.fake_osascript),
                "FAKE_AGENTS": json.dumps(
                    [
                        {
                            "id": "abc12345",
                            "sessionId": "abc12345-1111-2222-3333-444444444444",
                            "cwd": str(self.project),
                            "name": "Export customers",
                            "state": "stopped",
                        }
                    ]
                ),
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

        probe = self.run_manager("doctor", "--probe")
        self.assertTrue(probe["live_probe"]["ok"])

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
            "--open-window",
        )
        task_id = started["task"]["id"]
        self.assertEqual(started["task"]["agent_id"], "abc12345")
        self.assertEqual(
            started["task"]["session_id"],
            "abc12345-1111-2222-3333-444444444444",
        )
        self.assertEqual(started["task"]["deployment_scope"], "test")
        self.assertTrue(started["window"]["opened"])
        osascript_calls = self.osascript_calls.read_text(encoding="utf-8")
        self.assertIn("attach abc12345", osascript_calls)

        listing = self.run_manager("list")
        self.assertEqual(len(listing["tasks"]), 1)

        status = self.run_manager("status", task_id[:8])
        self.assertTrue(status["logs"]["available"])
        self.assertIn("tests passed", status["logs"]["tail"])
        self.assertNotIn("\x1b", status["logs"]["tail"])

        attachment = self.run_manager("attach", task_id[:8], "--print-only")
        self.assertIn("attach abc12345", attachment["command"])

        opened = self.run_manager("open-window", task_id[:8])
        self.assertTrue(opened["window"]["opened"])

        resumed = self.run_manager(
            "resume",
            task_id[:8],
            "--instruction",
            "Fix the final check",
            "--open-window",
        )
        self.assertEqual(resumed["task"]["status"], "resumed")
        self.assertTrue(resumed["window"]["opened"])

        stopped = self.run_manager("stop", task_id[:8])
        self.assertEqual(stopped["task"]["status"], "stopped")

        ledger = json.loads(
            (self.base / "state" / "tasks.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(ledger["tasks"]), 1)
        self.assertEqual(ledger["tasks"][0]["id"], task_id)

    def test_migrates_legacy_task_ids(self):
        state_dir = self.base / "state"
        state_dir.mkdir()
        ledger = {
            "version": 1,
            "tasks": [
                {
                    "id": "manager-old",
                    "session_id": "requested-old-session",
                    "title": "Export customers",
                    "project": str(self.project),
                    "status": "started",
                    "launch_output": "backgrounded · abc12345 · Export customers",
                }
            ],
        }
        (state_dir / "tasks.json").write_text(json.dumps(ledger), encoding="utf-8")

        listing = self.run_manager("list")
        task = listing["tasks"][0]
        self.assertEqual(task["agent_id"], "abc12345")
        self.assertEqual(
            task["session_id"], "abc12345-1111-2222-3333-444444444444"
        )
        self.assertEqual(task["requested_session_id"], "requested-old-session")


if __name__ == "__main__":
    unittest.main()
