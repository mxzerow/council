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

**All cascade logic lives in [core.md](core.md). Read it first.** This file holds only what is Cursor-specific: where the skill lives, config defaults, Start, and how a seat is **dispatched** (Gemini: api/agy through the shell; Grok: Cursor CLI through the shell; gpt/claude/composer: Task).

## Paths

Skill root is the directory that contains this `SKILL.md`. Resolve it to an **absolute native path** before any filesystem or shell work.

- Windows: `$env:USERPROFILE\.cursor\skills\council`
- macOS / Linux: `$HOME/.cursor/skills/council`
- Project install: `<workspace>/.cursor/skills/council` (or `.agents/skills/council`)

Never pass unresolved `~` into Shell or Task prompts. Expand it first.

## Platform (Cursor cloud agents, Linux, macOS)

This skill's paths are written Windows-style (`<run>\file`). On Linux or macOS, including Cursor cloud agents, use forward slashes and the real `~`. If `python` is not found, use `python3` for the scripts. Gemini seats use the shell (api/agy), Grok uses the Cursor CLI, and gpt/claude/composer use Task. Cloud agents have no agy or Cursor CLI: Gemini relies on the API secret `COUNCIL_GEMINI_API_KEY` and Grok is skipped after its failed preflight. Never commit a config containing credentials or a key file. If no Gemini transport works, skip the seat. In a cloud run the skill must be in the repo's `.cursor/skills/council/` (see the README) or synced; run data is created under `~/.council/runs` on that machine and is not kept after the run.

## Config defaults

Load the roster and routes through council_roster.py at Start. Other settings merge local over shared. If a key is missing, use:

| Key | Default |
|-----|---------|
| `show_exchange` | `ask` |
| `runs_dir` | `~/.council/runs`, expanded to an absolute path. Resolved as `config.local.yaml` (gitignored) > `config.yaml` > this default; created if missing. It is outside the skill folder on purpose, so syncing or copying the skill never uploads past council text. A real Cursor run (2026-10-23) read run files outside the workspace without trouble |
| `max_reviewers` | `7` (cap on non-fallback reviewer entries) |
| `run_retention_count` | `10` |
| `run_retention_days` | `14` |
| `routes` | core.md Routing table; per-origin family routes |
| `config_version` | `2`; local `based_on_config_version` records reviewed migrations |
| `gemini_api.unavailable_retry_wait_sec` | `20` (0..120), plus 0..2 s jitter for 503 |
| `gemini_api.unavailable_retries` | `1` (0..3), inside API call |
| `gemini_api.enabled` | `false` |
| `gemini_api.tier` | `free` (`free` or `paid`) |
| `gemini_api.free_tier_ack` | `false`; must be true on free |
| `gemini_api.timeout_sec` | `1200` free / `300` paid; override 30..3600 |
| `gemini_api.thinking_level` | `high` (`low`, `medium`, `high`); independent of seat effort |
| `gemini_api.max_output_review` | `32768` (1024..65536) |
| `gemini_api.max_output_rewrite` | `65536` (1024..65536) |
| `gemini_api.max_model_steps` | `2` (0..5) |
| `gemini_api.key_file` | `~/.council/gemini.key`; env `COUNCIL_GEMINI_API_KEY` overrides |

`max_reviewers`, `run_retention_count` and `run_retention_days` must be positive integers; stop on invalid values. The loader applies the reviewer cap, keeps fallbacks only with their target, and reports truncation.

**Roster and transport:** use the loader's effective seats and dispatch entries (core.md, Routing). Gemini uses api/agy only; Grok uses `cursor_cli` (runner/source `cursor`); gpt/claude/composer use Task. Never route Gemini through Cursor, and never shell out to a separate Grok CLI. API settings are validated by `council_gemini_api.py config`.

**Default roster:** Argon breadth reviewer on api then agy, with a conditional Flash breadth fallback only if Argon remains unparsed after both; GPT Sol reviewer; rewriter chain Flash then Sonnet. Seat 0 and integration are Opus stages, not roster entries.

## Start

