---
description: Run the Claude Swarm live demo (simulated agents, no API key required)
allowed-tools: Bash
---

Run `command -v claude-swarm >/dev/null 2>&1 || pip install claude-swarm`, then
run `claude-swarm --demo --no-ui` to show a simulated multi-agent run with no
`ANTHROPIC_API_KEY` required. Explain to the user that this is a scripted
simulation, not a real agent run — use `/claude-swarm:swarm` for that.
