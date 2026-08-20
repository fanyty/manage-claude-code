#!/usr/bin/env python3
"""Manage local Claude Code background sessions for a Codex skill."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATE_DIR = Path(os.environ.get("CLAUDE_MANAGER_STATE_DIR", str(Path.home() / ".codex" / "manage-claude-code"))).expanduser()
STATE_FILE = STATE_DIR / "tasks.json"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
PERMISSION_MODES = ("auto", "manual", "acceptEdits", "plan", "dontAsk")
DEPLOYMENT_SCOPES = ("none", "test", "production")


class ManagerError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(payload: Any, exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"version": 1, "tasks": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagerError(f"Cannot read task ledger: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ManagerError(f"Invalid task ledger: {STATE_FILE}")
    return data


def save_state(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="tasks-", suffix=".json", dir=STATE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, STATE_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def run_claude(args: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(CLAUDE_BIN)
    if not executable:
        raise ManagerError(f"Claude Code executable not found: {CLAUDE_BIN}")
    result = subprocess.run([executable, *args], cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise ManagerError(f"Claude Code command failed ({result.returncode}): {detail}")
    return result


def find_task(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    exact = [task for task in data["tasks"] if task.get("id") == task_id]
    if exact:
        return exact[0]
    prefix = [task for task in data["tasks"] if str(task.get("id", "")).startswith(task_id)]
    if len(prefix) == 1:
        return prefix[0]
    if len(prefix) > 1:
        raise ManagerError(f"Task ID prefix is ambiguous: {task_id}")
    raise ManagerError(f"Unknown task ID: {task_id}")


def agent_id(agent: dict[str, Any]) -> str:
    for key in ("id", "sessionId", "session_id", "agentId", "conversationId"):
        value = agent.get(key)
        if value:
            return str(value)
    return ""


def agent_status(agent: dict[str, Any] | None) -> str:
    if not agent:
        return "unknown"
    for key in ("status", "state", "phase"):
        value = agent.get(key)
        if value:
            return str(value)
    return "unknown"


def get_agents(include_completed: bool = True) -> list[dict[str, Any]]:
    args = ["agents", "--json"]
    if include_completed:
        args.append("--all")
    result = run_claude(args)
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ManagerError(f"Claude Code returned invalid agents JSON: {exc}") from exc
    return payload if isinstance(payload, list) else []


def match_agent(agents: list[dict[str, Any]], session_id: str) -> dict[str, Any] | None:
    if not session_id:
        return None
    for agent in agents:
        current_id = agent_id(agent)
        if current_id and (current_id == session_id or current_id.startswith(session_id) or session_id.startswith(current_id)):
            return agent
    return None


def command_doctor(_: argparse.Namespace) -> None:
    executable = shutil.which(CLAUDE_BIN)
    if not executable:
        emit({"ready": False, "error": f"Claude Code executable not found: {CLAUDE_BIN}"}, 1)
    version = run_claude(["--version"]).stdout.strip()
    auth = run_claude(["auth", "status"], check=False)
    try:
        auth_payload: Any = json.loads(auth.stdout) if auth.stdout.strip() else {}
    except json.JSONDecodeError:
        auth_payload = {"raw": (auth.stdout or auth.stderr).strip()}
    emit({"ready": auth.returncode == 0, "executable": executable, "version": version, "authenticated": auth.returncode == 0, "auth": auth_payload, "state_file": str(STATE_FILE)}, 0 if auth.returncode == 0 else 1)


def build_prompt(args: argparse.Namespace) -> str:
    deployment = {
        "none": "Do not deploy or publish anything.",
        "test": "Deployment is authorized only to a test or preview environment.",
        "production": "Production deployment is authorized within the stated task scope.",
    }[args.deployment_scope]
    return f"""You are the coding executor for a task managed from Codex.

BUSINESS OUTCOME
{args.goal.strip()}

DEFINITION OF DONE
{args.done.strip()}

DEPLOYMENT AUTHORIZATION
{deployment}

