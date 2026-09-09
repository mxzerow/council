# Council seat dispatch

Read this before dispatching any seat. Recipes use PowerShell, not Bash —
Claude Code's Bash tool (Git Bash) does not see `agent`, `agy`, or `codex` on
PATH even when they're installed; PowerShell needs its PATH refreshed too
before the first call in a session. Do this once per session, not once per seat:

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
```

Never pass secrets, `.env` contents, or credentials in any prompt to any CLI.

**Source of truth for CLI argv — `agy`/Gemini and `codex`/GPT only:**
`models.md` next to the shared `config.yaml` (see SKILL.md). Those two are
shared with Cursor's own skill, so their recipes live in the shared file and
this file just summarizes the branching — don't invent a second conflicting
recipe for them here.

The **native `agent` CLI (`kind: cursor` seats) recipe lives entirely in this
file, not `models.md`, on purpose:** it's Claude-side-only. Cursor's own
orchestrator never shells out to `agent` — it has native `Task` tool access —
so this recipe has no reason to live in a file shared with Cursor's skill.

## CLI command-family mapping

| Config `command` / label | Family | Preflight bucket | Dispatch |
|---|---|---|---|
| `kind: cursor`, native Cursor CLI | matches `models.md`'s family table | `agent` | Recipe below, this file |
| `command: agy` / `gemini`, Antigravity/Gemini | `gemini` | `agy`/`gemini` | Antigravity recipe in `models.md` |
| `command: codex`, Codex CLI / GPT CLI | `gpt` | `codex` | Codex recipe in `models.md` |

Preflight failures skip **only that bucket** (`cli-preflight-agent` /
`cli-preflight-agy` / `cli-preflight-codex`). A failed probe in one bucket must
not skip the other two.

## `kind: cursor` seat — native Cursor `agent` CLI

This is the substitute for Cursor's own `Task` tool with a `model` slug. It
was verified live: `agent -p --model <slug> --output-format json "<prompt>"`
genuinely dispatches to that vendor's model (confirmed cross-vendor identity
responses for `cursor-grok-4.6-high-fast`, `gpt-5.6-sol-medium`, and
`claude-opus-5-thinking-high` in one test session) and returns clean JSON.

**Last verified:** 2026-09-06, `agent` version `2026.09.02-c22c1a3`. Confirmed
that day: all flags below (`-p`, `--mode`, `--trust`, `--workspace`, `--model`,
`--output-format`) still parse and behave as documented, including reading a
real file inside the trusted `--workspace` with no permission gap (the
`--add-dir`-vs-project-trust issue that hit `agy` the same day does **not**
reproduce here). This was checked specifically *because* `agy`'s flag surface
had silently drifted (`--workspace`/`--trust` removed, `--print` calling
convention changed) between when Council was built and that date — `agent`
had not drifted, but nothing would have caught it early if it had, which is
why the preflight below exists now. If `agent --version` reports anything
other than `2026.09.02-c22c1a3` on the machine you're running from, treat this
recipe as **unverified since that version** — the preflight will catch a
broken flag, but re-confirm the file-read-under-`--workspace` behavior by hand
if you have time, since a preflight only proves the probe prompt worked, not
every documented behavior.

### Preflight (`cli-preflight-agent`)

Before the **first** `kind: cursor` seat in a run (and on reconfigure probe
for this family), using the **same** flags as a real seat:

1. Resolve `agent` on PATH (refresh PATH first, per the top of this file). Not
   found → fail `agent-resolve`.
2. Capture `agent --version`; record it in `meta.md` / argv-meta next to the
   run.
3. Write `<run>\cli-preflight.txt` containing exactly `PREFLIGHT-OK`.
4. Dispatch: `agent -p --mode plan --trust --workspace <skillRoot> --add-dir
   <run> --model <first configured kind:cursor slug> --output-format json
   "Read <run>\cli-preflight.txt and reply with exactly its contents."` —
   **`--add-dir <run>` is required, not optional.** `--workspace <skillRoot>`
   alone only covers the shared skill root; for the Claude sibling the run
   folder lives under `~/.claude/skills/council/runs/...`, a **separate**
   tree from `--workspace`'s target, so `cli-preflight.txt` (and every real
   seat's `seat-N-prompt.md`) is unreadable without it. Confirmed by direct
   reproduction 2026-09-09: this exact preflight failed with `read_file`
   auto-denied until `--add-dir <run>` was added, despite `--workspace
   <skillRoot>` already being present — the same class of gap as `agy`'s
   `--add-dir` issue, just against a different directory pair.
5. Success: exit 0, output parses as JSON, `.is_error` is false, `.result`
   contains `PREFLIGHT-OK`.
6. Failure (any of: non-JSON output, non-zero exit, `.is_error` true, missing
   `PREFLIGHT-OK`, an argument-parsing error instead of a real response): skip
   remaining `kind: cursor` seats **only** this run (`failures.md` reason
   `cli-preflight-agent`); do not skip `agy`/`gemini` or `codex` seats. Show
   the user the raw preflight failure text — an argument error here means the
   flags in this file need the same kind of fix `agy`'s got, not a silent skip
   with no explanation.

### Invoke

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

Known local gotcha (confirmed by direct reproduction 2026-09-06, superseding
an earlier wrong theory in this file): `agy --print` fails with
`jetski: no output produced — a tool required the "read_file" permission
that headless mode cannot prompt for it, so it was auto-denied` whenever the
target file sits inside a **recognized project/git workspace** that wasn't
passed via `--add-dir` — even when the process's own working directory *is*
that project. Reading an ad hoc non-project path (e.g. a scratch temp dir)
does not trigger this at all; it's specifically project-workspace trust
gating, not a missing global permission. Fix: pass `--add-dir <that project
root>` (see `models.md`'s Invoke recipe) — **not** a `~/.gemini/antigravity-cli/settings.json`
`permissions.allow` entry (that was tried and did not reproduce or fix
anything; do not resurrect it). Do not add `--dangerously-skip-permissions`.
Also confirmed the same day: `--workspace` and `--trust` no longer exist on
`agy` (`flags provided but not defined: -workspace`) and `--print` followed
by another flag now needs the attached form `--print=<prompt>` — an older
agy version apparently accepted the syntax this file used to document; if a
dispatch fails with an argument-parsing error rather than a permission one,
that's this drift, not the permission issue above.

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
