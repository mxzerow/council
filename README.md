# Council

A sequential **multi-model review cascade** for [Cursor](https://cursor.com) (`/council`) and an optional [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sibling.

The current chat model writes the first answer. Configurable reviewers then run **one at a time**, each reading the last artifact, filing gaps, and either revising or leaving it `UNCHANGED`. There is no parallel first-answers pass and no chairman synthesis — the last successful seat's artifact is the result.

[![skills.sh](https://skills.sh/b/mxzerow/council)](https://skills.sh/mxzerow/council)

See [CHANGELOG.md](CHANGELOG.md) for what's changed in the roster and config over time.

## Install (Cursor)

```bash
npx skills add mxzerow/council -g -a cursor
```

That copies this skill to `~/.cursor/skills/council` (Windows: `%USERPROFILE%\.cursor\skills\council`). Restart Cursor or start a new agent chat, then run `/council`.

Manual install:

```bash
git clone https://github.com/mxzerow/council.git ~/.cursor/skills/council
```

PowerShell:

```powershell
git clone https://github.com/mxzerow/council.git "$env:USERPROFILE\.cursor\skills\council"
```

**Use `-a cursor` only.** Installing this repo into Claude Code with `npx skills` would load the Cursor orchestrator (it uses Cursor's `Task` tool). Claude Code needs the sibling files under [`claude/`](claude/) — see below.

## Use

In Cursor:

```
/council
/council proceed
/council this architecture doc
/council this time only: Grok then Gemini
```

Say `/council` with a topic, point it at an existing draft, or ask for a council / wide / multi-model review. The skill confirms the roster unless you already said `proceed` or included the topic in the same message.

## When to invoke / how to pack

Prefer **one coherent mid-size artifact** over a mega-dump or many tiny councils. Cascade seats do **not** break work up (no subagents; path-scoped reads). Partitioning is an **orchestration** step before the cascade.

### When to invoke

Invoke when the deliverable is high-stakes, you want cross-vendor challenge on one coherent artifact, and a draft exists (or seat 0 can write one).

Skip when: trivia / one-liners / single-file nits; answers needed in under a minute; routine work; or the “artifact” would be an unbounded dump (“review this whole feature”).

### How to pack a run

1. **Artifact type:** design/plan prose, or a bounded set of path-fenced files for implementation review (tighter scope than prose — hot paths only).
2. **Size check:** one human sitting. If not, stop and partition (below).
3. **`request.md`:** short hints — goal, constraints, non-goals, absolute paths + stable anchors. Do not paste whole modules.
4. **`artifact.md`:** full text of the draft under review. Prefer live paths for on-disk code; do not duplicate entire trees.
5. **List only files seats must read** — every listed file is paid by every seat.
6. **`review_roots`:** only when CLI seats need `--add-dir` for those live paths.
7. **Exchange:** final-only unless you need the transcript.

### When the problem is too big (cut plan)

| Step | What happens |
|------|----------------|
| Framing / cut plan | Seat 0 / chat model proposes goals, interfaces, invariants, non-goals, partition map + links (**size-bounded** framing) |
| Gemini breadth | Breadth-check the cut plan (standalone or Framing Council with Gemini as breadth-auditor) |
| Human veto | **Required when input is large** — approve, reject, or reshape before partition Councils |
| Gate + freeze | No partition runs until framing converged (or human-accepted risks); then freeze/version the framing |
| Partition Councils | One deep dive per cut; `request.md` carries frozen invariants; full framing listed only if touching shared interfaces |
| Integration (optional) | Merged plan / conflict list |

**Who proposes cuts (decided):** Seat 0 / chat model → Gemini breadth on the cut plan → **human veto if input is large**.

Keep the framing doc small: it is multiplied across seats × partitions. Prefer concurrent partitions against a **frozen** shared contract; if the contract cannot freeze, run dependents sequentially.

Anti-patterns: mega-`artifact.md`; parallel micro-councils with no framing; starting partitions while framing still has open blocking gaps; summarizing the codebase into the artifact to “save tokens”; expecting cascade seats to sub-delegate around context limits.

Council already does **packet hygiene** (short `request.md`, path-only prompts, `open-gaps.md`, compact envelopes, churn/same-model skip). It does **not** silently compress or summarize `artifact.md`.

## What you need

| Seat | Requirement |
|------|-------------|
| Orchestrator (seat 0) | Cursor agent chat |
| Cursor reviewers (`kind: cursor`) | Cursor account usage for that model |
| Gemini (`command: agy`) | [Antigravity CLI](https://antigravity.google/cli) signed in with Google — **no API key** |
| GPT (`command: codex`) | [Codex CLI](https://github.com/openai/codex) (`codex login`). ChatGPT login uses ChatGPT/Codex quota |

Default roster (interleaved since 2026-09-12 — Gemini is cheap enough to run every other seat): **Gemini (breadth-auditor) → Claude (Cursor) → Gemini → Codex CLI (GPT) → Gemini → Grok → Gemini**. Reconfigure in chat (`/council configure`) or edit [`config.yaml`](config.yaml).

CLI recipes in [`models.md`](models.md) are written for **PowerShell**. Cursor `Task` seats work on any OS. Gemini/Codex seats on macOS/Linux need the equivalent PATH and preflight commands.

## Claude Code sibling

Claude Code cannot call Cursor's `Task` tool. [`claude/SKILL.md`](claude/SKILL.md) is a sibling orchestrator that drives Cursor's `agent` CLI, plus `agy`/`gemini` and `codex`, using the **same** `config.yaml` / `models.md` / reviewer contracts.

1. Install the Cursor skill first (shared files).
2. Copy the sibling:

```powershell
$dest = Join-Path $env:USERPROFILE ".claude\skills\council"
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item "$env:USERPROFILE\.cursor\skills\council\claude\SKILL.md" $dest
Copy-Item "$env:USERPROFILE\.cursor\skills\council\claude\dispatch.md" $dest
```

```bash
mkdir -p ~/.claude/skills/council
cp ~/.cursor/skills/council/claude/SKILL.md ~/.claude/skills/council/
cp ~/.cursor/skills/council/claude/dispatch.md ~/.claude/skills/council/
```

Then invoke `/council` in Claude Code. If the shared Cursor files are missing, the sibling stops rather than inventing a second roster.

## Layout

| File | Role |
|------|------|
| [`SKILL.md`](SKILL.md) | Cursor orchestrator |
| [`config.yaml`](config.yaml) | Roster and defaults (shared) |
| [`models.md`](models.md) | Model catalog and CLI recipes |
| [`reviewer-envelope.md`](reviewer-envelope.md) | Compact envelope for ordinary seats |
| [`reviewer-prompt.md`](reviewer-prompt.md) | Full contract (breadth-auditor / implementation) |
| [`claude/SKILL.md`](claude/SKILL.md) | Claude Code orchestrator |
| [`claude/dispatch.md`](claude/dispatch.md) | Claude Code CLI invocation |

Run folders under `runs/` are local working state (prompts, artifacts, CLI logs). They are gitignored and must not be published — they can contain the text you sent through Council.

## License

[MIT](LICENSE)
