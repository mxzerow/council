---
name: council
description: >-
  Runs a sequential multi-model review cascade. The current chat model writes
  the first answer, reviewers on other model families each report gaps only,
  and a single integration pass (Opus) resolves every gap and writes the
  revision. Use when the user invokes /council or /Council, or clearly asks for
  a council pass, council review, wide review, or multi-model review as the task
  itself. Do not use when those phrases are quoted, negated (e.g. "don't do a
  wide review"), incidental, or the work is trivia / a one-liner / needs an
  answer in under a minute.
---

# Council

You are the orchestrator. Council is a **workflow**, not a Cursor mode: do **not** `SwitchMode`.

**All cascade logic lives in [core.md](core.md). Read it first.** This file holds only what is Cursor-specific: where the skill lives, config defaults, Start, and how a seat is **dispatched** (the Task tool).

## Paths

Skill root is the directory that contains this `SKILL.md`. Resolve it to an **absolute native path** before any filesystem or shell work.

- Windows: `$env:USERPROFILE\.cursor\skills\council`
- macOS / Linux: `$HOME/.cursor/skills/council`
- Project install: `<workspace>/.cursor/skills/council` (or `.agents/skills/council`)

Never pass unresolved `~` into Shell or Task prompts. Expand it first.

## Config defaults

Read from [config.yaml](config.yaml). If a key is missing, use:

| Key | Default |
|-----|---------|
| `show_exchange` | `ask` |
| `runs_dir` | `<skill-root>\runs`. Resolved as `config.local.yaml` (gitignored) > `config.yaml` > this default; created if missing. Task seats must be able to read it, so keep it inside the workspace or skill tree until that is verified |
| `max_reviewers` | `7` (cap on the number of `seats`) |
| `run_retention_count` | `10` |
| `run_retention_days` | `14` |

`max_reviewers`, `run_retention_count` and `run_retention_days` must be positive integers; on invalid values stop and ask the user to fix or reconfigure. If `seats` is longer than `max_reviewers`, warn and use the first N seats.

**Roster:** `seats:` (schema, strict `role` values, migration from the old keys: [core.md](core.md#roster-seats)). **Transport:** every seat is dispatched through Task, whatever its family. Never shell out to `agy`, `gemini`, `codex`, `grok` or `agent` from Cursor.

**Default roster:** reviewers Gemini Argon (`breadth-auditor`) → GPT Sol, then the rewriter chain Gemini Argon → Gemini Flash → Claude Sonnet (the first one the Task list offers does the rewrite; today that is Argon). Seat 0 and the integration stage are Opus (the chat model, or an Opus Task seat); they are stages, not roster entries.

## Start

1. Do **not** call `rename_chat` unless the user explicitly asked to rename this chat.
2. Read [config.yaml](config.yaml) and validate per [core.md](core.md#roster-seats): `seats` is a non-empty list (stop and point to the migration rule if the old `reviewers` / `cursor_reviewers` keys are present and `seats` is not); each seat has a known `family` and a `line`; `role` is `reviewer`, `breadth-auditor` or `rewriter`; `show_exchange` is `ask` | `always` | `never`; numeric keys per Config defaults. On invalid config, stop and ask the user to fix or reconfigure.
3. Resolve models first ([Model resolution](#model-resolution-cursor)), then show the roster (count, labels, order) in one short list, flagging every seat that will be skipped (no matching model in the Task list, same-model skip).
4. Confirm unless the user already decided:
   - `/council proceed` or `proceed` → run
   - `/council configure` or `configure` / `reconfigure` → [Reconfigure](#reconfigure)
   - One-shot override in the same message (`this time only: Grok then Claude`) → use that roster for this run; do not write `config.yaml` unless they say to save
   - A full request after `/council` (topic included) → treat as **Proceed**

   Otherwise try **one** `AskQuestion`: Proceed / Reconfigure then run / Reconfigure only. If `AskQuestion` is missing or fails, print the roster and `Reply proceed / reconfigure / reconfigure only`; do not stall.
5. Exchange visibility: `always` → transcript; `never` → final only; `ask` → ask once, or honor "show exchange" in the message, or default to final-only when ambiguous.

## Same-model skip (Cursor)

Rule and family table: [core.md](core.md#same-model-skip). Seat 0 is the selected chat model. For an artifact the user supplied, use the author's family if known (for "review the plan above" the previous assistant message is the chat model's, so the chat model's family), else `unknown`, which skips no reviewer.

| Seat-0 family | Effective path (default roster) |
|---|---|
| Claude, Grok, Composer, or `unknown` (author not known) | Argon → Sol, then integrate, then rewrite (nothing skipped) |
| GPT | Argon, then integrate, then rewrite (Sol skipped) |
| Gemini | Sol, then integrate (Argon skipped as a reviewer; the rewrite still runs) |

The integration and rewrite stages are **not** reviews, so they are never skipped by this rule (see [core.md](core.md#stages)).

## Dispatch (Cursor)

This is the only logic that differs from Claude Code. For each seat core.md says to dispatch:

1. **Resolve the model** per [Model resolution](#model-resolution-cursor). If it returns `skip`, skip the seat (`rejected-family`).
2. **Prompt:** write `seat-<n>-prompt.md` as core.md's Cascade loop step 3 says (fields, packet paths, the inlined `reviewer-envelope.md`, and that the seat is gaps only). The Task prompt itself is a short pointer: read that file, then write the envelope to `<run>\seat-<n>-reply.txt`. Do **not** paste `artifact.md` or skill file bodies into the Task prompt.
3. **Call:** `Task` with `subagent_type: generalPurpose`, `model` = the resolved slug, `run_in_background: false`.
4. **Read-only:** tell the seat not to Write, Edit or Delete anything in the workspace, with one exception: it writes its envelope to `<run>\seat-<n>-reply.txt` and replies with a short acknowledgement only. If the file is missing afterwards (the Task returned the envelope as text), save the returned text to that file yourself. **Unverified in Cursor:** whether Task seats can write there and keep full replies out of the conversation; if they cannot, the context saving applies to Claude Code only.
5. **Parse** with `scripts/council_parse.py` ([core.md](core.md#envelope-parser)). Retry = a **new** Task (do not assume resume), also read-only.
6. The raw reply is `seat-<n>-reply.txt` (Cursor writes no separate `seat-<n>-<slug>.md`). Record the slug actually passed to Task in `meta.md` under `dispatched:`.
7. **Failures:** Task has no stderr or argv, so `seat-<n>-argv-meta.txt` and `cli-preflight.txt` are not used in Cursor. If a Task call errors, save the error text to `<run>\seat-<n>-error.txt` and run `council_resolve.py classify --file` on it. If the error is about the `model` value (not found / not available), treat it as `model-rejected` even when classify says `other`, then follow core.md's rejection flow once.

**Task seats and stalls:** the orchestrator cannot poll inside a blocking `Task`. Do **not** peek at `subagents\*.jsonl` mid-flight and call that a stall. If a Task hangs, the user interrupt is the escape. State this if a seat is taking unusually long.

**Rewrite stage:** the `rewriter` seats are an ordered fallback chain (core.md, Stages step 4): resolve them in order and use the first the Task list offers. As of 2026-10-23 the Task list has no Gemini Flash ([catalog.md](catalog.md)), but it has Gemini 4 Argon, which is first in the chain, so the rewrite runs on Argon. Dispatch each chunk as a Task seat on that model: it writes its output (the rewritten chunk followed by the final `REWRITE_END` line) to `<run>\rewrite\chunk-<i>-reply.txt` and acknowledges; if the file is missing, save the returned text there; then `council_rewrite.py check --reply` that file. If nothing in the chain resolves, skip the rewrite, say why in the Return line, and return the integrated artifact.

**Integration stage:** dispatch as a Task seat with family `claude`, line `opus` (resolved like any seat), or run it yourself when the chat model is already Opus (then write your own envelope to `<run>\seat-<n>-reply.txt` and parse it like any seat with `--role integrator`, using the next seat number after the last reviewer). If the chat model is not Opus and no Opus Task model is available, warn the user that the draft and integration are not Opus-grade.

### Model resolution (Cursor)

Take the model slugs your Task tool offers (the list in its definition; drop `inherit`), one per line, and write them to `<runs_dir>\task-models.txt` (create `runs_dir` if it is missing; it does not depend on the run folder, so this can happen before the roster is shown). Do **not** use `cursor-agent --list-models`: it lists far more models than the Task tool accepts. Then resolve each seat per [core.md](core.md#model-resolution) with `--source file:<runs_dir>\task-models.txt` (state file `<runs_dir>\_resolved.json`). The script picks the newest version of the seat's family and line and never crosses lines. If it returns `skip` (`no-line-match` or `no-catalog`), skip the seat (`rejected-family`) and say why. If a Task call is rejected, follow core.md's rejection flow once; never loop through candidates.

## Return (Cursor)

Shared: [core.md](core.md#return). Cursor exchange handling: if exchange was requested, write `<run>/exchange.md` with the transcript and set `<skill-root>/last-exchange.md` to a single line with its absolute path (or run id). Secret-hygiene applies to both.

## Reconfigure

Read [catalog.md](catalog.md). Then:

1. Ask how many seats (minimum 1; warn above `max_reviewers`).
2. For each seat, in order, ask `family`, `line`, `effort` and an optional `role`. Default the first Gemini seat to `role: breadth-auditor` unless the user opts out.
3. Ask whether to persist and the `show_exchange` default (`ask` / `always` / `never`).
4. If they persist, write the `seats:` list of [config.yaml](config.yaml); preserve any existing `role: breadth-auditor` unless the user explicitly removes that seat or role. One-shot overrides do not persist unless they say to save.
5. If persisted, add an entry to [CHANGELOG.md](CHANGELOG.md) (dated, `Changed`/`Added`/`Removed` as fits). Skip only when nothing observable changed.
6. Run the cascade unless they chose **Reconfigure only**.

## Examples

**Run**

```
/council Review this auth design and tighten the threat model.
```

Draft seat 0 → save `seat-0-*.md` → reviewers report gaps → integration pass → return the integrated artifact with any rejected gaps listed.

**Reuse existing artifact**

```
/council proceed review the plan above
```

Pack the prior plan as `artifact.md`; skip the seat-0 rewrite; cascade reviewers.

**Reconfigure**

```
/council configure
```

Example: user picks 3 seats (Gemini Argon, Claude Opus, GPT Sol), persists, `show_exchange: ask`. Write the `seats:` list (keep `max_reviewers` and the other keys). Then run the cascade unless they chose Reconfigure only.
