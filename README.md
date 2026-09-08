# ai_skills

Personal collection of [Agent Skills](https://agentskills.io/specification)
— self-contained `SKILL.md` capability packages that Claude Code, opencode,
pi, and other compatible agents load on demand. Each skill lives in its own
directory under `skills/`.

## Skills

| Skill  | Description |
| ------ | ----------- |
| [gnhf](skills/gnhf/SKILL.md) | Launch a bounded, low-supervision overnight coding agent run against one well-specced task, in an isolated worktree. |

## Install

The [`skills` CLI](https://github.com/vercel-labs/skills) is the easiest
path — it detects installed agents and symlinks the skill into each one:

```bash
npx skills add pythoninthegrass/ai_skills
```

Useful flags: `-g` installs user-level instead of project-level, `-a
claude-code,opencode,pi` targets specific agents, `-s gnhf` installs one
skill by name, `-y` skips confirmation prompts, `--copy` copies files
instead of symlinking. `npx skills list`, `npx skills update`, and `npx
skills remove` manage what's installed.

To try a skill without installing it:

```bash
npx skills use pythoninthegrass/ai_skills@gnhf | claude
```

### Manual install

`skills add` installs a pinned snapshot (refreshed via `npx skills update`),
not a live link to a working checkout. For local development, or on a
machine without npx, clone the repo and symlink directly — pi and opencode
both auto-load `~/.agents/skills/`, and opencode also auto-loads
`~/.claude/skills/`, so two symlinks cover all three agents:

```bash
git clone https://github.com/pythoninthegrass/ai_skills.git ~/git/ai_skills
mkdir -p ~/.agents/skills ~/.claude/skills
ln -s ~/git/ai_skills/skills/gnhf ~/.agents/skills/gnhf   # pi, opencode
ln -s ~/git/ai_skills/skills/gnhf ~/.claude/skills/gnhf   # Claude Code
```

Documented discovery locations, per agent (see each agent's own docs for
the current, authoritative list — these locations are more stable than any
CLI flags):

| Agent | Personal | Project | Declare extra dirs |
| ----- | -------- | ------- | ------------------- |
| [Claude Code](https://docs.claude.com/en/docs/claude-code) | `~/.claude/skills/<name>/` | `.claude/skills/<name>/` | — |
| [opencode](https://opencode.ai/docs) | `~/.config/opencode/skill(s)/<name>/` | `.opencode/skill(s)/<name>/` | `skills.paths` in `opencode.json` |
| [pi](https://github.com/badlogic/pi-mono) | `~/.pi/agent/skills/`, `~/.agents/skills/` | `.pi/skills/`, `.agents/skills/` | `skills` array in `~/.pi/agent/settings.json`, or `pi --skill <path>` |

## Verify it loaded

Skills are scanned at startup — restart the agent (or start a new session)
after installing. Then:

- Claude Code: run `/gnhf`
- pi: run `/skill:gnhf`
- opencode: mention what you want done; opencode picks the skill up from
  its description
- Either way: `npx skills list -g` shows what the CLI has installed

## Repo layout

```text
skills/
└── gnhf/
    ├── SKILL.md
    └── scripts/
        └── smoke-test.sh
```

To add a new skill, create a directory under `skills/` with a `SKILL.md`
carrying `name` and `description` frontmatter (`npx skills init <name>`
scaffolds one), then install/symlink it the same way as above.

## Security

A skill's `SKILL.md` is instructions the model reads and follows, and any
bundled scripts are code the model can execute. Review both before
installing a skill from anywhere, including this repo. `gnhf` bundles
`scripts/smoke-test.sh`.