1. Do **not** call `rename_chat` unless the user explicitly asked to rename this chat.
2. Run `python -I <shared>/scripts/council_roster.py load --root <shared> --origin cursor`. Use only its `seats` JSON as the roster, never YAML seats directly. Exit 2: stop, show `detail` and any `migrations`, propose a `config.local.yaml` edit and apply only on approval. Exit 3: show each pending entry once (title; each change's key, action and example), then ask one question: **Apply the proposed edits / Mark as reviewed (no edits) / Not now**. On the first two choices apply edits if chosen, then run `python -I <shared>/scripts/council_roster.py stamp --root <shared>`; both editing and stamping require that approval. Not now proceeds with the current config and asks again next run. Exit 0: no migration message. Show loader warnings, truncation and every `skip`/`skip_detail`, including transports dropped by origin filtering. Validate `show_exchange` as ask/always/never and numeric settings per defaults.
3. Resolve models first ([Model resolution](#model-resolution-cursor)), then show the roster (count, labels, order) in one short list, flagging every seat that will be skipped (no matching model on any available transport, same-model skip).
4. Confirm unless the user already decided:
   - `/council proceed` or `proceed` → run
   - `/council configure` or `configure` / `reconfigure` → [Configure](#configure)
   - One-shot override in the same message (`this time only: Grok then Claude`) → use that roster for this run; carry no transports/fallback_for, use the route table, and do not persist without approval
   - A full request after `/council` (topic included) → treat as **Proceed**

   Otherwise try **one** `AskQuestion`: Proceed / Reconfigure then run / Reconfigure only. If `AskQuestion` is missing or fails, print the roster and `Reply proceed / reconfigure / reconfigure only`; do not stall.
5. Exchange visibility: `always` → transcript; `never` → final only; `ask` → ask once, or honor "show exchange" in the message, or default to final-only when ambiguous.

## Same-model skip (Cursor)

Rule and family table: [core.md](core.md#same-model-skip). Seat 0 is the selected chat model. For an artifact the user supplied, use the author's family if known (for "review the plan above" the previous assistant message is the chat model's, so the chat model's family), else `unknown`, which skips no reviewer.

| Seat-0 family | Effective path (default roster) |
|---|---|
| Claude, Grok, Composer, or `unknown` (author not known) | Argon (Flash fallback only if unparsed), then Sol, integrate and rewrite |
| GPT | Argon (Flash fallback only if unparsed), then integrate and rewrite (Sol skipped) |
| Gemini | Sol, then integrate and rewrite (Argon and its Flash fallback skip as same-model reviewers) |

The integration and rewrite stages are **not** reviews, so they are never skipped by this rule (see [core.md](core.md#stages)).

## Dispatch (Cursor)

This is the only logic that differs from Claude Code. For each seat core.md says to dispatch:

**Gemini branch (shell):** walk only the loaded api/agy chain; Task and Cursor CLI are never used for Gemini. The script holds the dispatch logic in both personal and project copies; recipes are optional setup and measurement notes.

Use `scripts/council_seat.py`. Do not write ad-hoc wrapper scripts.
Failures are logged to failures.md by the script.

Before a transport's first seat, run its preflight (use the first resolved model for agy; api needs no model):

```text
python -I <shared>/scripts/council_seat.py preflight --transport agy --root <shared> --run <run> --model <slug>
python -I <shared>/scripts/council_seat.py preflight --transport api --root <shared> --run <run>
```

A failed preflight skips only that transport. Resolve using its source (`api|agy`). Build review prompts with council_prompt.py (role and total included); API always uses `--mode inline`. Translate builder `mode: file` to `--delivery pointer` for agy, otherwise use inline (the default). Dispatch via Shell:

```text
python -I <shared>/scripts/council_seat.py run --transport agy --root <shared> --run <run> --seat <n> --label <label> --model <slug> --prompt-file <run>/seat-<n>-prompt.md --reply <run>/seat-<n>-reply.txt --delivery inline
python -I <shared>/scripts/council_seat.py run --transport api --root <shared> --run <run> --seat <n> --label <label> --model <slug> --prompt-file <run>/seat-<n>-prompt.md --reply <run>/seat-<n>-reply.txt
```

For agy pointer delivery, add repeated `--add-dir <review-root>`; shared root and run are granted automatically. The runner handles empty cwd, safe launchers, closed stdin, whole-tree hard timeout, stdout/stderr/meta and prompt/key redaction. Read its one-line JSON summary; exit 0 means parse the reply, 1 is seat failure, 2 is usage/IO/refusal. Surface `free_tier_warning` once per run. Apply core.md's two-dispatch rule and API failure table, model exclusions, cooldowns, call cap and full timeout. The runner never retries or resolves a model. Use Shell's background completion facility when API `outer_timeout_sec` from `council_gemini_api.py config` exceeds 540 s; budget at least that outer timeout (CLI timeout plus 120 s for CLI calls); do not poll or terminate early.

For rewrite chunks use the selected transport command with `--prompt-file <run>/rewrite/chunk-<i>-prompt.md --reply <run>/rewrite/chunk-<i>-reply.txt --kind rewrite --stem rewrite/chunk-<i>`; the runner appends the tail (and saves the API prompt). Do not pass seat effort to API. Freeze model/transport after chunk 1 succeeds; a later seat failure restarts prepare on the next transport/model. With no transport left, skip the seat. Record all orchestrator skips via:

```text
python -I <shared>/scripts/council_seat.py skip --run <run> --seat <n> --label <label> --reason no-transport --detail <reason>
```

Use the actual skip reason, such as same-model or rejected-family. Do not duplicate runner or parser failure lines.

**Grok branch (shell):** loaded transport `cursor_cli` maps to runner `--transport cursor` and resolver `--source cursor`. Before its first seat, preflight with the resolved Grok slug, then run:

```text
python -I <shared>/scripts/council_seat.py preflight --transport cursor --root <shared> --run <run> --model <slug>
python -I <shared>/scripts/council_seat.py run --transport cursor --root <shared> --run <run> --seat <n> --label <label> --model <slug> --prompt-file <run>/seat-<n>-prompt.md --reply <run>/seat-<n>-reply.txt --delivery inline
```

Use inline or pointer delivery from the builder. This spends Cursor quota and is never a backup for another family. Missing CLI skips Grok. Apply core.md's normal parser and failure rules.

### Fallback reviewer dispatch

Follow core.md, Fallback seats: only after the primary has exhausted every transport, model step, retry and `--final-attempt`, run `python -I <shared>/scripts/council_parse.py seat-state --run <run> --seat <n>`. `parsed: true` means no fallback and no log. If false, log `council_seat.py skip --run <run> --seat <n> --label <fallback-label> --reason fallback-triggered --detail "<primary label>: <last failure class or skip reason>"`, then apply the fallback's own same-model check and transport chain. Rebuild `seat-<n>-prompt.md`, reuse n, gaps G<n>-k and the original `--total`; keep integration numbering unchanged. Pass runner `--stem seat-<n>-fb`; save reply as `seat-<n>-fb-reply.txt`. If the fallback also ends unparsed, log `fallback-failed` via `council_seat.py skip`. A parsed fallback counts toward the minimum external review. The same-model gate can skip the fallback too.

The numbered Task steps below apply only to gpt/claude/composer seats. For a Task fallback use its fallback reply path in every step.

1. **Resolve the model** per [Model resolution](#model-resolution-cursor). If it returns `skip`, skip the seat (`rejected-family`).
2. **Prompt:** write `seat-<n>-prompt.md` as core.md's Cascade loop step 3 says (fields, packet paths, the inlined `reviewer-envelope.md`, and that the seat is gaps only). The Task prompt itself is a short pointer: read that file, then write the envelope to `<run>\seat-<n>-reply.txt`. Do **not** paste `artifact.md` or skill file bodies into the Task prompt.
3. **Call:** `Task` with `subagent_type: generalPurpose`, `model` = the resolved slug, `run_in_background: false`.
4. **Read-only:** tell the seat not to Write, Edit or Delete anything in the workspace, with one exception: it writes its envelope to `<run>\seat-<n>-reply.txt` and replies with a short acknowledgement only. If the file is missing afterwards (the Task returned the envelope as text), save the returned text to that file yourself. **Unverified in Cursor:** whether Task seats can write there and keep full replies out of the conversation; if they cannot, the context saving applies to Claude Code only.
5. **Parse** with `scripts/council_parse.py` ([core.md](core.md#envelope-parser)). Retry = a **new** Task (do not assume resume), also read-only.
6. The raw reply is `seat-<n>-reply.txt` (Cursor writes no separate `seat-<n>-<slug>.md`). Record the slug actually passed to Task in `meta.md` under `dispatched:`.
7. **Failures:** Task has no stderr or argv, so Task seats do not use `seat-<n>-argv-meta.txt` or `cli-preflight.txt` (API seats do). If a Task call errors, save the error text to `<run>\seat-<n>-error.txt` and run `council_resolve.py classify --file` on it. If the error is about the `model` value (not found / not available), treat it as `model-rejected` even when classify says `other`, then follow core.md's rejection flow once. (Gemini failures follow the transport chain above.)

**Task seats and stalls:** the orchestrator cannot poll inside a blocking `Task`. Do **not** peek at `subagents\*.jsonl` mid-flight and call that a stall. If a Task hangs, the user interrupt is the escape. State this if a seat is taking unusually long.

**Rewrite stage:** the `rewriter` seats are an ordered fallback chain (core.md, Stages step 4): resolve them in order using api/agy for Gemini, cursor_cli for Grok, or Task for gpt/claude/composer. Use the first available model/transport that works, with core.md's restart and call-budget rules. The following details apply to Task dispatches. Gemini rewriters follow the Gemini chain through the shell, so Flash is available here again (the Task list has none, [catalog.md](catalog.md)); a Claude rewriter (Sonnet) is a Task seat. For a Task rewriter, dispatch each chunk as a Task seat on the model: it writes its output (the rewritten chunk followed by the final `REWRITE_END` line) to `<run>\rewrite\chunk-<i>-reply.txt` and acknowledges; if the file is missing, save the returned text there; then `council_rewrite.py check --reply` that file. If nothing in the chain resolves, skip the rewrite, say why in the Return line, and return the integrated artifact.

**Integration stage:** dispatch as a Task seat with family `claude`, line `opus` (resolved like any seat), or run it yourself when the chat model is already Opus (then write your own envelope to `<run>\seat-<n>-reply.txt` and parse it like any seat with `--role integrator`, using the next seat number after the last reviewer). If the chat model is not Opus and no Opus Task model is available, warn the user that the draft and integration are not Opus-grade.

### Model resolution (Cursor)

Gemini resolves with api/agy only; Grok resolves with `--source cursor` from its Cursor CLI listing. For Task seats (gpt/claude/composer), take the model slugs the tool offers (drop `inherit`), one per line, and write them to `<runs_dir>\task-models.txt` (create `runs_dir` if missing). Do **not** use `cursor-agent --list-models`: it lists far more models than Task accepts. Resolve Task seats per [core.md](core.md#model-resolution) with `--source file:<runs_dir>\task-models.txt` (state file `<runs_dir>\_resolved.json`). The script picks the newest version of the seat's family and line and never crosses lines. On `skip` (`no-line-match` or `no-catalog`), try the next configured transport, then skip the seat (`rejected-family`) when none remains and say why. If a Task call is rejected, follow core.md's rejection flow once.

## Return (Cursor)

Shared: [core.md](core.md#return). Cursor exchange handling: if exchange was requested, write `<run>/exchange.md` with the transcript and set `<runs_dir>/last-exchange.md` to a single line with its absolute path (or run id). Secret-hygiene applies to both.

## Configure

`/council configure`, `configure` and `reconfigure` open this wizard. Put ALL changes in `config.local.yaml`; never edit `config.yaml` or `CHANGELOG.md`. Updates never overwrite or upload local config. When shipped `config_version` exceeds local `based_on_config_version` (missing means 1), show upgrade notes at Start and ask before editing or stamping; see Start's Apply / Mark as reviewed / Not now choices.

Explain first: Gemini is NEVER routed through Cursor or Task, in any run. Grok uses the Cursor CLI (`cursor_cli`, runner/source `cursor`). Argon works out of the box for anyone with agy; API access is optional. The seeded roster is Argon breadth on api then agy, conditional Flash breadth fallback only after all Argon attempts fail, GPT Sol, then the Flash/Sonnet rewriter chain. With the API disabled its preflight logs `api-refused` once and Argon uses agy. **Free-tier API prompts/replies may be used for product improvement and human review; never send PHI or confidential text.** The wizard never enables the API or changes its acknowledgment.

1. Ask for a start point: shipped default, a preset from `<shared>/presets/*.yaml` when available, or the current local roster (from `load`). Presets: `api-only`, `claude-code-full` (adds Grok), `cursor-originated` (adds Claude Opus); project installs do not include them, so offer shipped/current there. API-only requires separately configured API access. A local `seats:` replaces the entire shared list.
2. Ask for the number of primary review seats (minimum 1; warn above `max_reviewers`, offer to raise it or reduce seats). Fallbacks and rewriters do not add review slots. For each seat ask family, line, effort, role and label; default the first Gemini reviewer to breadth-auditor. Preserve an existing breadth role unless explicitly removed. Show `python -I <shared>/scripts/council_resolve.py list --source <source> --family <family>` for its first transport when reachable (api/agy/codex/cursor; Task uses catalog.md; Agent has no CLI catalog). Resolve versions within the chosen line only.
3. Show where each seat is called from for this origin (`cursor`), then offer keep / single transport / custom order. Accept only the core.md Routing matrix: Gemini api/agy in both; Grok cursor_cli in both; GPT codex in default, task in cursor; Claude agent in default, task in cursor; Composer none in default, task in cursor. Configure `routes` per origin/family or seat `transports` as an explicit replacement; show filtering and every no-transport skip in the other origin. Gemini never uses Cursor/Task, even as backup.
4. For each reviewer ask whether to add a fallback. Give primary and fallback unique labels; place the fallback immediately after its primary, set `fallback_for` to the exact primary label and inherit its role. It runs only if `seat-state` is still unparsed after every primary transport, retry and final attempt. Ask for rewriter chain order separately; rewriters have no `fallback_for`.
5. Ask `show_exchange` (ask/always/never) and whether to save or use only this run. A one-run override follows core.md Runtime override and carries neither transports nor fallback_for. For saving, write `<runs_dir>/configure-candidate.yaml` containing only selected `seats`, `routes`, `max_reviewers`, `show_exchange` (optional `based_on_config_version` metadata). No gemini_api, gemini_transport, key_file, credentials or unrelated settings; retain API settings in the existing local file.
6. Run `python -I <shared>/scripts/council_roster.py validate --root <shared> --local <runs_dir>/configure-candidate.yaml`. Show its resulting rosters for BOTH default and cursor origins, including fallback slots, rewriter order, truncation and skipped transports. Explain that omitted candidate sections keep existing local values when saved; include current effective values for any of these four sections needed for an accurate preview. Fix validation errors before seeking approval.
7. Show the exact proposed change and ask approval. Only on approval run `python -I <shared>/scripts/council_roster.py write-local --root <shared> --candidate <runs_dir>/configure-candidate.yaml`. It validates the merged local text in both origins, preserves unrelated lines (including gemini_api) verbatim, backs up the old file as `config.prev.local.yaml`, writes atomically and stamps the shipped config version. On failure show its safe detail and stop; on success reload and show the saved roster. Never write the shipped config or changelog. Run the cascade unless the user chose configure-only.

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

**Configure**

```
/council configure
```

Example: start from cursor-originated, keep Argon with its conditional Flash fallback, GPT Sol and Claude Opus, and choose `show_exchange: ask`. Validate the candidate, preview both origins, then save with `write-local` only after approval. Run unless the user chose configure-only.
