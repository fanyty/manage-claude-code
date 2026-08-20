#!/usr/bin/env python3
"""Manage local Claude Code background sessions for a Codex skill."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATE_DIR = Path(os.environ.get("CLAUDE_MANAGER_STATE_DIR", str(Path.home() / ".codex" / "manage-claude-code"))).expanduser()
STATE_FILE = STATE_DIR / "tasks.json"
PLATFORM = os.environ.get("CLAUDE_MANAGER_PLATFORM", sys.platform)
CLAUDE_SETTINGS_FILE = Path(
    os.environ.get("CLAUDE_MANAGER_SETTINGS_FILE", str(Path.home() / ".claude" / "settings.json"))
).expanduser()
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
OSASCRIPT_BIN = os.environ.get("CLAUDE_MANAGER_OSASCRIPT_BIN")
TERMINAL_PROFILE = os.environ.get("CLAUDE_MANAGER_TERMINAL_PROFILE", "Pro")
WINDOWS_TERMINAL_BIN = os.environ.get("CLAUDE_MANAGER_WINDOWS_TERMINAL_BIN", "wt.exe")
POWERSHELL_BIN = os.environ.get("CLAUDE_MANAGER_POWERSHELL_BIN", "powershell.exe")
CC_SWITCH_DB_OVERRIDE = os.environ.get("CLAUDE_MANAGER_CC_SWITCH_DB")
PERMISSION_MODES = ("auto", "manual", "acceptEdits", "plan", "dontAsk")
DEPLOYMENT_SCOPES = ("none", "test", "production")
TERMINAL_STATES = {"completed", "failed", "stopped", "exited", "done"}
CREDENTIAL_OVERRIDE_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
BACKGROUND_ID_RE = re.compile(r"backgrounded\s*[·•]\s*([0-9a-zA-Z-]{8,})")
OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SECRET_RE = re.compile(r"(?i)(sk-ant-[A-Za-z0-9_-]{8})[A-Za-z0-9_-]+")


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


def run_claude(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(CLAUDE_BIN)
    if not executable:
        raise ManagerError(f"Claude Code executable not found: {CLAUDE_BIN}")
    command_env = os.environ.copy()
    if env_overrides:
        command_env.update(env_overrides)
    try:
        result = subprocess.run(
            [executable, *args],
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=command_env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        result = subprocess.CompletedProcess(
            [executable, *args],
            124,
            stdout,
            stderr or f"Timed out after {timeout:g} seconds",
        )
    if check and result.returncode != 0:
        detail = sanitize_text(result.stderr or result.stdout or "unknown error").strip()
        raise ManagerError(f"Claude Code command failed ({result.returncode}): {detail}")
    return result


def sanitize_text(text: str) -> str:
    text = OSC_RE.sub("", text)
    text = CSI_RE.sub("", text)
    text = text.replace("\r", "\n")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    text = SECRET_RE.sub(r"\1…REDACTED", text)
    lines = [line.rstrip() for line in text.splitlines()]
    compact: list[str] = []
    for line in lines:
        if line or (compact and compact[-1]):
            compact.append(line)
    return "\n".join(compact).strip()


def parse_background_id(output: str) -> str:
    match = BACKGROUND_ID_RE.search(sanitize_text(output))
    return match.group(1) if match else ""


def manager_command(*args: str) -> str:
    command = [sys.executable, str(Path(__file__).resolve()), *args]
    return subprocess.list2cmdline(command) if PLATFORM == "win32" else shlex.join(command)


def open_macos_terminal_window(task: dict[str, Any]) -> dict[str, Any]:
    osascript = shutil.which(OSASCRIPT_BIN or "osascript")
    executable = shutil.which(CLAUDE_BIN)
    if not osascript:
        raise ManagerError("macOS osascript executable not found")
    if not executable:
        raise ManagerError(f"Claude Code executable not found: {CLAUDE_BIN}")
    attach_command = shlex.join([executable, "attach", task_agent_id(task)])
    saved_window_id = task.get("terminal_window_id")
    if not isinstance(saved_window_id, int):
        saved_window_id = 0
    script = "\n".join(
        [
            'tell application "Terminal"',
            "activate",
            f"set savedWindowId to {saved_window_id}",
            "set targetWindow to missing value",
            'set actionName to "created"',
            "if savedWindowId is not 0 then",
            "repeat with terminalWindow in windows",
            "if (id of terminalWindow) is savedWindowId then",
            "set targetWindow to terminalWindow",
            "exit repeat",
            "end if",
            "end repeat",
            "end if",
            "if targetWindow is missing value then",
            f"do script {json.dumps(attach_command)}",
            "set targetWindow to front window",
            "else",
            "set index of targetWindow to 1",
            "if busy of selected tab of targetWindow then",
            'set actionName to "focused"',
            "else",
            f"do script {json.dumps(attach_command)} in selected tab of targetWindow",
            'set actionName to "reused"',
            "end if",
            "end if",
            f"set current settings of selected tab of targetWindow to settings set {json.dumps(TERMINAL_PROFILE)}",
            "set index of targetWindow to 1",
            'return (id of targetWindow as text) & "|" & actionName',
            "end tell",
        ]
    )
    try:
        result = subprocess.run(
            [osascript, "-e", script],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise ManagerError(
            "Timed out waiting for macOS to open Terminal. "
            "Allow the automation request in System Settings > Privacy & Security > Automation, then retry."
        ) from exc
    if result.returncode != 0:
        detail = sanitize_text(result.stderr or result.stdout or "unknown error")
        raise ManagerError(f"Unable to open Terminal window: {detail}")
    output = sanitize_text(result.stdout)
    window_id_text, separator, action = output.partition("|")
    if not separator or not window_id_text.isdigit():
        raise ManagerError(f"Terminal opened but its window ID could not be recorded: {output or 'no output'}")
    window_id = int(window_id_text)
    task["terminal_window_id"] = window_id
    return {
        "opened": True,
        "application": "Terminal",
        "window_id": window_id,
        "action": action,
        "reused": action in {"reused", "focused"},
        "attach_command": attach_command,
        "output": output,
    }


def windows_runtime_paths(task: dict[str, Any]) -> tuple[Path, Path]:
    runtime_dir = STATE_DIR / "windows"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    task_id = str(task["id"])
    return runtime_dir / f"{task_id}.control.json", runtime_dir / f"{task_id}.status.json"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.stem}-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def process_is_running(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError):
        return False
    return True


def read_bridge_pid(status_path: Path, expected_token: str) -> int | None:
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("bridge_token") != expected_token:
        return None
    pid = payload.get("pid")
    return pid if isinstance(pid, int) and pid > 0 else None


def focus_windows_terminal(window_name: str, pid: int, wt: str | None, powershell: str) -> bool:
    if wt:
        result = subprocess.run(
            [wt, "-w", window_name, "focus-tab", "--target", "0"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        return result.returncode == 0
    focus_script = """
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class CodexWindow {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
}
'@
$p = Get-Process -Id $args[0] -ErrorAction Stop
if ($p.MainWindowHandle -eq 0) { exit 2 }
[CodexWindow]::ShowWindowAsync($p.MainWindowHandle, 9) | Out-Null
if (-not [CodexWindow]::SetForegroundWindow($p.MainWindowHandle)) { exit 3 }
""".strip()
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-Command", focus_script, str(pid)],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return result.returncode == 0


def open_windows_terminal_window(task: dict[str, Any]) -> dict[str, Any]:
    executable = shutil.which(CLAUDE_BIN)
    powershell = shutil.which(POWERSHELL_BIN) or shutil.which("pwsh.exe")
    wt = shutil.which(WINDOWS_TERMINAL_BIN)
    if not executable:
        raise ManagerError(f"Claude Code executable not found: {CLAUDE_BIN}")
    if not powershell:
        raise ManagerError("PowerShell executable not found; install Windows PowerShell or PowerShell 7")
    bridge = Path(__file__).with_name("windows_terminal_bridge.ps1")
    if not bridge.is_file():
        raise ManagerError(f"Windows terminal bridge is missing: {bridge}")

    control_path, status_path = windows_runtime_paths(task)
    window_name = str(task.get("terminal_window_name") or f"codex-claude-{str(task['id'])[:8]}")
    bridge_token = str(task.get("terminal_bridge_token") or uuid.uuid4())
    task["terminal_window_name"] = window_name
    task["terminal_bridge_token"] = bridge_token
    write_json_atomic(
        control_path,
        {
            "task_id": task["id"],
            "title": task.get("title", "Claude Code task"),
            "agent_id": task_agent_id(task),
            "claude_executable": executable,
            "bridge_token": bridge_token,
            "updated_at": now(),
        },
    )

    pid = read_bridge_pid(status_path, bridge_token)
    if process_is_running(pid):
        focused = focus_windows_terminal(window_name, pid, wt, powershell)
        task["terminal_process_id"] = pid
        task["terminal_backend"] = "windows-terminal" if wt else "powershell"
        return {
            "opened": True,
            "application": "Windows Terminal" if wt else "PowerShell",
            "window_name": window_name,
            "process_id": pid,
            "action": "focused" if focused else "already-running",
            "reused": True,
            "control_file": str(control_path),
        }

    status_path.unlink(missing_ok=True)
    bridge_args = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-File",
        str(bridge),
        "-ControlPath",
        str(control_path),
        "-StatusPath",
        str(status_path),
        "-BridgeToken",
        bridge_token,
    ]
    if wt:
        launch_command = [
            wt,
            "-w",
            window_name,
            "new-tab",
            "--title",
            str(task.get("title") or "Claude Code"),
            *bridge_args,
        ]
        subprocess.Popen(launch_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        backend = "windows-terminal"
    else:
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen(
            bridge_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        backend = "powershell"

    deadline = time.monotonic() + 8
    pid = None
    while time.monotonic() < deadline:
        pid = read_bridge_pid(status_path, bridge_token)
        if process_is_running(pid):
            break
        time.sleep(0.1)
    if not process_is_running(pid):
        raise ManagerError(
            "Windows terminal process did not become ready. Check PowerShell execution policy and Windows Terminal."
        )
    task["terminal_process_id"] = pid
    task["terminal_backend"] = backend
    return {
        "opened": True,
        "application": "Windows Terminal" if wt else "PowerShell",
        "window_name": window_name,
        "process_id": pid,
        "action": "created",
        "reused": False,
        "control_file": str(control_path),
    }


def open_terminal_window(task: dict[str, Any]) -> dict[str, Any]:
    if PLATFORM == "darwin":
        return open_macos_terminal_window(task)
    if PLATFORM == "win32":
        return open_windows_terminal_window(task)
    raise ManagerError("Visible terminal windows are supported on macOS and Windows")


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
    return str(agent.get("id") or agent.get("agentId") or "")


def agent_session_id(agent: dict[str, Any]) -> str:
    return str(
        agent.get("sessionId")
        or agent.get("session_id")
        or agent.get("conversationId")
        or ""
    )


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


def match_agent(agents: list[dict[str, Any]], task: dict[str, Any]) -> dict[str, Any] | None:
    primary_identifiers = {str(task["agent_id"])} if task.get("agent_id") else set()
    parsed = parse_background_id(str(task.get("launch_output", "")))
    if parsed:
        primary_identifiers.add(parsed)
    secondary_identifiers = {
        str(task[key])
        for key in ("session_id", "requested_session_id")
        if task.get(key)
    }
    identifier_groups = [primary_identifiers]
    if not primary_identifiers:
        identifier_groups.append(secondary_identifiers)
    for identifiers in identifier_groups:
        for agent in agents:
            native_ids = {value for value in (agent_id(agent), agent_session_id(agent)) if value}
            for expected in identifiers:
                if any(
                    native == expected
                    or native.startswith(expected)
                    or expected.startswith(native)
                    for native in native_ids
                ):
                    return agent
        if identifiers:
            return None
    candidates = [
        agent
        for agent in agents
        if agent.get("name") == task.get("title")
        and str(Path(str(agent.get("cwd", ""))).expanduser()) == task.get("project")
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def hydrate_task(task: dict[str, Any], agent: dict[str, Any] | None = None) -> bool:
    changed = False
    parsed = parse_background_id(str(task.get("launch_output", "")))
    if parsed and task.get("agent_id") != parsed:
        task["agent_id"] = parsed
        changed = True
    if agent:
        native_agent_id = agent_id(agent)
        native_session_id = agent_session_id(agent)
        if native_agent_id and task.get("agent_id") != native_agent_id:
            task["agent_id"] = native_agent_id
            changed = True
        if native_session_id and task.get("session_id") != native_session_id:
            if task.get("session_id") and not task.get("requested_session_id"):
                task["requested_session_id"] = task["session_id"]
            task["session_id"] = native_session_id
            changed = True
    return changed


def task_agent_id(task: dict[str, Any]) -> str:
    value = str(task.get("agent_id") or parse_background_id(str(task.get("launch_output", ""))))
    if not value:
        raise ManagerError(
            f"No Claude background ID recorded for task {task.get('id')}; run list to refresh it"
        )
    return value


def configured_overrides() -> dict[str, list[str]]:
    process = [name for name in CREDENTIAL_OVERRIDE_VARS if os.environ.get(name)]
    settings: list[str] = []
    if CLAUDE_SETTINGS_FILE.exists():
        try:
            payload = json.loads(CLAUDE_SETTINGS_FILE.read_text(encoding="utf-8"))
            configured_env = payload.get("env", {}) if isinstance(payload, dict) else {}
            if isinstance(configured_env, dict):
                settings = [
                    name for name in CREDENTIAL_OVERRIDE_VARS if configured_env.get(name)
                ]
        except (OSError, json.JSONDecodeError):
            pass
    return {"process": process, "user_settings": settings}


def claude_user_provider() -> dict[str, str | None]:
    if not CLAUDE_SETTINGS_FILE.exists():
        return {"model": None, "base_url": None}
    try:
        payload = json.loads(CLAUDE_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"model": None, "base_url": None}
    configured_env = payload.get("env", {}) if isinstance(payload, dict) else {}
    if not isinstance(configured_env, dict):
        return {"model": None, "base_url": None}
    return {
        "model": configured_env.get("ANTHROPIC_MODEL"),
        "base_url": configured_env.get("ANTHROPIC_BASE_URL"),
    }


def cc_switch_database() -> Path:
    if CC_SWITCH_DB_OVERRIDE:
        return Path(CC_SWITCH_DB_OVERRIDE).expanduser()
    candidates = [Path.home() / ".cc-switch" / "cc-switch.db"]
    if PLATFORM == "win32":
        for variable in ("APPDATA", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if not root:
                continue
            candidates.extend(
                [
                    Path(root) / "CCSwitch" / "cc-switch.db",
                    Path(root) / "cc-switch" / "cc-switch.db",
                    Path(root) / "CC Switch" / "cc-switch.db",
                ]
            )
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def cc_switch_provider() -> dict[str, Any] | None:
    database = cc_switch_database()
    if not database.exists():
        return None
    try:
        database_uri = database.expanduser().resolve().as_uri()
        connection = sqlite3.connect(f"{database_uri}?mode=ro", uri=True)
        row = connection.execute(
            "SELECT id, name, settings_config FROM providers "
            "WHERE app_type='claude' AND is_current=1 LIMIT 1"
        ).fetchone()
        connection.close()
    except sqlite3.Error as exc:
        raise ManagerError(f"Cannot read CC Switch provider database: {exc}") from exc
    if not row:
        return None
    provider_id, name, raw_config = row
    try:
        config = json.loads(raw_config)
    except json.JSONDecodeError as exc:
        raise ManagerError(f"CC Switch provider {name} has invalid configuration JSON") from exc
    configured_env = config.get("env", {}) if isinstance(config, dict) else {}
    env = {
        str(key): value
        for key, value in configured_env.items()
        if isinstance(key, str) and isinstance(value, str)
    } if isinstance(configured_env, dict) else {}
    provider = {
        "source": "cc-switch",
        "id": str(provider_id),
        "name": str(name),
        "model": env.get("ANTHROPIC_MODEL"),
        "base_url": env.get("ANTHROPIC_BASE_URL"),
        "env": env,
    }
    applied = claude_user_provider()
    provider["settings_sync"] = (
        applied["model"] == provider["model"]
        and applied["base_url"] == provider["base_url"]
    )
    return provider


def resolve_provider(source: str) -> dict[str, Any]:
    if source in {"auto", "cc-switch"}:
        provider = cc_switch_provider()
        if provider:
            return provider
        if source == "cc-switch":
            raise ManagerError("CC Switch has no current Claude provider")
    return {
        "source": "claude",
        "id": None,
        "name": "Claude user settings",
        "model": None,
        "base_url": None,
        "env": {},
        "settings_sync": True,
    }


def public_provider(provider: dict[str, Any]) -> dict[str, Any]:
    return {
        key: provider.get(key)
        for key in ("source", "id", "name", "model", "base_url", "settings_sync")
    }


def provider_cli_args(provider: dict[str, Any]) -> list[str]:
    if provider["source"] == "cc-switch":
        return ["--setting-sources", "project,local"]
    return []


def prepare_background_daemon(
    provider: dict[str, Any],
    data: dict[str, Any],
    agents: list[dict[str, Any]],
) -> None:
    if provider["source"] != "cc-switch":
        return
    active_agents = [
        agent for agent in agents if agent_status(agent).lower() not in TERMINAL_STATES
    ]
    recorded = data.get("daemon_provider", {})
    if active_agents:
        if recorded.get("id") != provider.get("id"):
            raise ManagerError(
                "Claude's background daemon is already serving active sessions from an unknown or different provider. "
                "Stop those sessions before switching to the current CC Switch provider."
            )
        return
    run_claude(["daemon", "stop", "--any"], check=False)


def platform_readiness() -> dict[str, Any]:
    python_supported = sys.version_info >= (3, 9)
    payload: dict[str, Any] = {
        "name": PLATFORM,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "python_supported": python_supported,
        "supported": PLATFORM in {"darwin", "win32"},
    }
    if PLATFORM == "darwin":
        osascript = shutil.which(OSASCRIPT_BIN or "osascript")
        payload.update(
            {
                "visible_terminal": "Terminal",
                "visible_terminal_ready": bool(osascript),
                "terminal_executable": osascript,
                "manual_action": None if osascript else "Install or restore macOS Terminal automation support.",
            }
        )
    elif PLATFORM == "win32":
        powershell = shutil.which(POWERSHELL_BIN) or shutil.which("pwsh.exe")
        wt = shutil.which(WINDOWS_TERMINAL_BIN)
        bridge = Path(__file__).with_name("windows_terminal_bridge.ps1")
        payload.update(
            {
                "visible_terminal": "Windows Terminal" if wt else "PowerShell",
                "visible_terminal_ready": bool(powershell and bridge.is_file()),
                "powershell_executable": powershell,
                "windows_terminal_executable": wt,
                "windows_terminal_optional": True,
                "bridge_available": bridge.is_file(),
                "manual_action": (
                    None
                    if powershell and bridge.is_file()
                    else "Install PowerShell and reinstall the complete manage-claude-code skill."
                ),
            }
        )
    else:
        payload.update(
            {
                "visible_terminal": None,
                "visible_terminal_ready": False,
                "manual_action": "Use this skill on macOS or Windows.",
            }
        )
    payload["ready"] = bool(
        payload["supported"] and python_supported and payload["visible_terminal_ready"]
    )
    return payload


def command_doctor(args: argparse.Namespace) -> None:
    executable = shutil.which(CLAUDE_BIN)
    if not executable:
        emit({"ready": False, "error": f"Claude Code executable not found: {CLAUDE_BIN}"}, 1)
    version = run_claude(["--version"]).stdout.strip()
    auth = run_claude(["auth", "status"], check=False)
    agents_check = run_claude(["agents", "--json"], check=False)
    try:
        auth_payload: Any = json.loads(auth.stdout) if auth.stdout.strip() else {}
    except json.JSONDecodeError:
        auth_payload = {"raw": sanitize_text(auth.stdout or auth.stderr)}
    override_sources = configured_overrides()
    overrides = sorted(set(override_sources["process"] + override_sources["user_settings"]))
    probe_payload: dict[str, Any] | None = None
    platform = platform_readiness()
    ready = auth.returncode == 0 and agents_check.returncode == 0 and platform["ready"]
    provider = resolve_provider(args.provider_source)
    if args.probe:
        probe_command = provider_cli_args(provider) + ["-p", "--permission-mode", "plan"]
        if provider["source"] == "claude":
            probe_command.extend(["--max-budget-usd", "0.02"])
        probe_command.append("Reply exactly READY. Do not use tools.")
        probe = run_claude(
            probe_command,
            check=False,
            timeout=60,
            env_overrides=provider["env"],
        )
        probe_payload = {
            "ok": probe.returncode == 0,
            "returncode": probe.returncode,
            "detail": sanitize_text(probe.stdout or probe.stderr)[-1000:],
        }
        ready = ready and probe.returncode == 0
    warning = None
    if provider["source"] == "cc-switch" and not provider["settings_sync"]:
        warning = (
            "CC Switch's current provider is not applied to Claude user settings. "
            "Re-enable that provider in CC Switch before starting a background task."
        )
        ready = False
    elif PLATFORM == "win32" and not platform.get("windows_terminal_executable"):
        warning = (
            "Windows Terminal was not found. The skill can use a PowerShell window, "
            "but installing Windows Terminal provides better focus and window reuse."
        )
    elif overrides and not args.probe:
        warning = (
            "Environment credentials may override the reported login. "
            "Run doctor --probe to verify a real request."
        )
    emit(
        {
            "ready": ready,
            "executable": executable,
            "version": version,
            "authenticated": auth.returncode == 0,
            "background_sessions_available": agents_check.returncode == 0,
            "auth": auth_payload,
            "credential_override_variables": overrides,
            "credential_override_sources": override_sources,
            "live_probe": probe_payload,
            "warning": warning,
            "provider": public_provider(provider),
            "platform": platform,
            "cc_switch_database": str(cc_switch_database()),
            "state_file": str(STATE_FILE),
        },
        0 if ready else 1,
    )


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
    provider = resolve_provider(args.provider_source)
    for task in data["tasks"]:
        if task.get("project") != str(project):
            continue
        active_agent = match_agent(agents, task)
        if active_agent and agent_status(active_agent).lower() not in TERMINAL_STATES:
            raise ManagerError(f"Project already has an active managed task: {task['id']} ({agent_status(active_agent)})")
    manager_id = str(uuid.uuid4())
    if provider["source"] == "cc-switch" and not provider["settings_sync"]:
        raise ManagerError(
            "CC Switch's current Claude provider does not match ~/.claude/settings.json. "
            "Re-enable the intended provider in CC Switch, then run doctor --probe again."
        )
    prepare_background_daemon(provider, data, agents)
    command = provider_cli_args(provider) + ["--background", "--name", args.title, "--permission-mode", args.permission_mode]
    if args.open_window:
        command.extend(["--settings", json.dumps({"theme": "dark"})])
    if args.model:
        command.extend(["--model", args.model])
    command.append(build_prompt(args))
    result = run_claude(command, cwd=project, env_overrides=provider["env"])
    background_id = parse_background_id(result.stdout or result.stderr)
    if not background_id:
        raise ManagerError(
            "Claude Code started without returning a recognizable background ID: "
            + sanitize_text(result.stdout or result.stderr)[-1000:]
        )
    task = {
        "id": manager_id,
        "agent_id": background_id,
        "session_id": None,
        "title": args.title,
        "project": str(project),
        "goal": args.goal.strip(),
        "done": args.done.strip(),
        "deployment_scope": args.deployment_scope,
        "permission_mode": args.permission_mode,
        "model": args.model,
        "provider": public_provider(provider),
        "status": "started",
        "created_at": now(),
        "updated_at": now(),
        "launch_output": sanitize_text(result.stdout),
        "history": [],
    }
    current = match_agent(get_agents(include_completed=True), task)
    hydrate_task(task, current)
    data["tasks"].append(task)
    data["daemon_provider"] = public_provider(provider)
    save_state(data)
    window: dict[str, Any] | None = None
    if args.open_window:
        try:
            window = open_terminal_window(task)
            save_state(data)
        except ManagerError as exc:
            window = {"opened": False, "error": str(exc)}
    emit(
        {
            "task": task,
            "native_agent": current,
            "window": window,
            "visibility": (
                "Claude Code is running in the background and attached in its reusable visible terminal window."
                if window and window.get("opened")
                else "Claude Code is running in the background; use open-window or attach to enter its live terminal session."
            ),
            "commands": {
                "status": manager_command("status", manager_id),
                "logs": manager_command("logs", manager_id),
                "attach": manager_command("attach", manager_id),
                "open_window": manager_command("open-window", manager_id),
            },
        }
    )


def command_list(_: argparse.Namespace) -> None:
    data = load_state()
    agents = get_agents(include_completed=True)
    tasks = []
    changed = False
    for task in data["tasks"]:
        current = match_agent(agents, task)
        changed = hydrate_task(task, current) or changed
        item = dict(task)
        item["native_agent"] = current
        item["observed_status"] = agent_status(current) if current else task.get("status", "unknown")
        tasks.append(item)
    if changed:
        save_state(data)
    unmanaged = [agent for agent in agents if not any(match_agent([agent], task) for task in data["tasks"])]
    emit({"tasks": tasks, "unmanaged_agents": unmanaged})


def read_logs(task: dict[str, Any], lines: int) -> dict[str, Any]:
    result = run_claude(["logs", task_agent_id(task)], check=False)
    text = sanitize_text(result.stdout or result.stderr or "")
    available = result.returncode == 0
    return {
        "available": available,
        "cached": False,
        "returncode": result.returncode,
        "tail": "\n".join(text.splitlines()[-lines:]) if available else "",
        "reason": None if available else text,
    }


def retain_logs(task: dict[str, Any], logs: dict[str, Any], lines: int) -> bool:
    if logs["available"]:
        tail = str(logs.get("tail", ""))
        if tail:
            task["last_log_tail"] = tail
            task["last_log_at"] = now()
            return True
        return False
    cached = str(task.get("last_log_tail", ""))
    if cached:
        logs["tail"] = "\n".join(cached.splitlines()[-lines:])
        logs["cached"] = True
    return False


def command_status(args: argparse.Namespace) -> None:
    data = load_state()
    task = find_task(data, args.task_id)
    current = match_agent(get_agents(include_completed=True), task)
    changed = hydrate_task(task, current)
    logs = read_logs(task, args.lines)
    changed = retain_logs(task, logs, args.lines) or changed
    if current:
        task["status"] = agent_status(current)
        task["updated_at"] = now()
        changed = True
    if changed:
        save_state(data)
    emit({"task": task, "native_agent": current, "logs": logs})


def command_logs(args: argparse.Namespace) -> None:
    data = load_state()
    task = find_task(data, args.task_id)
    logs = read_logs(task, args.lines)
    if retain_logs(task, logs, args.lines):
        save_state(data)
    emit({"task_id": task["id"], "logs": logs})


def command_resume(args: argparse.Namespace) -> None:
    data = load_state()
    task = find_task(data, args.task_id)
    active = match_agent(get_agents(include_completed=False), task)
    if active and agent_status(active).lower() not in TERMINAL_STATES:
        raise ManagerError(f"Task is still active ({agent_status(active)}). Attach to it or wait before resuming.")
    session_id = str(task.get("session_id") or "")
    if not session_id:
        raise ManagerError(f"No resumable Claude session ID recorded for task {task['id']}")
    saved_provider = task.get("provider", {})
    provider_source = str(saved_provider.get("source") or "auto")
    provider = resolve_provider(provider_source)
    command = provider_cli_args(provider)
    if args.open_window:
        command.extend(["--settings", json.dumps({"theme": "dark"})])
    command.extend(["--background", "--resume", session_id, "--permission-mode", args.permission_mode or task.get("permission_mode", "auto"), args.instruction])
    result = run_claude(command, cwd=Path(task["project"]), env_overrides=provider["env"])
    task.setdefault("history", []).append({"at": now(), "action": "resume", "instruction": args.instruction})
    task["status"] = "resumed"
    task["updated_at"] = now()
    task["launch_output"] = sanitize_text(result.stdout)
    new_background_id = parse_background_id(result.stdout or result.stderr)
    if not new_background_id:
        raise ManagerError("Claude Code resumed without returning a background ID")
    task["agent_id"] = new_background_id
    hydrate_task(task, match_agent(get_agents(include_completed=True), task))
    save_state(data)
    window: dict[str, Any] | None = None
    if args.open_window:
        try:
            window = open_terminal_window(task)
            save_state(data)
        except ManagerError as exc:
            window = {"opened": False, "error": str(exc)}
    emit(
        {
            "task": task,
            "window": window,
            "visibility": (
                "Claude Code resumed in the background and attached in its reusable visible terminal window."
                if window and window.get("opened")
                else "Claude Code resumed in the background; use open-window or attach to enter its live terminal session."
            ),
            "commands": {
                "status": manager_command("status", task["id"]),
                "logs": manager_command("logs", task["id"]),
                "attach": manager_command("attach", task["id"]),
                "open_window": manager_command("open-window", task["id"]),
            },
        }
    )


def command_stop(args: argparse.Namespace) -> None:
    data = load_state()
    task = find_task(data, args.task_id)
    result = run_claude(["stop", task_agent_id(task)], check=False)
    if result.returncode != 0:
        detail = sanitize_text(result.stderr or result.stdout or "unknown error")
        raise ManagerError(f"Unable to stop task: {detail}")
    task["status"] = "stopped"
    task["updated_at"] = now()
    task.setdefault("history", []).append({"at": now(), "action": "stop"})
    save_state(data)
    emit({"task": task, "output": sanitize_text(result.stdout)})


def command_attach(args: argparse.Namespace) -> None:
    task = find_task(load_state(), args.task_id)
    executable = shutil.which(CLAUDE_BIN)
    if not executable:
        raise ManagerError(f"Claude Code executable not found: {CLAUDE_BIN}")
    command = [executable, "attach", task_agent_id(task)]
    if args.print_only or not (sys.stdin.isatty() and sys.stdout.isatty()):
        printable = subprocess.list2cmdline(command) if PLATFORM == "win32" else shlex.join(command)
        emit({"task_id": task["id"], "interactive_required": True, "command": printable})
    if PLATFORM == "win32":
        raise SystemExit(subprocess.call(command))
    os.execv(executable, command)


def command_open_window(args: argparse.Namespace) -> None:
    data = load_state()
    task = find_task(data, args.task_id)
    window = open_terminal_window(task)
    save_state(data)
    emit({"task_id": task["id"], "window": window})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="Check Claude Code readiness")
    doctor.add_argument(
        "--probe",
        action="store_true",
        help="Make a minimal live model request to verify effective credentials",
    )
    doctor.add_argument(
        "--provider-source",
        choices=("auto", "claude", "cc-switch"),
        default="auto",
        help="Choose Claude's user settings or the current CC Switch provider",
    )
    doctor.set_defaults(func=command_doctor)
    start = subparsers.add_parser("start", help="Start a managed background task")
    start.add_argument("--project", required=True)
    start.add_argument("--title", required=True)
    start.add_argument("--goal", required=True)
    start.add_argument("--done", required=True)
    start.add_argument("--deployment-scope", choices=DEPLOYMENT_SCOPES, default="none")
    start.add_argument("--permission-mode", choices=PERMISSION_MODES, default="auto")
    start.add_argument("--model")
    start.add_argument(
        "--provider-source",
        choices=("auto", "claude", "cc-switch"),
        default="auto",
        help="Choose Claude's user settings or the current CC Switch provider",
    )
    start.add_argument(
        "--open-window",
        action="store_true",
        help="Open or reuse the task's macOS or Windows terminal window and attach to Claude Code",
    )
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
    resume.add_argument(
        "--open-window",
        action="store_true",
        help="Reuse the task's visible terminal window and attach to the resumed Claude Code task",
    )
    resume.set_defaults(func=command_resume)
    stop = subparsers.add_parser("stop", help="Stop a background task")
    stop.add_argument("task_id")
    stop.set_defaults(func=command_stop)
    attach = subparsers.add_parser("attach", help="Attach to a task interactively")
    attach.add_argument("task_id")
    attach.add_argument("--print-only", action="store_true")
    attach.set_defaults(func=command_attach)
    open_window = subparsers.add_parser(
        "open-window",
        help="Open or focus the managed task's macOS or Windows terminal window",
    )
    open_window.add_argument("task_id")
    open_window.set_defaults(func=command_open_window)
    return parser


def main() -> None:
    try:
        args = build_parser().parse_args()
        args.func(args)
    except ManagerError as exc:
        emit({"error": str(exc)}, 1)


if __name__ == "__main__":
    main()
