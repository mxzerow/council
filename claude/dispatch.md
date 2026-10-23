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

The **Grok CLI (`grok` seats) recipe lives entirely in this file, not `models.md`,
on purpose:** it's Claude-side-only. Cursor's own orchestrator never shells
out — it has native `Task` tool access — so this recipe has no reason to live
in a file shared with Cursor's skill.

## Transport mapping

| Seat `family` | Transport | Preflight bucket | Dispatch |
|---|---|---|---|
| `gemini` | `agy` (legacy `gemini`) | `agy`/`gemini` | Antigravity recipe in `models.md` |
| `gpt` | `codex` | `codex` | Codex recipe in `models.md` |
| `grok` | Grok CLI (`agent` on PATH) | `agent` | Recipe below, this file |
| `claude` | none (same family as the orchestrator) | — | skipped by Same-model skip |
| `composer` | none | — | skipped, `no-transport` |

Preflight failures skip **only that bucket** (`cli-preflight-agent` /
`cli-preflight-agy` / `cli-preflight-codex`). A failed probe in one bucket must
not skip the other two.

## `grok` seat — Grok CLI (`agent`)

On this machine `agent` is the **Grok Build CLI** (`~/.grok/bin/agent.exe`,
`grok 1.0.62` when verified 2026-10-23), not Cursor's `agent`. The old Cursor
recipe (`--mode plan --trust --workspace`) fails with `unexpected argument
'--mode'`; do not use it. This recipe is Claude-side-only (Cursor dispatches
Grok through Task), which is why it lives here and not in `models.md`.

Catalog: `agent models` (prints "You are not authenticated" yet headless calls
work; lists e.g. `grok-4.7`). Resolve the newest version of the seat's `line`
from it per Latest-in-family.

### Preflight (`cli-preflight-agent`)

1. Resolve `agent` on PATH (refresh PATH first, per the top of this file) and
   capture `agent --version`.
2. Write `<run>\cli-preflight.txt` containing exactly `PREFLIGHT-OK`.
3. Run the invoke recipe below with the prompt `Read <run>\cli-preflight.txt
   and reply with exactly its contents.`
4. Success: exit 0, JSON parses, and `.text` contains `PREFLIGHT-OK` (the text
   may include a short preamble).
5. Failure: skip remaining `grok` seats only (`failures.md` reason
   `cli-preflight-agent`); show the user the raw error.

### Invoke

```powershell
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = (Get-Command agent).Source
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
$psi.RedirectStandardInput = $true; $psi.CreateNoWindow = $true
$psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
foreach ($t in @('-p', $shortPrompt, '--model', $slug, '--permission-mode', 'plan',
                 '--output-format', 'json', '--cwd', $runDir, '--max-turns', '12')) {
  $psi.ArgumentList.Add($t)
}
$p = [System.Diagnostics.Process]::Start($psi)
$p.StandardInput.Close()          # required: otherwise the CLI can wait on stdin
$out = $p.StandardOutput.ReadToEndAsync(); $err = $p.StandardError.ReadToEndAsync()
if (-not $p.WaitForExit($timeoutMs)) { Stop-Process -Id $p.Id -Force }   # timeout
$text = ($out.Result | ConvertFrom-Json).text
```

- `--permission-mode plan` is read-only (a write probe created no file,
  verified 2026-10-23). Never drop it for a review-only run.
- `--cwd $runDir` lets the seat read the packet files; list their absolute
  paths in the short prompt as for the other CLI seats.
- The reply is the JSON `.text` field. It may contain a sentence or two before
  the first Council sentinel: strip it (see Envelope parsing below). Failure
  conditions (skip seat, log to `failures.md`, continue): non-zero exit; JSON
  parse fails; `.text` has no Council sentinel after one retry.

## `gemini` seat — `agy` / `gemini` (Gemini / Antigravity)

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

## `gpt` seat — `codex`

Follow the **Codex CLI** section in shared `models.md` exactly:

1. **Resolve launcher once** per run (`.exe` → `node`+`codex.js` →
   `pwsh -File codex.ps1`). Reuse for `login status`, preflight, and every
   dispatch. Record FileName, script/js, and version in argv-meta.
2. **Per-command preflight** before the first Codex seat (version, login mode,
   fresh `-o`, write-deny under skill root + review roots). Failure →
   `cli-preflight-codex` only.
3. Invoke: same launcher; `exec`; `-s read-only`; `--ephemeral`;
   `--ignore-user-config`; `-c windows.sandbox="unelevated"` (required with
   `--ignore-user-config`, else shell reads are rejected as "blocked by policy";
   verified 2026-10-23); `--skip-git-repo-check`; `-C` skill root;
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
