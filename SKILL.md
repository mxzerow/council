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

**All cascade logic lives in [core.md](core.md). Read it first.** This file holds only what is Cursor-specific: where the skill lives, config defaults, Start, and how a seat is **dispatched** (Task or the opt-in Gemini API helper).

## Paths

Skill root is the directory that contains this `SKILL.md`. Resolve it to an **absolute native path** before any filesystem or shell work.

- Windows: `$env:USERPROFILE\.cursor\skills\council`
- macOS / Linux: `$HOME/.cursor/skills/council`
- Project install: `<workspace>/.cursor/skills/council` (or `.agents/skills/council`)

Never pass unresolved `~` into Shell or Task prompts. Expand it first.

## Platform (Cursor cloud agents, Linux, macOS)

This skill's paths are written Windows-style (`<run>\file`). On Linux or macOS, including Cursor cloud agents, use forward slashes and the real `~`. If `python` is not found, use `python3` for the scripts. Seats use Task or the Gemini API Python helper; vendor CLI recipes are for Claude Code. Cloud agents receive the API key through the `COUNCIL_GEMINI_API_KEY` secret, never a committed config or key file. In a cloud run the skill must be in the repo's `.cursor/skills/council/` (see the README) or synced; run data is created under `~/.council/runs` on that machine and is not kept after the run.

## Config defaults

Read from [config.yaml](config.yaml). If a key is missing, use:

| Key | Default |
|-----|---------|
| `show_exchange` | `ask` |
| `runs_dir` | `~/.council/runs`, expanded to an absolute path. Resolved as `config.local.yaml` (gitignored) > `config.yaml` > this default; created if missing. It is outside the skill folder on purpose, so syncing or copying the skill never uploads past council text. A real Cursor run (2026-10-23) read run files outside the workspace without trouble |
| `max_reviewers` | `7` (cap on the number of `seats`) |
| `run_retention_count` | `10` |
| `run_retention_days` | `14` |
| `gemini_transport` | `[agy, cursor]`; Cursor silently skips `agy` |
| `gemini_api.enabled` | `false` |
| `gemini_api.tier` | `free` (`free` or `paid`) |
| `gemini_api.free_tier_ack` | `false`; must be true on free |
| `gemini_api.timeout_sec` | `1200` free / `300` paid; override 30..3600 |
| `gemini_api.thinking_level` | `high` (`low`, `medium`, `high`); independent of seat effort |
| `gemini_api.max_output_review` | `32768` (1024..65536) |
| `gemini_api.max_output_rewrite` | `65536` (1024..65536) |
| `gemini_api.max_model_steps` | `2` (0..5) |
| `gemini_api.key_file` | `~/.council/gemini.key`; env `COUNCIL_GEMINI_API_KEY` overrides |

`max_reviewers`, `run_retention_count` and `run_retention_days` must be positive integers; on invalid values stop and ask the user to fix or reconfigure. If `seats` is longer than `max_reviewers`, warn and use the first N seats.

