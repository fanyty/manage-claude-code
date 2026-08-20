---
name: manage-claude-code
description: Delegate software development tasks from Codex to a locally installed Claude Code, track background sessions, read progress logs, let the user attach for hands-on control, resume follow-up work, verify results, and report status in business language. Use when the user explicitly wants Codex to manage or monitor Claude Code; do not use for ordinary Codex coding tasks.
---

# Manage Claude Code

Act as the manager and verifier while local Claude Code acts as the coding executor. Keep the user in Codex for delegation and reporting, while supporting hands-on access to Claude Code when requested.

Use `scripts/claude_task.py` for deterministic session management. It stores a local task ledger under `~/.codex/manage-claude-code/` and uses Claude Code's native background-session commands.

## Check readiness

Run both checks before the first managed task on a machine:

```bash
python3 <skill-dir>/scripts/claude_task.py doctor
python3 <skill-dir>/scripts/claude_task.py doctor --probe
```

The live probe is mandatory because `claude auth status` can report a saved login even when a headless background worker cannot refresh it. It makes one small model request and may incur normal provider usage. Run it again after credentials or providers change. Stop and explain the missing prerequisite if Claude Code is absent, unauthenticated, or the probe fails. Do not install, update, change credentials, or log in without a separate user request.

## Define the delegation

Before starting Claude Code, establish:

- the exact project directory;
- the desired business outcome;
- observable completion criteria;
- whether deployment is excluded, limited to a test environment, or explicitly authorized for production;
- any irreversible or externally visible operations requiring separate approval.

Resolve facts from the project yourself. Ask only for decisions that materially change scope, risk, cost, or external impact.

## Start a background task

Use one active writing task per project unless the tasks have isolated worktrees. Never use Claude Code's permission-bypass mode.

```bash
python3 <skill-dir>/scripts/claude_task.py start \
  --project /absolute/path/to/project \
  --title "Short task name" \
  --goal "Business outcome and required work" \
  --done "Observable checks proving completion" \
  --deployment-scope none \
  --open-window
```

Set `--deployment-scope test` or `production` only when the user explicitly authorizes that scope for this task. The default permission mode is `auto`; use `manual`, `acceptEdits`, or `plan` when the user asks for tighter control.

On macOS, use `--open-window` by default unless the user asks for background-only operation. This preserves Codex tracking while opening Terminal with its built-in `Pro` profile, attaching it to the Claude Code session, and applying Claude's dark theme for readable contrast. These choices affect only the managed session window, not the user's global Terminal default. Immediately return the manager task ID and native Claude background ID. If the window could not be opened, report the `window.error` and show the emitted `open_window` and `attach` commands. Do not claim that a successfully started process has completed the work.

The first visible launch may trigger a macOS Automation permission prompt. Tell the user to allow Codex or its Python process to control Terminal. If permission is denied or the request times out, keep the background task intact and retry `open-window` only after the user grants permission.

## Track and report

List known tasks and native Claude Code sessions:

```bash
python3 <skill-dir>/scripts/claude_task.py list
```

Read a task's state and recent output:

```bash
python3 <skill-dir>/scripts/claude_task.py status <task-id>
python3 <skill-dir>/scripts/claude_task.py logs <task-id> --lines 120
```

Translate raw progress into:

- current stage;
- completed work;
- evidence or checks run;
- blocker or decision needed;
- next action.

Re-query the native session before every status report. Do not imply continuous monitoring while Codex is not actively running. When the user asks Codex to keep watching, poll at reasonable intervals within the active task and provide updates without modifying the agreed scope.

## Let the user operate Claude Code

When the user wants hands-on control, print or run the attachment command:

```bash
python3 <skill-dir>/scripts/claude_task.py attach <task-id>
```

On macOS, open a new visible Terminal window without taking over Codex's terminal:

```bash
python3 <skill-dir>/scripts/claude_task.py open-window <task-id>
```

Attaching requires an interactive terminal. After the user changes the task directly in Claude Code, re-read its status and logs, then update Codex's understanding before verifying against the latest goal.

## Continue or stop work

Resume a completed or stopped Claude Code session with a follow-up instruction:

```bash
python3 <skill-dir>/scripts/claude_task.py resume <task-id> \
  --instruction "Address the failed check and rerun verification" \
  --open-window
```

Do not resume a session that is still actively running; attach to it or wait. Stop a task only when the user requests it or when continuing would exceed an agreed safety boundary:

```bash
python3 <skill-dir>/scripts/claude_task.py stop <task-id>
```

Limit retries to two unless the user sets another limit. After repeated failure, report the cause and request a decision instead of silently changing scope.

## Verify before declaring completion

Claude Code's completion message is evidence, not final proof. Independently inspect the relevant changes and run proportionate checks from the project when possible. Verify deployment health when deployment was authorized.

Report completion in business language:

1. What outcome was delivered.
2. What changed from the user's perspective.
3. What evidence supports completion.
4. What remains risky or unfinished.
5. Where the user can inspect the result.

Never broaden file access, publish code, deploy, spend money, use credentials, or perform irreversible operations beyond the authorization already given for the managed task.
