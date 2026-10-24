# Council

A sequential **multi-model review** for [Cursor](https://cursor.com) and [Claude Code](https://docs.anthropic.com/en/docs/claude-code), run with `/council`. Both harnesses share one set of rules ([`core.md`](core.md)); only the way a model is called differs.

## How a run works

1. **Draft (seat 0).** Opus writes the draft, or you supply one.
2. **Review, gaps only.** Reviewers from other model families (default: Gemini Argon, then GPT Sol) each read the draft and report gaps. They never rewrite it.
3. **Integrate, once.** Opus resolves every gap (accept or reject with a reason) and writes the revision. Anything it rejects is printed at the end, with its reason.
4. **Rewrite, once.** A writer model polishes the result for a reader who is not a specialist, in small chunks, adding short explanations of jargon. The writers are an ordered fallback chain (default: Gemini Argon, then Gemini Flash, then Claude Sonnet); the first that works does the whole rewrite. A script checks every chunk and keeps the original wording of any chunk that fails.

There is no parallel first-answers pass and no chairman synthesis. See [`core.md`](core.md#stages).

## What you need

| Piece | Requirement |
|-------|-------------|
| Orchestrator | Cursor agent chat, or Claude Code |
| Python 3 | Runs the helper scripts in [`scripts/`](scripts/) (standard library only) |
| `gemini` seats | [Antigravity CLI](https://antigravity.google/cli) (`agy`), signed in with Google. No API key |
| `gpt` seats | [Codex CLI](https://github.com/openai/codex) (`codex login`). ChatGPT login uses ChatGPT/Codex quota |
| `grok` seats | The Grok CLI (`agent` on PATH) |
| Fallback for `gemini` seats | Cursor's CLI (`cursor-agent login`): used when `agy` is out of quota; spends Cursor usage |
| Cursor | Every seat goes through Cursor's own Task tool, so the CLIs above are not needed there |

Recipes for calling each CLI are written for PowerShell on Windows ([`recipes/`](recipes/)).

## Install

**Cursor.** Clone this repository to `~/.cursor/skills/council` (Windows: `%USERPROFILE%\.cursor\skills\council`), restart Cursor or start a new agent chat, and run `/council`.

**Claude Code.** The Claude Code skill is the `claude/` folder of the same repository, linked into Claude's skills folder so both read one copy of the shared files:

```powershell
# Windows (no admin rights needed for a junction)
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\council" -Target "$env:USERPROFILE\.cursor\skills\council\claude"
```

```bash
# macOS / Linux
ln -s ~/.cursor/skills/council/claude ~/.claude/skills/council
```

Do **not** copy `claude/SKILL.md` on its own: it finds `core.md`, `config.yaml` and the scripts in the parent folder, and stops if they are missing.

### Cursor cloud agents

Cloud agents work on a fresh clone of a repository and do not see `~/.cursor/skills`. Pick one route.

**Route A: sync your user skills.**

1. In Cursor, open Settings, then Agents, then Context and Tools.
2. Turn on "Sync Skills for Cloud Agents".
3. Start a new cloud agent and run `/council`.

**Route B: put a copy in the project repository.** Use this when the repository should carry the skill.

1. Run (replace the path with your project):

   ```bash
   python -I ~/.cursor/skills/council/scripts/council_install.py install --target /path/to/your/project
   ```

2. Review `.cursor/skills/council/` in the project, then commit and push it. It holds the Cursor runtime files and four scripts only, no run data.
3. Start a cloud agent on that repository and run `/council`.
4. To update later, run the same command again. A file you edited in the copy (for example `config.yaml`) is reported and left alone; add `--force` to overwrite it. `python -I .../council_install.py check --target ...` shows drift without changing anything.

On a cloud machine the skill runs on Linux: paths use `/`, the scripts run as `python3` if `python` is missing, and run data goes to `~/.council/runs` on that machine and is discarded with it. Seats go through Cursor's Task tool, so the CLI recipes (`agy`, `codex`) are not used there. Not yet tested on a real cloud agent; the installed copy was checked to be discovered by a local `cursor-agent`.

## Use

```
/council
/council proceed
/council this architecture doc
/council this time only: models: gemini argon, gpt sol
/council configure
```

Say `/council` with a topic, point it at an existing draft, or ask for a council or wide review. It confirms the roster unless you already said `proceed` or gave the topic in the same message.

## Configure

[`config.yaml`](config.yaml) holds one roster for both harnesses:

```yaml
seats:
  - family: gemini     # gemini | gpt | claude | grok | composer
    line: argon        # the model line; the version is resolved to the newest of that line
    effort: medium
    role: breadth-auditor   # reviewer (default) | breadth-auditor | rewriter
```

A seat names a model family and line, never a version: the newest version of that line is picked from the live catalog on every run, and a different line is never substituted (Flash never becomes Argon). Add `pin: <exact slug>` to freeze a seat. An unrecognized `role` is a config error. A family with no way to be called in the current harness is skipped. Claude Code skips `claude` seats (same family as the orchestrator).

Machine-specific overrides go in `config.local.yaml` (gitignored; see [`config.local.example.yaml`](config.local.example.yaml)), for example `runs_dir`. Run folders default to `~/.council/runs` (outside the skill folder).

## Layout

| Path | Role |
|------|------|
| [`core.md`](core.md) | The shared rules: roster, stages, parsing, model resolution, return format |
| [`SKILL.md`](SKILL.md) | Cursor skill: paths, Start, how a seat is called through Task |
| [`claude/SKILL.md`](claude/SKILL.md) | Claude Code skill: Start, the CLI mapping, the Opus integration stage |
| [`config.yaml`](config.yaml) | The roster and defaults (shared) |
| [`reviewer-envelope.md`](reviewer-envelope.md), [`reviewer-prompt.md`](reviewer-prompt.md) | What a reviewer seat is told |
| [`catalog.md`](catalog.md) | Cursor Task model reference |
| [`recipes/`](recipes/) | Per-CLI call recipes for Claude Code (`common`, `agy`, `codex`, `grok`) |
| [`partitioning.md`](partitioning.md) | What to do when the input is too big for one cascade |
| [`scripts/`](scripts/) | `council_parse.py` (reply parser and run state), `council_resolve.py` (newest model version), `council_rewrite.py` (safe chunked rewrite), `council_prompt.py` (builds seat prompts, inline when they fit) |

## Tests

```bash
cd scripts && python -B -m unittest discover -p "test_*.py"
```

The scripts are what keeps the model-written parts honest: replies are parsed and bookkept by code, a reviewer cannot change the artifact, and a rewritten chunk is thrown away if it loses a number, a heading, a negation or any protected text.

## Status

Verified live in Claude Code on 2026-10-23 (a full run: Gemini Argon, GPT Sol, Opus integration, Gemini Flash rewrite). Cursor's own agent read the skill and reported READY WITH FIXES (fixed since): the Task tool offers Gemini 4 Argon, Claude Opus 5.5 and GPT-5.6 Sol but **no Gemini Flash**, so in Cursor the rewrite runs on Gemini 4 Argon, first in the rewriter chain. Task seats writing their reply to a file, and shell access for the scripts outside plan mode, have not been exercised yet; the first real Cursor run is the check.

## Run data

`~/.council/runs` holds working state (prompts, artifacts, CLI logs). It can contain the text you sent through Council, which is why it lives outside the skill folder and outside git. On Windows with a small system drive, make `~/.council` a junction to another drive (`New-Item -ItemType Junction -Path $env:USERPROFILE\.council -Target D:\.council`).

## License

[MIT](LICENSE)