**Roster:** `seats:` (schema, strict `role` values, migration from the old keys: [core.md](core.md#roster-seats)). **Transport:** Gemini follows `gemini_transport`: `api` uses the Python helper via Shell, `cursor` uses Task, `agy` is silently unavailable here. Other families use Task. Never shell out to `agy`, `gemini`, `codex`, `grok` or `agent` from Cursor; the API script is a Python helper, not a vendor CLI. Validate effective API settings with `council_gemini_api.py config`; core.md § Gemini transport defines strict validation and per-key local overrides.

**Default roster:** reviewers Gemini Argon (`breadth-auditor`) → GPT Sol, then the rewriter chain Gemini Argon → Gemini Flash → Claude Sonnet (the first one the Task list offers does the rewrite; today that is Argon). Seat 0 and the integration stage are Opus (the chat model, or an Opus Task seat); they are stages, not roster entries.

## Start

1. Do **not** call `rename_chat` unless the user explicitly asked to rename this chat.
2. Read [config.yaml](config.yaml) and validate per [core.md](core.md#roster-seats): `seats` is a non-empty list (stop and point to the migration rule if the old `reviewers` / `cursor_reviewers` keys are present and `seats` is not); each seat has a known `family` and a `line`; `role` is `reviewer`, `breadth-auditor` or `rewriter`; `show_exchange` is `ask` | `always` | `never`; numeric keys per Config defaults. On invalid config, stop and ask the user to fix or reconfigure.
3. Resolve models first ([Model resolution](#model-resolution-cursor)), then show the roster (count, labels, order) in one short list, flagging every seat that will be skipped (no matching model on any available transport, same-model skip).
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

**Gemini API branch:** walk the configured chain, skipping `agy`. For `api`, require enabled and successful `python -I <shared>/scripts/council_gemini_api.py preflight --root <shared>`; failure logs `cli-preflight-api` and skips only this transport. Resolve with `--source api`. Build reviews with `council_prompt.py build ... --mode inline`; dispatch via Shell:

```text
python -I <shared>/scripts/council_gemini_api.py call --root <shared> --model <slug> --prompt-file <run>/seat-<n>-prompt.md --out <run>/seat-<n>-reply.txt
```

Read only its one-line JSON summary; record it in `seat-<n>-argv-meta.txt`, capture stderr separately, and parse the reply file on success. Surface the free-tier warning once per run. Apply core.md's API failure table (including exclusions, one-day unavailable cooldown, bounded model steps, full timeout, no timeout downgrade, and total call cap). Use Shell's background completion facility for long calls; do not poll or terminate early. For rewrite chunks, save the chunk prompt plus the rewrite tail in a file and add `--kind rewrite`. Do not pass seat effort to the helper. Freeze model/transport after chunk 1 succeeds; on later seat failure, `prepare` and restart on the next transport/model. Recipes are not installed into project copies; these instructions plus core.md are sufficient. If `api` is exhausted/unavailable, advance to the next entry (`cursor` means Task below).

1. **Resolve the model** per [Model resolution](#model-resolution-cursor). If it returns `skip`, skip the seat (`rejected-family`).
2. **Prompt:** write `seat-<n>-prompt.md` as core.md's Cascade loop step 3 says (fields, packet paths, the inlined `reviewer-envelope.md`, and that the seat is gaps only). The Task prompt itself is a short pointer: read that file, then write the envelope to `<run>\seat-<n>-reply.txt`. Do **not** paste `artifact.md` or skill file bodies into the Task prompt.
3. **Call:** `Task` with `subagent_type: generalPurpose`, `model` = the resolved slug, `run_in_background: false`.
4. **Read-only:** tell the seat not to Write, Edit or Delete anything in the workspace, with one exception: it writes its envelope to `<run>\seat-<n>-reply.txt` and replies with a short acknowledgement only. If the file is missing afterwards (the Task returned the envelope as text), save the returned text to that file yourself. **Unverified in Cursor:** whether Task seats can write there and keep full replies out of the conversation; if they cannot, the context saving applies to Claude Code only.
5. **Parse** with `scripts/council_parse.py` ([core.md](core.md#envelope-parser)). Retry = a **new** Task (do not assume resume), also read-only.
6. The raw reply is `seat-<n>-reply.txt` (Cursor writes no separate `seat-<n>-<slug>.md`). Record the slug actually passed to Task in `meta.md` under `dispatched:`.
7. **Failures:** Task has no stderr or argv, so Task seats do not use `seat-<n>-argv-meta.txt` or `cli-preflight.txt` (API seats do). If a Task call errors, save the error text to `<run>\seat-<n>-error.txt` and run `council_resolve.py classify --file` on it. If the error is about the `model` value (not found / not available), treat it as `model-rejected` even when classify says `other`, then follow core.md's rejection flow once. Exhausted Gemini Task failures advance to the next configured transport.

**Task seats and stalls:** the orchestrator cannot poll inside a blocking `Task`. Do **not** peek at `subagents\*.jsonl` mid-flight and call that a stall. If a Task hangs, the user interrupt is the escape. State this if a seat is taking unusually long.

**Rewrite stage:** the `rewriter` seats are an ordered fallback chain (core.md, Stages step 4): resolve them in order using Gemini's configured transport chain or Task for other families. Use the first available model/transport that works, with core.md's restart and call-budget rules. The following details apply to Task dispatches. As of 2026-10-23 the Task list has no Gemini Flash ([catalog.md](catalog.md)), but it has Gemini 4 Argon, which is first in the chain, so the rewrite runs on Argon. Dispatch each chunk as a Task seat on that model: it writes its output (the rewritten chunk followed by the final `REWRITE_END` line) to `<run>\rewrite\chunk-<i>-reply.txt` and acknowledges; if the file is missing, save the returned text there; then `council_rewrite.py check --reply` that file. If nothing in the chain resolves, skip the rewrite, say why in the Return line, and return the integrated artifact.

**Integration stage:** dispatch as a Task seat with family `claude`, line `opus` (resolved like any seat), or run it yourself when the chat model is already Opus (then write your own envelope to `<run>\seat-<n>-reply.txt` and parse it like any seat with `--role integrator`, using the next seat number after the last reviewer). If the chat model is not Opus and no Opus Task model is available, warn the user that the draft and integration are not Opus-grade.

### Model resolution (Cursor)

For the `api` transport use `--source api`; never restrict it to the Task list. For Task, take the model slugs the tool offers (drop `inherit`), one per line, and write them to `<runs_dir>\task-models.txt` (create `runs_dir` if missing). Do **not** use `cursor-agent --list-models`: it lists far more models than Task accepts. Resolve Task seats per [core.md](core.md#model-resolution) with `--source file:<runs_dir>\task-models.txt` (state file `<runs_dir>\_resolved.json`). The script picks the newest version of the seat's family and line and never crosses lines. On `skip` (`no-line-match` or `no-catalog`), try the next configured transport, then skip the seat (`rejected-family`) when none remains and say why. If a Task call is rejected, follow core.md's rejection flow once.

## Return (Cursor)

Shared: [core.md](core.md#return). Cursor exchange handling: if exchange was requested, write `<run>/exchange.md` with the transcript and set `<runs_dir>/last-exchange.md` to a single line with its absolute path (or run id). Secret-hygiene applies to both.

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
