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

## 2026-10-23 — Unify the Cursor and Claude Code skills; gaps-only reviewers, Opus integration, rewriter chain

### Changed

- **One set of rules.** The shared cascade logic moved into `core.md`; `SKILL.md` (Cursor) and `claude/SKILL.md` now hold only what differs: root resolution, Start, and how a seat is called.
- **One roster.** `reviewers` and `cursor_reviewers` are replaced by a single `seats:` list (family, line, effort, role, label, pin). Default: Gemini Argon (breadth-auditor) → GPT Sol → Gemini Flash (rewriter). Opus drafts and integrates as stages, not seats. A pre-change roster is in git history (commit `5ccf6c1`).
- **Reviewers are gaps-only.** A reviewer's Revised must be `UNCHANGED`; one Opus integration pass resolves every gap and prints each rejection with its reason at the end.
- **Strict `role` validation** in both harnesses (previously Cursor was strict and Claude Code ignored typos).
- **Models are resolved, not pinned.** A seat stores a family and line; the newest version of that line is picked from the live catalog each run, never crossing lines. A model the server rejects is skipped for 7 days; the last working model is the fallback when a catalog is unreachable.
- Run data now lives under `runs/` next to `config.yaml` (override with `runs_dir` in `config.local.yaml`). The old `claude/runs/` (59 runs) was moved there.
- `models.md` is split: `catalog.md` for Cursor, `recipes/` for each Claude Code CLI. A default run now loads about 42 KB in Claude Code (was about 61 KB) and about 35 KB in Cursor (was about 79 KB).
- The Grok seat is called through the Grok CLI; the old Cursor `agent` recipe no longer applies.

### Added

- **Rewriter fallback chain.** Several `rewriter` seats are tried in order (Gemini Argon, Gemini Flash, Claude Sonnet, all newest of their line); the first that resolves and works does the whole rewrite. Needed because the Cursor Task list has no Gemini Flash. Rewrite chunks are sent inline in the prompt at low effort: measured on `agy`, a file read doubles input tokens (34k against 17.7k) and one medium-effort call spent 13.9k thinking tokens. `agy` calls use `--output-format json`, which also reports token usage.
- `scripts/council_parse.py`: parses replies, mints gap IDs, applies STATUS, restores dropped dissent, and renders the packet files from `gaps.json`. The orchestrator reads a one-line summary instead of the full reply.
- `scripts/council_resolve.py`: newest-version-of-the-same-line resolver, rejected-model cooldown, last-good fallback, failure classifier.
- `scripts/council_rewrite.py`: masks protected text, rewrites in small chunks, checks every chunk, falls back per chunk or for the whole artifact, and reports every explanation added.
- `partitioning.md` (oversize inputs), `config.local.example.yaml`, a minimum-review rule, and tests for all three scripts.

### Removed

- The churn guard, terminal-calm skip and breadth-auditor content-loss guard (with their config keys): with gaps-only reviewers there is nothing left for them to guard.
- The legacy `## Gaps` reply fallback.
- `claude/dispatch.md` (its mapping moved into `claude/SKILL.md`, its Grok recipe into `recipes/grok.md`).

### Fixed

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
