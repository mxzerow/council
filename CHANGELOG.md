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
