# Council seat dispatch

Read this before dispatching any seat. Recipes use PowerShell, not Bash —
Claude Code's Bash tool (Git Bash) does not see `agent`, `agy`, or `codex` on
PATH even when they're installed; PowerShell needs its PATH refreshed too
before the first call in a session. Do this once per session, not once per seat:

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
```

Never pass secrets, `.env` contents, or credentials in any prompt to any CLI.

**Source of truth for CLI argv:** `models.md` next to the shared `config.yaml`
(see SKILL.md). This file summarizes sibling branching; do not invent a second
conflicting recipe.

## CLI command-family mapping

| Config `command` / label | Family | Preflight bucket | Dispatch |
|---|---|---|---|
| `command: agy` / `gemini`, Antigravity/Gemini | `gemini` | `agy`/`gemini` | Antigravity recipe in `models.md` |
| `command: codex`, Codex CLI / GPT CLI | `gpt` | `codex` | Codex recipe in `models.md` |

Preflight failures skip **only that bucket** (`cli-preflight-agy` vs
`cli-preflight-codex`). A failed Gemini probe must not skip Codex, and vice versa.

## `kind: cursor` seat — native Cursor `agent` CLI

This is the substitute for Cursor's own `Task` tool with a `model` slug. It
was verified live: `agent -p --model <slug> --output-format json "<prompt>"`
genuinely dispatches to that vendor's model (confirmed cross-vendor identity
responses for `cursor-grok-4.6-high-fast`, `gpt-5.6-sol-medium`, and
`claude-opus-5-thinking-high` in one test session) and returns clean JSON.

```powershell
$runDir  = Join-Path $skillRoot "runs\<timestamp>"   # skill root = this SKILL.md's directory
$promptFile = Join-Path $runDir "seat-<n>-prompt.md"
$prompt = Get-Content -LiteralPath $promptFile -Raw

$raw = agent -p --mode plan --trust --workspace $runDir --model "<slug>" --output-format json $prompt 2>&1
$code = $LASTEXITCODE
```

- `--mode plan` = read-only/planning — the seat cannot write or edit files.
  Never drop this for a review-only run. For an `implementation` run mode
  (rare — only when the request itself is implementation and Revised output
  is that implementation), see [SKILL.md](SKILL.md)'s run-mode note before
  removing `--mode plan`; prefer keeping it and treating the seat's output as
  a proposed diff instead.
- `--trust` avoids an interactive workspace-trust prompt that would hang
  headlessly.
- `--workspace $runDir` lets the seat read `request.md`/`artifact.md`/etc. by
  relative path without extra `--add-dir` flags.
- Parse `$raw` as JSON. The reply text is the `.result` field. Failure
  conditions (skip seat, log to `failures.md`, continue): `$code -ne 0`;
  `.is_error` true; JSON parse fails; `.result` empty or missing the envelope
  sentinels after one retry.

Model slugs come straight from `config.yaml`'s `model:` field — they're the
same strings Cursor's Task tool and `agent --model` both accept (verified via
`agent --list-models`). No translation needed. If a saved slug is rejected,
skip the seat, note it, and point the user at `models.md` /
`agent --list-models` to refresh the catalog.

Default GPT reviews use **Codex CLI**, not `agent --model gpt-…`. Keep Cursor
Task GPT only when the user explicitly chose that fallback.

## `kind: cli` seat — `agy` / `gemini` (Gemini / Antigravity)

Follow the exact Antigravity recipe in Cursor's `models.md` (detect `agy`
first, fall back to `gemini`; write the prompt to `seat-<n>-prompt.md`; invoke
print mode with `--add-dir`; never pass `--dangerously-skip-permissions`,
`--yolo`, or `GEMINI_API_KEY`).

Per-command preflight: before the first `agy`/`gemini` seat, run that family's
preflight. On failure, skip remaining `agy`/`gemini` seats only.

Known local gotcha: `agy --print` can fail headlessly with a permission-gate
error until an allow-rule is added under `permissions.allow` in agy's settings.
Treat as CLI-seat failure — skip, log, continue. Do not add
`--dangerously-skip-permissions`.

## `kind: cli` seat — `codex` (GPT family)

Follow the **Codex CLI** section in shared `models.md` exactly:

1. **Resolve launcher once** per run (`.exe` → `node`+`codex.js` →
   `pwsh -File codex.ps1`). Reuse for `login status`, preflight, and every
   dispatch. Record FileName, script/js, and version in argv-meta.
2. **Per-command preflight** before the first Codex seat (version, login mode,
   fresh `-o`, write-deny under skill root + review roots). Failure →
   `cli-preflight-codex` only.
3. Invoke: same launcher; `exec`; `-s read-only`; `--ephemeral`;
   `--ignore-user-config`; `--skip-git-repo-check`; `-C` skill root;
   `--add-dir` skill root + run + review roots; optional `-m` /
   `-c model_reasoning_effort=…`; `-o seat-N-last-message.txt`;
   **final positional prompt** (never `-p` — that is `--profile`).
4. Delete/truncate `-o` before every attempt including retries; accept only a
   file freshly written this attempt.
5. On timeout, kill the **entire process tree** rooted at the launched PID.
6. Parse the Council envelope from the fresh `-o` file (not noisy stdout).

Never request API keys in chat. Prefer ChatGPT browser login. When login
status reports `Logged in using ChatGPT`, ChatGPT/Codex quota applies. For
API-key automation, use only what the installed CLI documents (`OPENAI_API_KEY`
with `codex login --with-api-key`).

## Envelope parsing (both seat kinds)

Look for Council sentinels in the reply text (not `## Gaps` headings — those are
prose, not machine markers). Recognized sentinels:

```text
<<<COUNCIL_STATUS>>>
...
<<<COUNCIL_GAPS>>>
...
<<<COUNCIL_UNRESOLVED>>>
...
<<<COUNCIL_REVISED>>>
...
<<<COUNCIL_END>>>
```

`<<<COUNCIL_STATUS>>>` is required by the shared contract when prior gap IDs
exist. Find the **first** Council sentinel of any kind
(`<<<COUNCIL_STATUS>>>`, `<<<COUNCIL_GAPS>>>`, …) and discard only text
**before** that sentinel. Do **not** search for `<<<COUNCIL_GAPS>>>` first and
drop a preceding STATUS block.

`<<<COUNCIL_REVISED>>>` and `<<<COUNCIL_END>>>` must both be present. Anything after
`<<<COUNCIL_END>>>` is not part of the artifact — discard it.

**Identity:** if the Revised body, trimmed and compared case-insensitively, is
exactly `UNCHANGED`, treat the artifact as unchanged (copy the pre-seat
`artifact.md`). Empty Revised, a missing Revised marker, or a missing closer is
**malformed**, not identity — consume the single parse retry. `UNCHANGED` plus
ordinary `addressed` on an **open** ID is illegal identity (`addressed (pre-existing)`
is the legal identity close).

**Observed in testing:** a seat can still emit a sentence or two of preamble
before the first sentinel even though the contract says not to. That is not a
parse failure — strip the preamble; only treat the seat as failed if no
Council sentinel appears at all, or the Revised section is empty/missing
without a legal `UNCHANGED` token.

For Codex seats, apply the same sentinel rules to the contents of the fresh
`-o` last-message file.
