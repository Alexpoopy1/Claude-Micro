---
name: claude-swarm
description: Delegate a large, decomposable coding task (e.g. "refactor auth from Express to Next.js API routes", "add JWT auth across the API") to Claude Swarm, a multi-agent orchestrator that splits the task into a dependency graph and runs parallel Claude Code agents with file-conflict detection, budget enforcement, and an Opus quality gate. Use when a task is big enough to benefit from parallel subagents with real cost/budget limits, not for small single-file edits.
license: MIT
version: 0.2.0
---

# Claude Swarm

Wraps the `claude-swarm` CLI (https://github.com/affaan-m/claude-swarm), a
multi-agent orchestration tool built on the Claude Agent SDK. It decomposes a
task into a dependency graph, runs independent subtasks in parallel with
pessimistic file locking to avoid agents clobbering each other, enforces a
hard USD budget, and finishes with an Opus quality-gate review of the
combined diff.

## When to use this

Reach for `claude-swarm` instead of doing the work directly when **all** of
these hold:

- The task naturally decomposes into several largely-independent subtasks
  (e.g. "add routes + middleware + tests + docs" for a new feature).
- The user wants real parallelism and is fine spending API budget on worker
  agents (it needs its own `ANTHROPIC_API_KEY`, billed separately from this
  session).
- The task is too large to comfortably hold in one pass, but small edits,
  single-file changes, or anything requiring tight back-and-forth with the
  user should just be done directly instead — don't reach for a swarm for
  those.

Always run `--dry-run` first for anything non-trivial so the user can see the
decomposition plan and estimated shape before spending budget, unless they've
explicitly asked to just run it.

## Setup (once per environment)

Check whether the CLI is already installed before doing anything else:

```bash
command -v claude-swarm >/dev/null 2>&1 && claude-swarm --version
```

If missing, install from PyPI:

```bash
pip install claude-swarm
```

Real (non-demo) runs require `ANTHROPIC_API_KEY` to be set in the
environment — this key belongs to the user's own Anthropic account and is
separate from the Claude Code session's own credentials. If it isn't set,
tell the user rather than guessing/inventing one; `--demo` works without a
key.

## Usage

```bash
# See it work with no API key (simulated agents, live TUI)
claude-swarm --demo

# Show the decomposition plan without executing or spending budget
claude-swarm --dry-run "Add user authentication with JWT"

# Run for real
claude-swarm "Refactor auth module from Express middleware to Next.js API routes"

# Tune concurrency, budget, retries
claude-swarm --max-agents 6 --budget 3.0 --retry 2 "Build a REST API for user management"

# Skip the Opus quality-gate review for a faster/cheaper run
claude-swarm --no-quality-gate "Quick fix: update README"

# Non-interactive environments (this session's Bash tool has no TTY)
claude-swarm --no-ui "..."

# Review past runs
claude-swarm sessions
claude-swarm replay <session-id>
```

Key flags: `-d/--cwd` (project dir, default `.`), `-n/--max-agents` (default
4), `-m/--model` (decomposition model, default `opus`), `-b/--budget` (USD,
default 5.0), `-r/--retry` (default 1), `-c/--config` (path to a
`swarm.yaml` topology file), `--quality-gate/--no-quality-gate` (default
on).

**Always pass `--no-ui`** when invoking from inside a Claude Code Bash tool
call — the default Rich dashboard is a live TTY UI and will not render
correctly through a non-interactive shell.

## Reporting back

After a run, summarize for the user: tasks completed/failed, total cost,
the quality-gate verdict (if enabled), and the session ID (`claude-swarm
replay <id>` to inspect). Don't silently swallow a failed or partial run —
surface it plainly.
