# Claude Swarm (plugin)

Claude Code plugin wrapping [`claude-swarm`](https://github.com/affaan-m/claude-swarm)
(installed separately from PyPI: `pip install claude-swarm`) — a multi-agent
orchestration CLI built on the Claude Agent SDK.

Provides:
- a `claude-swarm` **skill** (auto-invoked when a task looks like a good fit
  for parallel decomposition) — see `skills/claude-swarm/SKILL.md`
- `/claude-swarm:swarm [task]` — run a real swarm
- `/claude-swarm:swarm-demo` — run the no-API-key demo

This plugin does not vendor the tool's source; it is a thin wrapper that
installs and drives the upstream CLI. See the upstream repository for the
full source, license (MIT, included here), and documentation.
