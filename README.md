# Claude-Micro

A [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces) hosting:

| Plugin | What it is | Provides |
| --- | --- | --- |
| [`img2threejs`](plugins/img2threejs) | Reconstruct a reference image as a code-only, procedural Three.js model via a staged, quality-gated sculpting pipeline. Vendored from [img2threejs/img2threejs](https://github.com/img2threejs/img2threejs) (Apache-2.0), which is itself already shaped as a Claude Code skill. | Skill: `img2threejs` |
| [`claude-swarm`](plugins/claude-swarm) | Multi-agent task orchestration — decompose a task into a dependency graph and run parallel Claude Code agents with budget enforcement and an Opus quality gate. Wraps the [`claude-swarm`](https://github.com/affaan-m/claude-swarm) PyPI CLI (MIT). | Skill: `claude-swarm`; commands: `/claude-swarm:swarm`, `/claude-swarm:swarm-demo` |

## Install

From inside Claude Code:

```
/plugin marketplace add alexpoopy1/claude-micro
/plugin install img2threejs@claude-micro
/plugin install claude-swarm@claude-micro
```

`claude-swarm` additionally requires the CLI itself (`pip install
claude-swarm`) and an `ANTHROPIC_API_KEY` for real (non-demo) runs — the
skill and `/claude-swarm:swarm` command check for and explain this.
`img2threejs` is pure Python 3.10+ stdlib, no extra installs required.

## Layout

```
.claude-plugin/marketplace.json   # marketplace manifest listing both plugins
plugins/
  img2threejs/
    .claude-plugin/plugin.json
    skills/img2threejs/           # vendored upstream skill (SKILL.md + forge/ + grimoire/ + ...)
  claude-swarm/
    .claude-plugin/plugin.json
    skills/claude-swarm/SKILL.md  # usage guide + when-to-use
    commands/                     # /claude-swarm:swarm, /claude-swarm:swarm-demo
```

## Updating the vendored img2threejs skill

`plugins/img2threejs/skills/img2threejs` is a snapshot of the upstream
repository (minus `.git`/`.github`). To pick up upstream changes, re-copy its
contents over that directory and bump the version in both
`plugins/img2threejs/.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json`.
