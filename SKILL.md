---
name: council
description: >-
  Runs a sequential multi-model review cascade. The current chat model writes
  the first answer, then configurable reviewers (Cursor Task models and optional
  local Gemini CLI) each review gaps and incorporate changes in order. Use when
  the user invokes /council or /Council, or clearly asks for a council pass,
  council review, wide review, or multi-model review as the task itself. Do not
  use when those phrases are quoted, negated (e.g. "don't do a wide review"),
  incidental, or the work is trivia / a one-liner / needs an answer in under a
  minute.
---

# Council

You are the orchestrator. Produce the initial answer yourself (unless the user supplied an existing artifact). Route reviewers sequentially. Do not rewrite the last reviewer's artifact unless that seat failed.

Council is a **workflow**, not a Cursor mode. Do **not** `SwitchMode`.

Do not switch to parallel first-answers, anonymized peer review, or a chairman synthesis pass.

**Shared logic:** read [core.md](core.md) first (this directory). This file keeps only what is Cursor-specific: Config defaults, Start, Task-seat dispatch, Family slug resolve, and the Cursor-only guards listed in core.md's divergence ledger.

## When not to use

Shared: see [core.md](core.md#when-not-to-use).

## Paths

Skill root is the directory that contains this `SKILL.md`. Resolve it to an **absolute native path** before any filesystem or shell work.

Typical global install:

- Windows: `$env:USERPROFILE\.cursor\skills\council`
- macOS / Linux: `$HOME/.cursor/skills/council`

Project install: `<workspace>/.cursor/skills/council` (or `.agents/skills/council` if that is where the skill was added).

Never pass unresolved `~` into Shell or Task prompts. Expand it first.

## Config defaults

Read from [config.yaml](config.yaml). If a key is missing, use:

| Key | Default |
|-----|---------|
| `show_exchange` | `ask` |
| `runs_dir` | `<skill-root>\runs` — resolved as: `config.local.yaml` (gitignored, machine-specific) > `config.yaml` > this default. Created if missing. Cursor Task seats must be able to read it; keep it inside the workspace/skill tree until that is verified |
| `max_reviewers` | `7` (cap on the number of `seats`) |
| `churn_guard` | `2` |
| `terminal_calm_skip` | `false` |
| `cli_timeout_sec` | `180` |
| `run_retention_count` | `10` |
| `run_retention_days` | `14` |
| `stall_checkin_sec` | `60` |

**Numeric validation (Start step 2):** `max_reviewers`, `cli_timeout_sec`, `run_retention_count`, `run_retention_days`, and `stall_checkin_sec` must be positive integers (`>= 1`). `churn_guard` must be an integer `>= 0` (`0` disables churn-stop **and** terminal-calm skip). `terminal_calm_skip` must be `true` or `false` (default `false`). On invalid values, stop and ask the user to fix or reconfigure.

If `seats` is longer than `max_reviewers`, warn, use the first N seats, continue unless the user insists on the full list.

**Roster:** this orchestrator reads `seats:` (schema, strict `role` values, and migration from the old `reviewers` / `cursor_reviewers` keys: [core.md](core.md#roster-seats)). **Transport:** every seat runs through Task, whatever its family. Never shell out to `agy`, `gemini`, `codex`, `grok` or `agent` from Cursor. Reconfigure writes `seats:`.

Optional per-seat keys: `role` (`reviewer` | `breadth-auditor` | `rewriter`; a `rewriter` seat is skipped as `rewriter-not-implemented` until step 7), `label`, `pin` (exact Task slug), `effort`, and `validation_critical: true` (churn-stop must not skip this seat; removed with the churn guard in step 5).

**Default roster:** Gemini Argon (`breadth-auditor`) → Claude Opus → GPT Sol → Gemini Argon (final pass). In Cursor all four run unless Same-model skip removes the seat matching seat 0's family.

## Start

1. Do **not** call `rename_chat` unless the user explicitly asked to rename this chat. Skipping is not a blocker.
2. Read [config.yaml](config.yaml). Validate: `seats` is a non-empty list (if the old `reviewers` / `cursor_reviewers` keys are present and `seats` is not, stop and point to the migration rule in [core.md](core.md#roster-seats)); each seat has a known `family` and a `line`; `role` is a documented value (`reviewer`, `breadth-auditor`, `rewriter`) — an unrecognized `role` is invalid; `show_exchange` is `ask` | `always` | `never`; `terminal_calm_skip` is `true` or `false` when present (default `false`); numeric keys per [Config defaults](#config-defaults). On invalid config, stop and ask the user to fix or reconfigure.
3. Show the roster (count, labels, order) in one short list. Apply `max_reviewers` as above.
4. Confirm unless the user already decided:

   - `/Council proceed` or `proceed` → run
   - `/Council configure` or `configure` / `reconfigure` → reconfigure flow
   - One-shot override in the same message (`this time only: Grok then Claude`) → use that roster for this run; do not write `config.yaml` unless they say to save
   - A full request after `/Council` (topic included) → treat as **Proceed**

   Otherwise try **one** `AskQuestion`: Proceed / Reconfigure then run / Reconfigure only. If exchange is unknown and `show_exchange: ask`, include Yes/No for the transcript when the tool allows a second question in the same turn.

   **If `AskQuestion` is missing or fails:** print the roster and `Reply proceed / reconfigure / reconfigure only`. Do not stall. If they already sent a full `/Council` request, treat as Proceed.

5. Exchange visibility: `always` → transcript; `never` → final only; `ask` → as above, or honor "show exchange" in the message, or default to final-only when ambiguous.

6. If they chose reconfigure, follow [Reconfigure](#reconfigure) first.

### Artifact selection

Shared: see [core.md](core.md#artifact-selection).

### Size check and partitioning

Shared: see [core.md](core.md#size-check-and-partitioning).

## Produce the draft (seat 0)

Shared: see [core.md](core.md#produce-the-draft-seat-0). Cursor: seat 0 is the selected chat model; never `Task` `inherit` for seat 0.

## Run folder

Shared: see [core.md](core.md#run-folder). Cursor run folders live under `runs_dir` ([Config defaults](#config-defaults)).

## Same-model skip

Shared rule and family table: see [core.md](core.md#same-model-skip).

**Effective paths** (default `seats:` roster Gemini Argon → Claude Opus → GPT Sol → Gemini Argon; same-model-skip only. This does not cover churn-stop, which depends on runtime calm):

| Seat-0 family | Effective reviewer path |
|---|---|
| Claude | Argon → Sol → Argon (Opus skipped) |
| GPT / Codex | Argon → Opus → Argon (Sol skipped) |
| Gemini | Opus → Sol (both Argon seats skipped) |
| Grok, Composer or unknown | Argon → Opus → Sol → Argon (nothing skipped) |

The Gemini `role: breadth-auditor` seat and the GPT seat do **not** bypass same-model skip.

## Cascade

Read [reviewer-envelope.md](reviewer-envelope.md) and [reviewer-prompt.md](reviewer-prompt.md) before dispatching. Seats run **one after another**. Do not launch reviewers in parallel.

Allowed Cursor slugs: read [models.md](models.md) (single catalog). Catalog slugs are **preferred**. Dispatch uses [Family slug resolve](#family-slug-resolve) against this session’s Task `model` enum. **Do not reorder** the `models.md` catalog table to mirror roster order — that table is the family-remap tie-break.

Maintain `calm_streak = 0` and `terminal_calm_streak = 0` and remember the pre-seat artifact text for each seat. Each seat gets **at most two dispatches** total (initial + one retry for any reason: parse failure, empty Revised, patch list, illegal `UNCHANGED`, or content-loss). Whichever failure comes first consumes the retry.

For each reviewer in roster order:

1. If churn-stop already triggered, skip this seat when its resolved family is **not** `gemini` (redefined 2026-09-12 — was `kind: cursor`; Codex now counts) **unless** `validation_critical: true` or `request.md` names that seat/label for validation/observation. Never skip a remaining Gemini seat after churn-stop. See [Churn guard](#churn-guard). Apply [Terminal calm skip](#terminal-calm-skip) when `terminal_calm_skip: true` before dispatching a trailing Cursor seat (note: terminal-calm-skip's own trigger condition is unchanged by this redesign and is currently unreachable on this roster — see that section).
2. Apply [Same-model skip](#same-model-skip).
3. **Seats:** resolve the seat (`family`, `line`, `effort`, or `pin`) with [Family slug resolve](#family-slug-resolve). If no family member is on the Task enum, skip (`rejected-family`). Do not skip only because an exact slug is missing.
4. Refresh `artifact.md`, `gaps.md`, `open-gaps.md`, `verdicts.md`, `unresolved.md`. Snapshot pre-seat artifact for churn compare. Derive **mandatory STATUS IDs** = blocking IDs whose latest `verdicts.md` entry is not `addressed`. List those IDs as a compact comma-separated header. Do not require STATUS for closed `addressed` IDs (inherit unless the seat reopens them).
5. Dispatch Cursor or CLI seat (below).
6. Include in the Task/CLI prompt: **seat index**, **label**, **total seats**, **run mode**, **seat kind**, **resolved reviewer family**, **configured role** (if any), **open blocking IDs** (comma-separated; not full gap prose), absolute paths to packet files (never `meta.md`; never placeholder-only files; ordinary seats: inlined envelope, not a Read of `reviewer-prompt.md`), and for implementation mode the **allowed proposal/application roots**. Do **not** paste `artifact.md` or skill file bodies into the Task/`-p` argv.

**Task seats and stalls:** the orchestrator cannot poll inside a blocking `Task`. Do **not** peek at `subagents\*.jsonl` mid-flight and call that a stall. If a Task hang, the user interrupt is the escape. State this if a seat is taking unusually long. CLI stalls: see [Stall check-in](#stall-check-in).

### Token-saving rules (accuracy-preserving)

1. Path-only reviewer prompts; **open blocking IDs only** (comma-separated). Closed addressed IDs inherit unless reopened.
2. [Churn guard](#churn-guard): skip redundant mid-roster Cursor seats; still run remaining CLI.
3. Same-model skip (above).
4. Short `request.md` hints (absolute path + stable heading anchor; do not inline skill bodies or brittle line ranges); live disk outranks packs.
5. Late Cursor seats: stabilize; Gaps `- none` + `UNCHANGED` when nothing material. **Carve-out:** for `role: breadth-auditor`, Revised defaults to `UNCHANGED`, but Gaps must still report breadth findings so later seats can address them (`- none` means genuinely nothing found).
6. `show_exchange: ask` → final-only when ambiguous (no duplicate full transcript in chat).
7. CLI preflight **per command family** before that family's first seat (`agy`/`gemini` vs `codex`; fail independently).
8. Keep the default 4-seat roster; do not drop Claude, GPT or Gemini seats for cost by default (cost cuts come from path-only prompts and a smaller shared core, not fewer reviewers).
9. Optional **smoke** roster via reconfigure only (1 Cursor + CLI) for plumbing checks; deep reviews keep the full roster.
10. Omit placeholder packet files; ordinary seats read `open-gaps.md` not `gaps.md`; inline [reviewer-envelope.md](reviewer-envelope.md).
11. Optional [Terminal calm skip](#terminal-calm-skip) (`terminal_calm_skip`, default off).
12. [Size check and partitioning](core.md#size-check-and-partitioning): never mega-dump; framing stays size-bounded (multiplied across partitions); prefer `UNCHANGED` over cosmetic full rewrites.

### Churn guard

_Legacy (removed in step 5): examples below describe the pre-step-3 7-seat roster._

`churn_guard: 0` disables churn-stop.

**Normalize** artifact text before equality: interpret as UTF-8; NFC; `\r\n` → `\n`; strip trailing whitespace on each line; ensure a single trailing newline.

After each **successful** parse (and after content-loss acceptance when applicable):

**Redefined 2026-09-12: cost-tier-based, not kind-based.** `calm_streak` now tracks calm **non-Gemini** seats (Claude, Codex, Grok — whatever the roster's paid reviewers are), not `kind: cursor` specifically. Gemini/agy seats are excluded entirely from this mechanism in both directions — a Gemini seat never increments, never resets, and is never itself skipped, regardless of whether it was calm. The guard exists to control cost on the seats that cost real money; Gemini is cheap enough that its cost isn't being managed here at all (see the interleaving note in "Default roster intent" above).

**By design, not an oversight (flagged by Council review as G1-5, addressed 2026-09-13):** because Gemini is a no-op either direction, `calm_streak` can bridge across a substantive Gemini revision that happens between two calm non-Gemini seats — e.g. a calm Claude, then a Gemini pass that makes real changes, then a calm Codex still reaches `churn_guard: 2` and fires churn-stop, even though the artifact just changed and no non-Gemini reviewer has seen the post-Gemini state yet. Also, once churn-stop has fired, a later Gemini pass revising the artifact further does not un-fire it — the skip, once triggered, stands for the rest of the run. This is intentional: churn-guard is a pure **expenditure cap on the paid seats**, not an artifact-convergence signal — it exists to stop paying for redundant Claude/Codex/Grok turns, and whether Gemini did real work in between is deliberately irrelevant to that goal, per the user's explicit preference that Gemini's cost (and by extension its activity) not factor into this guard at all. If artifact-convergence semantics are ever wanted instead, a substantive (non-calm) Gemini pass would need to reset `calm_streak` — that would be a different, incompatible design and isn't what's implemented here.

1. If Gaps is `- none` **or only new `optional:` gaps** **and** normalized Revised equals normalized pre-seat artifact (including legal `UNCHANGED`) **and** this seat introduced no new `unresolved` / `rejected` statuses on open IDs, **and this seat's resolved family is not `gemini`**, increment `calm_streak`. If this seat's family **is** `gemini`, `calm_streak` is left unchanged either way — no increment, no reset. Otherwise (a non-Gemini seat that failed the calm test), reset `calm_streak` to 0.
2. Standing prior `rejected` / `unresolved` IDs do **not** block calm (they are inert for the streak).
3. When `calm_streak` reaches `churn_guard`: append `failures.md` with `churn-stop` and the calm seat indices. Set a flag: skip remaining seats whose resolved family is **not** `gemini` (Claude, Codex, Grok — whichever remain) unless `validation_critical: true` or `request.md` asks to observe/validate that seat/label. **Always** still dispatch every remaining Gemini seat regardless of streak state.

**Consequence for Codex:** Codex is no longer categorically protected from churn-stop the way `kind: cli` used to protect it — it now counts toward `calm_streak` like any other non-Gemini seat, and is in principle skippable. **At the default `churn_guard: 2`** on the current interleaved roster (Gemini → Claude → Gemini → Codex → Gemini → Grok → Gemini) this changes nothing observable: `calm_streak` can only reach 2 *after* Codex has already run (Codex is the earliest seat, other than Claude, that can push the streak to 2), so by the time churn-stop could fire, Codex is no longer "remaining" — Grok is the only seat left that's ever actually skippable at this setting. **This does not hold at `churn_guard: 1`**: a calm Claude alone reaches the threshold before Codex is dispatched, and churn-stop then skips every remaining non-Gemini seat — Codex included. If the roster is reordered later so a non-Gemini seat follows two other calm non-Gemini seats, that seat becomes a real skip candidate under this rule too — this is a deliberate design point now, not an oversight.

**Example (current 7-seat interleaved roster, Composer/unknown seat 0):** Gemini seat 1 (breadth, usually not calm if it raised blocking Gaps; excluded from the streak either way) → calm Claude (`calm_streak` → 1) → Gemini pass 2 (excluded, streak stays 1) → calm Codex (`calm_streak` → 2, reaches `churn_guard: 2`) → churn-stop fires: skip Grok (the only remaining non-Gemini seat) → Gemini pass 3 and Gemini pass 4 still run regardless. If Codex was *not* calm instead, `calm_streak` resets to 0 and Grok still runs on schedule.

### Terminal calm skip

_Legacy (removed in step 5): references to `kind` and the 7-seat roster below predate the `seats:` schema._

`terminal_calm_skip: false` (default) disables this feature. `churn_guard: 0` disables it as well. This is **orthogonal** to [Churn guard](#churn-guard) and uses a **separate** `terminal_calm_streak`. Cursor-only; the Claude sibling documents it as not applicable.

After each **successful** parse (same calm test as churn, except **any kind** including CLI may increment `terminal_calm_streak`):

1. If the seat is terminal-calm (Gaps `- none` or only new `optional:` gaps; legal identity Revised; no new `unresolved`/`rejected` on open IDs), increment `terminal_calm_streak`. Else reset it to 0.
2. When `terminal_calm_skip` is true **and** `terminal_calm_streak` has reached `churn_guard` **and** every **remaining** seat is `kind: cursor` and independently skippable, skip those trailing Cursor seats. `failures.md` reason `terminal-calm`.
3. **Never skip:** `kind: cli`; `validation_critical: true`; a seat or label that `request.md` names for validation or observation; `role: breadth-auditor`. If any remaining seat is protected, run it and reevaluate the tail afterward.

CLI calm **may** increment `terminal_calm_streak` even for seats excluded from `calm_streak`. **Corrected 2026-09-13** (this sentence used to say "must not increment Cursor `calm_streak`" — stale since the 2026-09-12 redefinition made `calm_streak` non-Gemini-based, not Cursor-based; Codex CLI *does* increment `calm_streak` now, only Gemini is excluded from it — but the point about `terminal_calm_streak` being a separate counter that any kind, including CLI, can increment stands unchanged). Example (pre-interleaving 4-seat roster): Gemini (blocking gaps, not calm) → Claude (rewrite) → Codex `UNCHANGED` + Gaps none → with `terminal_calm_skip: true` and `churn_guard: 1` (or two terminal-calm seats if guard is 2), skip Grok. If Codex still edits, Grok still runs.

**Corrected 2026-09-13 — the original "confirmed dead, no reading under which it still works" claim here was wrong; caught by Council review of the interleaving diff itself (G1-2).** Rule 2 requires *every remaining seat* to be `kind: cursor` before skipping the trailing run. On the **default** roster (Claude or a Cursor-family seat 0, or Composer/unknown seat 0) this is indeed a no-op: the roster always ends on a Gemini (CLI) seat (position 7), so there is never a point where the entire remaining tail is Cursor-kind. **But it is *not* dead on a Gemini-family seat 0**: same-model skip removes all four Gemini reviewer seats there, leaving the effective path Claude → Codex → Grok — which *does* end on a trailing Cursor seat (Grok), so `terminal_calm_skip: true` can still fire in that specific case, exactly as it did pre-interleaving. If terminal-calm-skip's cost savings matter for non-Gemini seat-0 runs specifically, either move Grok to the last position (breaking the strict alternation) or accept the feature is inert there until the roster changes again.

### Family slug resolve

Cursor seats match **provider family**, not an exact Task slug. Opus 5 Extra High and Sonnet Medium are the same seat family (`claude`). Grok 4.6 High and Grok 4.5 High are the same family (`grok`).

A seat has no slug. Use `family` directly as the family. For steps 4-5 treat the seat's `line` and `effort` as the configured tokens (no exact-slug match), and when neither side has a `\d+\.\d+` version token, prefer the **highest** version number among candidates that share the `line`. A `pin` is the configured slug and is dispatched verbatim when listed. Never pick a candidate from a different `line`.

For a configured slug (a `pin`, or the synthesized `line`/`effort` tokens) and its label:

1. Map it to a family using [Same-model skip](#same-model-skip). `inherit` is never a reviewer candidate.
2. Collect Task `model` enum values with that same family.
3. If that set is empty → skip. `failures.md` reason `rejected-family`. One line to the user. Do **not** try a different family.
4. If the configured slug is in the set → dispatch that slug.
5. Otherwise pick one candidate:
   - If the config slug contains a `\d+\.\d+` token (e.g. `4.6`), prefer candidates that share that **exact** version token. If neither config nor candidates have `\d+\.\d+`, skip this step (do not invent a version from a lone digit like Claude `5`).
   - Then score remaining shared hyphen-separated tokens (`high`, `fast`, `medium`, `thinking`, `sol`, …). Highest score wins.
   - Tie-break: [models.md](models.md) catalog table order, then Task enum order.
6. If the dispatched slug differs from config, append `failures.md` with reason `family-remap` (`wanted <config-slug>, dispatched <enum-slug>`). That is **not** a skip — still run the seat. Tell the user in one line.
7. `Task` `model` = the resolved slug. If that call is rejected, skip `rejected-slug` and **do not** try another candidate (no retry loop).

Worked example (enum has `cursor-grok-4.5-high-fast` and `cursor-grok-4.6-medium`):

- Config `cursor-grok-4.6-high` → family `grok` → dispatch `cursor-grok-4.6-medium` (shared `4.6` beats `high`+`fast` on 4.5).
- Config `claude-sonnet-5-thinking-medium` → family `claude` → dispatch any listed `claude-*` (no `\d+\.\d+` version step).
- Config `gpt-5.6-sol-medium` listed exactly → dispatch as-is (**Cursor Task fallback only**; default roster uses Codex CLI).
- Config a `claude-*` seat when the enum has **no** `claude-*` → skip `rejected-family`.

Write `seat-<n>-<dispatched-slug>.md` (the slug actually passed to `Task`).

CLI seats (`agy` / `gemini` → family `gemini`; `codex` → family `gpt`) are not Task-routed and do not use Family slug resolve.

### Task seat (all families)

In Cursor every seat is dispatched this way.

1. `Task` with `subagent_type: generalPurpose`, `model` = **resolved** slug, `run_in_background: false`.
2. **Always read-only:** instruct the seat **not** to Write, Edit, Delete, or otherwise mutate the workspace — in both `review-only` and `implementation` modes. Implementation proposals belong only in Revised output (path-fenced file bodies). The orchestrator applies them after a successful parse (see [Apply implementation Revised](core.md#apply-implementation-revised)).
3. Parse per [Parse and retry](core.md#parse-and-retry). Retry = **new** Task (do not assume resume), also read-only.

### CLI seat (`kind: cli`)

_Not used by Cursor (every seat is dispatched through Task). Kept as reference for the Claude Code sibling until step 5 removes it._

Branch on `command` (and label). Maintain **per-command-family** preflight state for the run: `agy`/`gemini` vs `codex` fail independently.

**Antigravity / Gemini (`command: agy` or `gemini`, or Antigravity/Gemini label):**

1. If `command: agy` (or Antigravity label): use `agy` only. Else try `gemini`, then `agy`. See [models.md](models.md).
2. Before the **first** `agy`/`gemini` seat in the run (and on reconfigure probe): run Antigravity [CLI preflight](models.md#cli-preflight-antigravity--gemini-family). On failure, skip remaining **`agy`/`gemini` seats only** (`cli-preflight-agy`); continue (Codex seats still eligible).
3. Write `seat-<n>-prompt.md` as a short pointer (metadata keys in [models.md](models.md)). Do **not** inline the artifact body.
4. Invoke with the Antigravity recipe in [models.md](models.md): `ArgumentList.Add` (or `pwsh` fallback), UTF-8, timeout, `--add-dir` skill root + `review_roots`, optional `model`/`effort`. Never yolo / `--dangerously-skip-permissions` / `GEMINI_API_KEY`.
5. Write `seat-<n>-argv-meta.txt` (exe, PS version, add-dir, role, model/effort, timeout, exit — no secrets).
6. Failure classes per [models.md](models.md): binary missing; auth; `permission-gate`; `ps-runtime`; non-zero exit; empty stdout; interactive ask; timeout.

**Codex (`command: codex` or Codex/GPT-CLI label):**

1. Resolve the Windows launcher **once** per run ([models.md](models.md) Codex section). Reuse for login status, preflight, and every Codex dispatch. Record FileName, script/js, and version in `meta.md` / argv-meta.
2. Before the **first** `codex` seat (and on reconfigure probe): run Codex [CLI preflight](models.md#cli-preflight-codex-family) with that launcher (version, login status, fresh `-o`, write-deny). On failure, skip remaining **`codex` seats only** (`cli-preflight-codex`); continue (Gemini seats still eligible).
3. Write `seat-<n>-prompt.md` as a short pointer; `kind: cli`, `family: gpt`, role if any.
4. Invoke with the Codex recipe in [models.md](models.md): same resolved launcher; `codex exec`; `-s read-only`; `--ephemeral`; `--ignore-user-config`; `--skip-git-repo-check`; `-C` skill root; `--add-dir` skill root + run + `review_roots`; optional `-m` / `-c model_reasoning_effort=…`; `-o` last-message path; **final positional prompt** (never `-p`). Truncate `-o` before every attempt including retries; accept only a fresh write. On timeout, kill the **process tree**. Timeout = `cli_timeout_sec`. Never `--dangerously-bypass-approvals-and-sandbox`. Never request API keys in chat.
5. Write `seat-<n>-argv-meta.txt` (launcher, script, version, login_mode, add-dir, ephemeral, ignore_user_config, sandbox, model/effort, timeout, exit, last_message_fresh — no secrets).
6. Parse the Council envelope from the fresh `-o` file. Failure classes per [models.md](models.md): resolve/auth/stale-`-o`/timeout/write-deny/`ps-runtime`/non-zero/empty.

Both CLI kinds: reviewers are read-only; implementation path-fenced apply is refused for `kind: cli` (see [Apply implementation Revised](core.md#apply-implementation-revised)).

### Parse and retry

Shared: see [core.md](core.md#parse-and-retry).

### Breadth-auditor content-loss guard

Applies only when the seat has `role: breadth-auditor`, after a **successful** envelope parse and **before** replacing `artifact.md`.

Reuse churn-guard **Normalize** for every comparison and the character-count check.

- Unchanged normalized Revised **or legal `UNCHANGED` identity** → pass immediately.
- Changed Revised passes only when **all** of:
  1. Gaps contains at least one bullet whose text begins with `Factual correction:` after the bullet marker.
  2. Revised is not patch-like (same test as Parse and retry — do not invent a second heuristic).
  3. **Headings:** single-pass scan; track fenced regions by opening fence marker; collect ATX headings (`^#{1,6}\s`) **outside** fences as ordered `(level, trimmed text)`. Revised list must equal the prior list exactly (no removals, reorders, level changes, or added headings). `#` lines inside fences (e.g. YAML comments) are not headings.
  4. **Fences:** ordered list of fence info strings (trimmed; empty string for bare fence) identical in count and content.
  5. Normalized Revised character count ≥ 85% of the prior artifact (floor only; growth allowed if 3–4 hold).

On failure:

1. Retry once with an explicit instruction to copy the prior artifact verbatim and put non-factual observations in Gaps — **only if** the seat still has its retry budget.
2. If retry fails or no retry remains: `failures.md` reason `cli-content-loss`; keep previous `artifact.md`; keep the raw failed reply in `seat-<n>-*.md`; append that seat's Gaps under `### Seat <n> — <label> (revised rejected)` and retain them as unincorporated [Breadth audit findings](core.md#return); do **not** apply that seat's `addressed` statuses / dissent-check restore from the rejected Revised; continue as a failed seat.

### After a successful parse

Shared: see [core.md](core.md#after-a-successful-parse). Cursor extensions applied there: [Breadth-auditor content-loss guard](#breadth-auditor-content-loss-guard), [Churn guard](#churn-guard), [Terminal calm skip](#terminal-calm-skip).

### Apply implementation Revised

Shared: see [core.md](core.md#apply-implementation-revised).

## Stall check-in

While a **CLI** process is alive: every `stall_checkin_sec` (default 60) send one line to the user: `Seat <n> still running, <elapsed>s elapsed`. Do **not** claim stdout/stderr file-byte growth monitoring (those files are written after exit).

Task seats: no internal poll; user interrupt if hung.

## Run retention

Shared: see [core.md](core.md#run-retention) (applies to Cursor now; Claude adopts it in step 8).

## Return

Shared: see [core.md](core.md#return). Cursor exchange handling (ledger D6): if exchange was requested, write `<run>/exchange.md` with the transcript and set `<skill-root>/last-exchange.md` to a single line with its absolute path (or run id). Secret-hygiene applies to both.

## Reconfigure

Read [models.md](models.md). Then:

1. Ask how many reviewers (minimum 1; warn above `max_reviewers`).
2. Ask which catalog entry for each seat, in order. Mention optional smoke roster (1 Cursor + CLI) for plumbing-only checks. When a Gemini/Antigravity CLI seat is chosen, default its `role` to `breadth-auditor` unless the user explicitly opts out. When the user picks **ChatGPT/GPT**, default to **Codex CLI** (`command: codex`); offer Cursor Task `gpt-5.6-sol-medium` only as an explicit fallback.
3. If Gemini/Antigravity is chosen, run that family's CLI preflight from [models.md](models.md). If Codex is chosen, run Codex preflight (same resolved launcher). If missing/failed, print install + login steps for **that** family and ask whether to keep the seat or pick another. A failed probe: do **not** persist a broken seat unless the user explicitly insists. Do not treat a Gemini probe failure as a Codex failure (or vice versa).
4. Ask whether to persist and the `show_exchange` default (`ask` / `always` / `never`).
5. Write the `seats:` list of [config.yaml](config.yaml) if they chose to persist: keep hygiene keys; preserve comments on `churn_guard: 0` and `terminal_calm_skip`; **preserve** any existing `role: breadth-auditor` (and related roster comments) unless the user explicitly removes that seat or role — do not silently demote Gemini to an ordinary CLI reviewer. One-shot overrides do not persist unless they say to save.
6. If persisted, add an entry to [CHANGELOG.md](CHANGELOG.md) (dated, `Changed`/`Added`/`Removed` as fits) describing the roster/config change — this file is shared with the Claude sibling and is the only consumer-facing record of what changed, since this repo has no version pins or release notifications. Skip only for a reconfigure that changes nothing observable (e.g. re-persisting the same roster).
7. Run the cascade unless they chose **Reconfigure only**.

## Examples

**Run**

```
/Council Review this auth design and tighten the threat model.
```

If AskQuestion is available, confirm roster; otherwise treat a full request as Proceed. Draft seat 0 → save `seat-0-*.md` → reviewers with envelope → return last artifact.

**Reuse existing artifact**

```
/Council proceed review the plan above
```

Pack prior plan as `artifact.md`; skip seat-0 rewrite; cascade reviewers.

**Reconfigure**

```
/Council configure
```

Example: user picks 3 seats (Grok, Codex CLI GPT, Antigravity), persists, `show_exchange: ask`. Write `config.yaml` (keep `max_reviewers` and other hygiene keys). Probe `agy` and `codex` independently; if one is missing, do not persist that seat unless they insist. Then run the cascade unless they chose Reconfigure only.