OPERATING RULES
- Work only in the current project and explicitly allowed directories.
- Inspect the existing project before changing it.
- Plan, implement, and run proportionate verification.
- Preserve unrelated user changes.
- Do not claim completion without evidence from checks or observable output.
- If blocked by a decision, credential, external approval, or unsafe operation, stop and state BLOCKED followed by the exact decision needed.
- Finish with a concise summary of changes, verification evidence, remaining risks, and any deployment result.
"""


def command_start(args: argparse.Namespace) -> None:
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise ManagerError(f"Project directory does not exist: {project}")
    data = load_state()
    agents = get_agents(include_completed=False)
    for task in data["tasks"]:
        if task.get("project") != str(project):
            continue
        active_agent = match_agent(agents, str(task.get("session_id", "")))
        if active_agent and agent_status(active_agent).lower() not in {"completed", "failed", "stopped", "exited"}:
            raise ManagerError(f"Project already has an active managed task: {task['id']} ({agent_status(active_agent)})")
    session_id = str(uuid.uuid4())
    command = ["--background", "--session-id", session_id, "--name", args.title, "--permission-mode", args.permission_mode]
    if args.model:
        command.extend(["--model", args.model])
    command.append(build_prompt(args))
    result = run_claude(command, cwd=project)
    task = {
        "id": session_id,
        "session_id": session_id,
        "title": args.title,
        "project": str(project),
        "goal": args.goal.strip(),
        "done": args.done.strip(),
        "deployment_scope": args.deployment_scope,
        "permission_mode": args.permission_mode,
        "model": args.model,
        "status": "started",
        "created_at": now(),
        "updated_at": now(),
        "launch_output": result.stdout.strip(),
        "history": [],
    }
    data["tasks"].append(task)
    save_state(data)
    emit({"task": task, "next": f"status {session_id}"})


def command_list(_: argparse.Namespace) -> None:
    data = load_state()
    agents = get_agents(include_completed=True)
    tasks = []
    for task in data["tasks"]:
        item = dict(task)
        current = match_agent(agents, str(task.get("session_id", "")))
        item["native_agent"] = current
        item["observed_status"] = agent_status(current) if current else task.get("status", "unknown")
        tasks.append(item)
    unmanaged = [agent for agent in agents if not any(match_agent([agent], str(task.get("session_id", ""))) for task in data["tasks"])]
    emit({"tasks": tasks, "unmanaged_agents": unmanaged})


def read_logs(task: dict[str, Any], lines: int) -> dict[str, Any]:
    result = run_claude(["logs", task["session_id"]], check=False)
    text = (result.stdout or result.stderr or "").rstrip()
    return {"returncode": result.returncode, "tail": "\n".join(text.splitlines()[-lines:])}


def command_status(args: argparse.Namespace) -> None:
    data = load_state()
    task = find_task(data, args.task_id)
    current = match_agent(get_agents(include_completed=True), task["session_id"])
    logs = read_logs(task, args.lines)
    if current:
        task["status"] = agent_status(current)
        task["updated_at"] = now()
        save_state(data)
    emit({"task": task, "native_agent": current, "logs": logs})


def command_logs(args: argparse.Namespace) -> None:
    task = find_task(load_state(), args.task_id)
    emit({"task_id": task["id"], "logs": read_logs(task, args.lines)})


def command_resume(args: argparse.Namespace) -> None:
    data = load_state()
    task = find_task(data, args.task_id)
    active = match_agent(get_agents(include_completed=False), task["session_id"])
    if active and agent_status(active).lower() not in {"completed", "failed", "stopped", "exited"}:
        raise ManagerError(f"Task is still active ({agent_status(active)}). Attach to it or wait before resuming.")
    command = ["--background", "--resume", task["session_id"], "--permission-mode", args.permission_mode or task.get("permission_mode", "auto"), args.instruction]
    result = run_claude(command, cwd=Path(task["project"]))
    task.setdefault("history", []).append({"at": now(), "action": "resume", "instruction": args.instruction})
    task["status"] = "resumed"
    task["updated_at"] = now()
    task["launch_output"] = result.stdout.strip()
    save_state(data)
    emit({"task": task})


def command_stop(args: argparse.Namespace) -> None:
    data = load_state()
    task = find_task(data, args.task_id)
    result = run_claude(["stop", task["session_id"]], check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise ManagerError(f"Unable to stop task: {detail}")
    task["status"] = "stopped"
    task["updated_at"] = now()
    task.setdefault("history", []).append({"at": now(), "action": "stop"})
    save_state(data)
    emit({"task": task, "output": result.stdout.strip()})


def command_attach(args: argparse.Namespace) -> None:
    task = find_task(load_state(), args.task_id)
    executable = shutil.which(CLAUDE_BIN)
    if not executable:
        raise ManagerError(f"Claude Code executable not found: {CLAUDE_BIN}")
    command = [executable, "attach", task["session_id"]]
    if args.print_only or not (sys.stdin.isatty() and sys.stdout.isatty()):
        emit({"task_id": task["id"], "interactive_required": True, "command": shlex.join(command)})
    os.execv(executable, command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="Check Claude Code readiness")
    doctor.set_defaults(func=command_doctor)
    start = subparsers.add_parser("start", help="Start a managed background task")
    start.add_argument("--project", required=True)
    start.add_argument("--title", required=True)
    start.add_argument("--goal", required=True)
    start.add_argument("--done", required=True)
    start.add_argument("--deployment-scope", choices=DEPLOYMENT_SCOPES, default="none")
    start.add_argument("--permission-mode", choices=PERMISSION_MODES, default="auto")
    start.add_argument("--model")
    start.set_defaults(func=command_start)
    listing = subparsers.add_parser("list", help="List managed tasks")
    listing.set_defaults(func=command_list)
    status = subparsers.add_parser("status", help="Show task state and recent logs")
    status.add_argument("task_id")
    status.add_argument("--lines", type=int, default=80)
    status.set_defaults(func=command_status)
    logs = subparsers.add_parser("logs", help="Show recent task logs")
    logs.add_argument("task_id")
    logs.add_argument("--lines", type=int, default=120)
    logs.set_defaults(func=command_logs)
    resume = subparsers.add_parser("resume", help="Resume a stopped or completed task")
    resume.add_argument("task_id")
    resume.add_argument("--instruction", required=True)
    resume.add_argument("--permission-mode", choices=PERMISSION_MODES)
    resume.set_defaults(func=command_resume)
    stop = subparsers.add_parser("stop", help="Stop a background task")
    stop.add_argument("task_id")
    stop.set_defaults(func=command_stop)
    attach = subparsers.add_parser("attach", help="Attach to a task interactively")
    attach.add_argument("task_id")
    attach.add_argument("--print-only", action="store_true")
    attach.set_defaults(func=command_attach)
    return parser


def main() -> None:
    try:
        args = build_parser().parse_args()
        args.func(args)
    except ManagerError as exc:
        emit({"error": str(exc)}, 1)


if __name__ == "__main__":
    main()
