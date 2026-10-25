# Changelog

Notable changes to Council's shared config and skill files (`config.yaml`,
`models.md`, `SKILL.md`, `claude/SKILL.md`, `README.md`). Format loosely
follows [Keep a Changelog](https://keepachangelog.com/) — dated entries,
reverse-chronological, no version numbers (this repo has no install/release
step to version against; `git log` is the real source of truth if you need
more detail than an entry gives you).

**Maintaining this file:** every merged PR that changes `config.yaml` or the
skill files' documented behavior gets an entry here, added as part of that
PR (not backfilled later). One entry per PR, dated by merge date, using
`Added` / `Changed` / `Fixed` / `Removed` sections as needed. Skip trivial
wording fixes that don't change behavior.

## 2026-10-25 - Routing v2

### Breaking

- Review [config.migrations.yaml](config.migrations.yaml), version 2, before upgrading an existing local config. Gemini through Cursor/Task is removed everywhere; Grok uses `cursor_cli`, and a local `seats:` now replaces the entire shared list. Legacy `gemini_transport` accepts only api/agy and is deprecated in favor of per-origin routes.
- Put ALL customizations in `config.local.yaml`, never `config.yaml`. Updates never overwrite or upload local config. Start shows pending migration notes and asks approval before changing anything or stamping `based_on_config_version`.

### Added

- Per-origin routes, seat `transports` overrides and conditional `fallback_for` reviewers; `council_roster.py` load/validate/check/migrations/stamp/write-local and parser `seat-state`.
- `config_version` / `based_on_config_version` and versioned migration notes; local writer validates both origins, preserves API settings verbatim, backs up the previous local file and saves atomically.
- API 503 same-model retry (20 s plus jitter, once by default), configurable retry limits and computed outer timeout; 429 advances without waiting.
- Configure wizard in both harnesses with validation, approval and local-only persistence; api-only, claude-code-full and cursor-originated presets (not installed into project copies).

### Changed

- Gemini defaults to api then agy in both origins. Argon works out of the box with agy; the API is optional and disabled by default. Free-tier privacy acknowledgment remains required.
- Default roster: Argon breadth, conditional Flash breadth fallback after all Argon routes fail, GPT Sol, then Flash/Sonnet rewriters. A fallback reuses its primary review slot and gap IDs.
- Grok runs through the Cursor CLI; GPT/Claude use the allowed transports for the run origin. Installer includes the roster script and migration notes, never local config, presets, recipes or keys.

### Removed

- Gemini via Cursor, the separate Grok CLI route and `recipes/grok.md`.

## 2026-10-25 — Default rewriter chain is Flash, then Sonnet

### Changed

- `config.yaml` no longer lists Gemini Argon as a rewriter. Rewriting is mechanical; Argon is reserved for the breadth review. The chain is Gemini Flash (low effort), then Claude Sonnet. README, `core.md` and both `SKILL.md` files updated to match.

## 2026-10-25 - Cross-platform seat runner

### Added

- Standard-library Python 3.10+ `council_seat.py`: one dispatch, preflight or logged skip for agy, Cursor, Codex, Grok and the Gemini API, on Windows/macOS/Linux. Safe launcher resolution, closed stdin, file-backed output, whole-tree timeouts, empty cwd, prompt hashes, key redaction and atomic success-only replies.
- Offline fake-CLI tests for transport results, argv, launchers, timeouts, redaction and failure logging; the installer includes the new runtime script.

### Changed

- Both harnesses and all recipes use the runner. Failures are logged exactly once by code; the harness still owns retries, model resolution and API fallback. Project copies need no recipes.

### Removed

- Windows-only Invoke-Seat / Stop-Tree helpers and hand-built process-launch recipes.

## 2026-10-25 — Seat runner follow-ups

### Fixed

- `council_seat.py` no longer loses a seat's own summary when `failures.md` is locked (it reports `failures_log_error` instead), and failure tokens no longer repeat the transport (`cursor-hook-bug`, not `cursor-cursor-hook-bug`).
- The Cursor hook bug was reproduced (`MSYSTEM` plus a tool call) and confirmed absent without `MSYSTEM`; the runner's `MSYSTEM` strip is kept as belt and braces, since council seats make no tool calls.

## 2026-10-24 — Cursor routes Gemini through the shell

### Changed

- In Cursor, Gemini seats no longer use the Task tool. They walk `gemini_transport` through the shell exactly like Claude Code (`agy`, the API helper, `cursor-agent`); Task stays for the other families. Consequences: Gemini Flash is available in Cursor again (via `agy` or the API), `agy` is no longer silently skipped, and a Cursor cloud agent (which has no `agy` or `cursor-agent`) relies on the API helper and skips Gemini seats if it is off. `core.md`, `SKILL.md` and the README were updated to match; nothing changes for Claude Code.

## 2026-10-24 — Gemini API transport (opt-in)

### Added

- Standard-library Python Gemini Developer API helper: config, preflight, paginated models and atomic review/rewrite calls. Keys come from an environment secret or local file, only enter the request header, and are redacted from errors and output. POSIX file permissions and an explicit free-tier privacy acknowledgment are enforced.
- Independent Gemini transport order and API tier settings, with strict per-key local overrides. Default `[agy, cursor]` keeps the API disabled. README setup instructions and `recipes/api.md` document tiers, privacy, long calls and failures.
- Offline fake-server tests for calls, key handling, config validation, failures and pagination; resolver and installer regressions. No live API calls were made for this implementation.

### Changed

- Gemini seats in both harnesses follow the configured chain. API quota/rejection/503 may step down within the same line, with a bounded call budget; timeouts never lower a model. Rewrite retries restart the whole stage when changing transport/model.
- Resolver accepts `--source api`, invocation-only `--exclude` and per-entry cooldown `--days`; old cooldown entries still default to seven days. Installer copies five runtime scripts and excludes keys and local configs through its allowlist.

## 2026-10-23 — Unify the Cursor and Claude Code skills; gaps-only reviewers, Opus integration, rewriter chain

### Changed

- **One set of rules.** The shared cascade logic moved into `core.md`; `SKILL.md` (Cursor) and `claude/SKILL.md` now hold only what differs: root resolution, Start, and how a seat is called.
- **One roster.** `reviewers` and `cursor_reviewers` are replaced by a single `seats:` list (family, line, effort, role, label, pin). Default: Gemini Argon (breadth-auditor) → GPT Sol → Gemini Flash (rewriter). Opus drafts and integrates as stages, not seats. A pre-change roster is in git history (commit `5ccf6c1`).
- **Reviewers are gaps-only.** A reviewer's Revised must be `UNCHANGED`; one Opus integration pass resolves every gap and prints each rejection with its reason at the end.
- **Strict `role` validation** in both harnesses (previously Cursor was strict and Claude Code ignored typos).
- **Models are resolved, not pinned.** A seat stores a family and line; the newest version of that line is picked from the live catalog each run, never crossing lines. A model the server rejects is skipped for 7 days; the last working model is the fallback when a catalog is unreachable.
- Run data now lives in `~/.council/runs`, outside the skill folder (override with `runs_dir` in `config.local.yaml`), so syncing or copying the skill to a cloud agent never uploads past council text. The old `claude/runs/` and `runs/` folders were moved there.
- `models.md` is split: `catalog.md` for Cursor, `recipes/` for each Claude Code CLI. A default run now loads about 42 KB in Claude Code (was about 61 KB) and about 35 KB in Cursor (was about 79 KB).
- The Grok seat is called through the Grok CLI; the old Cursor `agent` recipe no longer applies.

### Added

- **Token efficiency for Gemini (`agy`), measured.** A call carries about 14-15k tokens of fixed agent overhead and a file read is a second call, so reviewer prompts are now built by `scripts/council_prompt.py` and sent **inline** (everything in the prompt, one call, no tools) whenever they fit on a command line, with automatic fallback to the old file-read form. Inline cut input by 19-25% on the breadth review; running `agy` from an empty folder saves about 1.2k more tokens and halves startup; low effort removed all thinking tokens and halved the time with the same coverage in two trials (the breadth seat stays at medium until more samples). A direct Gemini API script, `thinkingBudget` in `settings.json` and context caching were evaluated and not adopted (see `recipes/agy.md`).
- **Cursor CLI fallback lane for Gemini seats.** When `agy` is out of quota or unavailable, a `gemini` seat is retried through `cursor-agent` (same model line, Cursor's quota; `scripts/council_resolve.py --source cursor`, `recipes/cursor.md`). Tested live: Argon medium review (5/5 planted flaws) and Flash 3.8 low rewrite (all checks passed). Automating the Gemini web chat was evaluated and declined: its robots.txt disallows `/app/` and `/chat/`, Google's terms prohibit automated access against machine-readable rules and bypassing protective measures, and the account at risk is the one behind the subscription (see `recipes/agy.md`).
- **Rewriter fallback chain.** Several `rewriter` seats are tried in order (Gemini Argon, Gemini Flash, Claude Sonnet, all newest of their line); the first that resolves and works does the whole rewrite. Needed because the Cursor Task list has no Gemini Flash. Rewrite chunks are sent inline in the prompt at low effort: measured on `agy`, a file read doubles input tokens (34k against 17.7k) and one medium-effort call spent 13.9k thinking tokens. `agy` calls use `--output-format json`, which also reports token usage.
- `scripts/council_parse.py`: parses replies, mints gap IDs, applies STATUS, restores dropped dissent, and renders the packet files from `gaps.json`. The orchestrator reads a one-line summary instead of the full reply.
- `scripts/council_resolve.py`: newest-version-of-the-same-line resolver, rejected-model cooldown, last-good fallback, failure classifier.
- `scripts/council_rewrite.py`: masks protected text, rewrites in small chunks, checks every chunk, falls back per chunk or for the whole artifact, and reports every explanation added.
- `scripts/council_install.py`: copies the Cursor runtime files into a project's `.cursor/skills/council/` so Cursor cloud agents can use the skill (they cannot see `~/.cursor/skills`). Never overwrites a file edited in the copy unless `--force`; `check` reports drift. README gained a "Cursor cloud agents" section (sync route and install route) and the Cursor `SKILL.md` a Platform note for Linux and macOS. A local `cursor-agent` found the installed copy as a project skill; a real cloud run is untested.
- `partitioning.md` (oversize inputs), `config.local.example.yaml`, a minimum-review rule, and tests for all three scripts.

### Removed

- The churn guard, terminal-calm skip and breadth-auditor content-loss guard (with their config keys): with gaps-only reviewers there is nothing left for them to guard.
- The legacy `## Gaps` reply fallback.
- `claude/dispatch.md` (its mapping moved into `claude/SKILL.md`, its Grok recipe into `recipes/grok.md`).

### Fixed

- The reply parser mistook a reviewer's quoted marker (for example `<<<COUNCIL_REVISED>>>` inside a gap about the protocol) for the real one and rejected a good envelope as "sentinels out of order". It now looks for markers in order and prefers ones alone on their line. Found by the inline-mode trial; it would have hit any council that reviews this skill.
- Codex seats failed with "blocked by policy" because `--ignore-user-config` dropped the Windows sandbox setting; the recipe now passes `-c windows.sandbox="unelevated"`.
- Three independent Codex reviews of the new scripts found and drove fixes for 13 blocking defects (state races, a model-rejection cache poisoned by auth errors, dated slugs outranking newer versions, placeholders colliding with document text, indented code left editable, version and sign changes going undetected, and others); the Claude Code path was then verified with a full live run.

## 2026-09-13 — Interleave Gemini into every other seat; redefine churn-guard ([#3](https://github.com/mxzerow/council/pull/3), supersedes closed [#2](https://github.com/mxzerow/council/pull/2))

### Changed

- Default roster: 4 seats → 7. Gemini/agy (cheap) now runs every other seat instead of once: `Gemini (breadth-auditor) → Claude → Gemini → Codex CLI (GPT) → Gemini → Grok → Gemini`. Only the first Gemini seat keeps `role: breadth-auditor`; the three interleaved passes are ordinary critical reviewers.
- `max_reviewers`: 5 → 7, including the Config-defaults fallback value in `SKILL.md` (so a missing key in `config.yaml` can't silently truncate the roster back down).
- Churn-guard redefined from **kind-based** to **cost-tier-based**: `calm_streak` now counts calm **non-Gemini** seats (Claude, Codex, Grok) instead of `kind: cursor` specifically. Gemini is excluded entirely either direction — never counted, never skipped. Codex loses its old blanket "CLI is never churn-skipped" protection and now counts toward the streak like Claude/Grok — though at the default `churn_guard: 2` its position ahead of Grok means it's never itself the seat that gets skipped (that stops holding at `churn_guard: 1`).

### Fixed

Six documentation bugs found by dogfooding the new 7-seat roster against this diff before merging (each independently confirmed by 3–4 of 6 active review seats):

- Effective-paths table (Composer/unknown row) still said "two calm Cursor seats" — now "two calm non-Gemini seats".
- "`terminal_calm_skip` is permanently inert, no reading under which it still works" was overstated — false for Gemini-family seat-0 runs, where same-model-skip removes all four Gemini seats and the effective path (`Claude → Codex → Grok`) ends on a trailing Cursor seat again. Scoped the claim to non-Gemini seat-0 runs.
- "Codex can never be skipped on this roster" was stated as universal; it only holds at `churn_guard: 2` (the default) — qualified in all four places it appeared.
- Two dangling references to "the churn-guard ambiguity above" in the Terminal calm skip section, left over from before the ambiguity was resolved via redesign.
- Terminal calm skip still said CLI calm "must not increment Cursor `calm_streak`" — stale; Codex (CLI) does increment it now, only Gemini is excluded.
- Churn-guard's calm streak can bridge across a substantive Gemini revision, since Gemini is a no-op either direction — documented as intentional (churn-guard is a pure expenditure cap on paid seats, not an artifact-convergence signal), not left as a silent gap.

## 2026-09-10 — Bump default Gemini seat to 3.8 Flash High ([#1](https://github.com/mxzerow/council/pull/1))

### Changed

- `agy`/Gemini breadth-auditor seat: `gemini-3.7-flash-high` → `gemini-3.8-flash-high` in `config.yaml` and `models.md` (Google shipped 3.8 Flash 2026-09-02; same pricing, ahead of 3.7 on published benchmarks). Verified live via a single-seat Council dispatch before merging — the model self-reported its own identity in the response.
- The `gemini-3.1-pro-high` hard-review pin is unchanged (different model tier).

## Earlier history (pre-dates this file — summarized from `git log`, not itemized per-PR)

- **2026-09-09** — Scoped reviewer reads to exactly the packet paths listed in each seat's prompt (no more incidental file access); wired up the native `agent` (Cursor) CLI preflight and fixed a missing `--add-dir` gap that caused it to fail; swapped the Claude Cursor reviewer to Sonnet 5 and fixed an `agy` `read_file` permission-gating issue.
- **2026-08-29** — Council published as an installable agent skill (initial commit).
