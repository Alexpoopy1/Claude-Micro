---
description: Run Claude Swarm on a task — decomposes it into parallel subagents with budget enforcement and an Opus quality gate
argument-hint: "[task description]"
allowed-tools: Bash
---

Run the `claude-swarm` multi-agent orchestrator on this task: $ARGUMENTS

Steps:
1. Check the CLI is installed: `command -v claude-swarm >/dev/null 2>&1 || pip install claude-swarm`.
2. If `$ARGUMENTS` is empty, ask the user what task to run instead of guessing.
3. Confirm `ANTHROPIC_API_KEY` is set (`echo ${ANTHROPIC_API_KEY:+set}`); if not, tell the user it's required for a real run (or suggest `--demo`).
4. First run with `--dry-run --no-ui` to show the decomposition plan, unless the user already said to just run it.
5. On confirmation, run for real with `--no-ui`: `claude-swarm --no-ui "$ARGUMENTS"`.
6. Summarize: tasks completed/failed, total cost, quality-gate verdict, session ID.

See the `claude-swarm` skill for full flag reference (budget, max-agents, retry, config).
