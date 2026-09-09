# Council model catalog

Read this when reconfiguring, validating Cursor slugs, or when a Gemini/Antigravity or Codex CLI seat fails. **This file is the single catalog** for allowed reviewers (do not duplicate slug lists elsewhere).

## Cursor Task seats

Use `kind: cursor` and the `model` slug.

Catalog slugs are **preferred**. Before `Task`, resolve by **provider family** (see SKILL.md Family slug resolve). Exact slug is optional. Prefer same `\d+\.\d+` version token when present, then effort tokens. Skip only when **no** family member is listed (`rejected-family`). Log a `family-remap` when the dispatched slug differs. Do not remap across families. Do not retry a slug after Task rejects it.

| Label | `model` slug | Family (same-model skip) | Routing |
|-------|----------------|--------------------------|---------|
| Cursor Grok 4.6 High | `cursor-grok-4.6-high` | `grok` | Same family/version as Grok 4.6 Medium |
| Cursor Grok 4.6 High Fast | `cursor-grok-4.6-high-fast` | `grok` | Same family/version as Grok 4.6 Medium |
| Cursor Grok 4.6 Medium | `cursor-grok-4.6-medium` | `grok` | Preferred in default roster; prefer other `cursor-grok-4.6-*` on the Task enum before other Grok versions |
| Cursor Grok 4.5 High | `cursor-grok-4.5-high-fast` | `grok` | Older Grok generation; remap only when no `4.6` candidate exists |
| ChatGPT 5.6 Sol Medium | `gpt-5.6-sol-medium` | `gpt` | **Cursor Task fallback only** — default roster uses Codex CLI (`command: codex`) |
| Claude Sonnet 5 Thinking Medium | `claude-sonnet-5-thinking-medium` | `claude` | Preferred in default roster; remap to any `claude-*` on the Task enum |
| Claude Sonnet 5 Thinking High | `claude-sonnet-5-thinking-high` | `claude` | Same family as Sonnet Medium |
| Claude Opus 5 Thinking High | `claude-opus-5-thinking-high` | `claude` | Same family as Sonnet; optional, not the default roster seat |
| Composer 2.5 Fast | `composer-2.5-fast` | `composer` | Usually listed |

The initial draft is always the current chat model. It is not a catalog seat. Map it to a family for same-model skip (see SKILL.md).

## Gemini / Antigravity local CLI

```yaml
- kind: cli
  command: agy
  label: Gemini (Antigravity CLI)
  role: breadth-auditor
  model: gemini-3.7-flash-high   # default seat; alternate: gemini-3.1-pro-high for hard reviews
  # effort: high                # optional: low | medium | high (often already in the model slug)
  # Do not set validation_critical — kind: cli is already never churn-skipped
```

Also valid: `command: gemini` (legacy Gemini CLI; prefer `agy` on consumer accounts after June 2026).

Not a Task slug. Family: `gemini`. Default in shared config: **first** roster seat with `role: breadth-auditor` (copy-through Revised; Gaps seed IDs for Claude / Codex GPT / Grok — see [reviewer-prompt.md](reviewer-prompt.md) § Role: breadth-auditor). The orchestrator shells out. No API key. Never set or request `GEMINI_API_KEY`. Never pass `--dangerously-skip-permissions`, `--yolo`, or `--approval-mode yolo`. Headless print mode still needs filesystem access: always pass `--add-dir` on the **skill root**, plus any extra **review roots** from `meta.md` / request (see Invoke). That is not a permission bypass; it scopes which directories `read_file` may use without an interactive approval prompt.

### `seat-N-prompt.md` metadata

Write a short pointer file (not the artifact body). Include these keys as plain lines or a small YAML/header block:

- `seat`, `label`, `total`, `mode` (`review-only` | `implementation`)
- `kind` (`cursor` | `cli`)
- `family` (`grok` | `gpt` | `claude` | `gemini` | `composer` | `unknown`)
- `role` when set in config (e.g. `breadth-auditor`)
- open blocking gap IDs (comma-separated), absolute paths to packet files (`request.md`, `artifact.md`, `open-gaps.md` when present, `unresolved.md` only when it has real dissent). **Never** list `meta.md`. Ordinary seats: inline `reviewer-envelope.md`; do not list `reviewer-prompt.md`. Breadth-auditor / implementation: also list `reviewer-prompt.md`.

Do **not** reorder the Cursor Task seats catalog table above — its row order is the family-remap tie-break.

### Detect

1. If `command: agy` (or Antigravity label): use `agy` only
2. Otherwise: `gemini`, then `agy`

Refresh PATH in the shell session before probing:

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
Get-Command agy -ErrorAction SilentlyContinue
```

If the required binary is missing, unauthenticated, or print mode fails, skip the seat and show the steps below.

### Install and sign in (no API key)

**Antigravity (preferred):**

```powershell
irm https://antigravity.google/cli/install.ps1 | iex
```

Binary: `%LOCALAPPDATA%\agy\bin\agy.exe` (installer usually adds this to User PATH). Open a **new** terminal, run `agy`, choose **Google OAuth**, finish the browser flow. Paste any auth code into **that terminal**, not into chat.

**Legacy Gemini CLI:** `npm install -g @google/gemini-cli`, then `gemini` + Login with Google. Consumer Google-login may be blocked after June 2026 — use `agy` instead.

Do not ask the user to create or paste an API key for Council Seat CLI reviews.

### CLI preflight (Antigravity / Gemini family)

Before the **first** `agy`/`gemini` seat in the run (and on reconfigure probe for that family), use the **same** invoke recipe as a real seat:

1. Resolve `agy` (or `gemini`); confirm `ProcessStartInfo.ArgumentList` exists **or** `pwsh` is on PATH. Else fail `ps-runtime`.
2. Write `<run>\cli-preflight.txt` containing exactly `PREFLIGHT-OK`.
3. Invoke: `--add-dir <skill-root> -p` with a short prompt to read that file and reply with its contents.
4. Success: exit 0 and stdout contains `PREFLIGHT-OK`.
5. Failure: skip remaining **`agy`/`gemini` seats only** (`failures.md` reason `cli-preflight-agy`); do **not** skip `codex` seats.

Codex has its own preflight below — families fail independently.

### Invoke (review-only) — ProcessStartInfo, short -p, `--add-dir`, UTF-8

Keep `-p` **short**: tell `agy` to read the absolute paths in `seat-N-prompt.md` and emit the Council envelope. Do **not** put `artifact.md` on the command line (Windows argv limit ~32k).

**Required `--add-dir` — this is not optional, and it is the #1 cause of seat
failures when skipped (confirmed by direct reproduction 2026-09-06: reading a
file inside a real git project without `--add-dir` on that project's root
fails with `read_file ... auto-denied`, even though the process's own cwd
already is that project; a scratch/non-project path does not need this at
all — it's project-workspace trust gating specifically):**

- Always: absolute skill root (`…\.cursor\skills\council`)
- **Always, not just "when present":** every review root the seat's prompt
  points at — read `meta.md` / `request.md` for these paths **before**
  building `$addDirs`, and add one `--add-dir` per distinct root. A seat
  whose packet mentions a live repo path (e.g. "Live repo (review root,
  read as needed...): D:\...") and doesn't get that path in `--add-dir`
  will fail exactly this way, deterministically, every time.
- Still never `--dangerously-skip-permissions`

Optional config `model` / `effort`: add `--model <value>` and/or `--effort <value>` before `-p`.

**PowerShell runtime:** Prefer `ProcessStartInfo.ArgumentList` (one argv entry per token). If `ArgumentList` is missing (Windows PowerShell 5.1 / .NET Framework), re-run the identical recipe under `pwsh -NoProfile`. If neither works, fail `ps-runtime`. Do **not** use `Start-Process -ArgumentList` (space-splits on Windows → interactive “What would you like me to read?”).

Do **not** merge streams with `2>&1` or trust `$LASTEXITCODE` after a redirect.

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
$agy = (Get-Command agy).Source
$skillRoot = "C:\Users\<You>\.cursor\skills\council"  # absolute; from meta.md / Paths
$run = Join-Path $skillRoot "runs\<timestamp>"
$promptFile = Join-Path $run "seat-N-prompt.md"
$outFile = Join-Path $run "seat-N-stdout.txt"
$errFile = Join-Path $run "seat-N-stderr.txt"
$metaFile = Join-Path $run "seat-N-argv-meta.txt"
$utf8 = New-Object System.Text.UTF8Encoding $false
$shortPrompt = "Read $promptFile and the absolute paths it lists. Return only the Council sentinel envelope (see inlined reviewer-envelope.md / reviewer-prompt.md). Do not write workspace files."
$timeoutMs = 180000  # config cli_timeout_sec * 1000
# REQUIRED, not optional: read meta.md / request.md for every review-root path
# this seat's prompt references (e.g. a live repo directory) and list each one
# here. Omitting a root the prompt tells the seat to read WILL fail with
# read_file ... auto-denied — confirmed by direct reproduction 2026-09-06.
$reviewRoots = @()  # populate from meta.md before dispatch — do not leave empty if request.md names any path
$addDirs = @($skillRoot) + $reviewRoots
$argTokens = [System.Collections.Generic.List[string]]::new()
foreach ($d in $addDirs) { $argTokens.Add('--add-dir'); $argTokens.Add($d) }
# if config model/effort set:
# $argTokens.Add('--model'); $argTokens.Add($model)
# $argTokens.Add('--effort'); $argTokens.Add($effort)
$argTokens.Add('-p'); $argTokens.Add($shortPrompt)

function Invoke-AgyWithArgs([string]$fileName, [string[]]$tokens) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $fileName
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $psi.StandardOutputEncoding = $utf8
  $psi.StandardErrorEncoding = $utf8
  foreach ($t in $tokens) { $psi.ArgumentList.Add($t) }
  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  $proc.Start() | Out-Null
  $outTask = $proc.StandardOutput.ReadToEndAsync()
  $errTask = $proc.StandardError.ReadToEndAsync()
  $timedOut = $false
  if (-not $proc.WaitForExit($timeoutMs)) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    $timedOut = $true
  }
  try { [void]$outTask.Wait() } catch {}
  try { [void]$errTask.Wait() } catch {}
  [System.IO.File]::WriteAllText($outFile, $(if ($outTask.Result) { $outTask.Result } else { '' }), $utf8)
  [System.IO.File]::WriteAllText($errFile, $(if ($errTask.Result) { $errTask.Result } else { '' }), $utf8)
  return @{ ExitCode = $proc.ExitCode; TimedOut = $timedOut; Pid = $proc.Id }
}

$hasArgList = $null -ne ([System.Diagnostics.ProcessStartInfo]::new().PSObject.Properties['ArgumentList'])
$runtime = 'ArgumentList'
if (-not $hasArgList) {
  $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
  if (-not $pwsh) { throw 'ps-runtime: no ArgumentList and no pwsh' }
  $runtime = 'pwsh'
  # Re-invoke this script body under pwsh, or build args via pwsh -NoProfile -Command with ProcessStartInfo there
}
$result = Invoke-AgyWithArgs -fileName $agy -tokens $argTokens.ToArray()
# Include configured role when present (e.g. role=breadth-auditor)
$meta = @(
  "agy=$agy",
  "ps=$($PSVersionTable.PSVersion)",
  "runtime=$runtime",
  "add-dir=$($addDirs -join ';')",
  "role=<from config or empty>",
  "timeout_ms=$timeoutMs",
  "exit=$($result.ExitCode)",
  "timed_out=$($result.TimedOut)"
) -join "`n"
[System.IO.File]::WriteAllText($metaFile, $meta, $utf8)
# Stall check-in: while process alive, every stall_checkin_sec emit "Seat N still running, <elapsed>s elapsed" (process-alive only).
```

If stdout looks like an interactive ask (“What would you like me to read?”) rather than a Council envelope, treat it as a failed seat (`unusable stdout`), not success.

If stderr contains `permission check failed` or `user denied permission` for `read_file`, treat as `permission-gate` (almost always missing `--add-dir`). Do not “fix” with `--dangerously-skip-permissions`.

**Seat failure (skip + continue)** when any of:

- `Get-Command` cannot find the binary
- stderr contains authentication required / timed out / login rejected
- stderr contains `permission check failed` / `user denied permission` (`permission-gate`)
- `ps-runtime` (no ArgumentList and no `pwsh`)
- `$code -ne 0`
- stdout file is missing or Trim() empty
- process killed after `cli_timeout_sec` (`timeout`)

Do **not** pass prompt text via `Invoke-Expression` or `cmd /c`.

## Codex CLI (GPT family)

Default GPT reviewer. Not a Cursor Task seat. Family: `gpt` (same-model skip vs ChatGPT/Codex seat 0).

```yaml
- kind: cli
  command: codex
  label: Codex CLI (GPT)
  model: gpt-5.6-sol          # Codex-native id (not gpt-5.6-sol-medium)
  effort: medium              # optional → -c model_reasoning_effort="medium"
```

### Windows launcher resolve (once per run)

`Get-Command codex` often yields `codex.ps1` under `%APPDATA%\npm`. `ProcessStartInfo` cannot run `.ps1` directly; bare PATH `codex` may hit a **different** WindowsApps build. Resolve **once** at first Codex need; store in `meta.md` / argv-meta; reuse for login status, preflight, and every dispatch.

1. If resolution yields a native `.exe` → `FileName = that.exe`; `ArgumentList` starts with `exec` …
2. Else if `%APPDATA%\npm\node_modules\@openai\codex\bin\codex.js` exists → `FileName = node.exe`; first arg = that `codex.js`; then `exec` …
3. Else → `FileName = pwsh`; args `-NoProfile`, `-File`, `<codex.ps1>`, then `exec` … as separate `ArgumentList` entries

Never set `FileName` to a `.ps1` alone. Never rely on bare PATH `codex` without recording which file was chosen.

Argv-meta **must** include: resolved `FileName`, underlying script/js when applicable, and `codex --version` from **that same launcher**.

```powershell
function Resolve-CodexLauncher {
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
  $cmd = Get-Command codex -ErrorAction SilentlyContinue
  $npmJs = Join-Path $env:APPDATA "npm\node_modules\@openai\codex\bin\codex.js"
  $node = (Get-Command node -ErrorAction SilentlyContinue)?.Source
  if ($cmd -and $cmd.Source -like '*.exe') {
    return @{ FileName = $cmd.Source; PrefixArgs = @(); Script = '' }
  }
  if ((Test-Path -LiteralPath $npmJs) -and $node) {
    return @{ FileName = $node; PrefixArgs = @($npmJs); Script = $npmJs }
  }
  if ($cmd -and $cmd.Source -like '*.ps1') {
    $pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue)?.Source
    if (-not $pwsh) { throw 'codex-resolve: ps1 found but no pwsh' }
    return @{ FileName = $pwsh; PrefixArgs = @('-NoProfile','-File',$cmd.Source); Script = $cmd.Source }
  }
  throw 'codex-resolve: no launcher'
}
```

Capture version with the same launcher (e.g. prefix + `--version`). Record login mode from `codex login status` (no secrets). When status reports `Logged in using ChatGPT`, ChatGPT/Codex quota applies — do not claim unconditional “outside all Cursor billing” for every auth mode.

### Install and sign in

Install Codex CLI per OpenAI docs. In a **normal** terminal run `codex login` (ChatGPT browser flow). Never paste API keys into Council chat. For automation-only API key login the installed CLI documents `OPENAI_API_KEY` with `codex login --with-api-key` — do not invent or recommend other undocumented key env var names.

### CLI preflight (Codex family)

Before the **first** `codex` seat (and on reconfigure probe for Codex), with the **resolved** launcher:

1. Capture version via that launcher; fail `codex-resolve` if resolve throws.
2. Confirm login usable via same launcher (`login status`); record mode in argv-meta.
3. Write `<run>\cli-preflight.txt` = `PREFLIGHT-OK`.
4. Delete/truncate `-o` target path.
5. Run the full invoke recipe (below) with a short **positional** prompt to read `cli-preflight.txt` and reply with its contents.
6. Success: exit 0 and the **fresh** last-message file (mtime/size from this attempt) contains `PREFLIGHT-OK`.
7. **Write-deny:** under `-s read-only`, prompt the seat to create a file under the skill root and each `review_root`; require denial/failure (no successful write). Log `codex-write-deny` if a write succeeds.
8. Failure: skip remaining **`codex` seats only** (`failures.md` reason `cli-preflight-codex`); do **not** skip `agy`/`gemini`.

### Invoke — positional prompt, ephemeral, isolated, `-o`, tree-kill

Never pass `-p` for the prompt (`-p` / `--profile` selects a Codex profile). Prompt is the **final positional** argument only (or stdin `-` if you deliberately use stdin).

Always include: `-s read-only`, `--ephemeral`, `--ignore-user-config`, `--skip-git-repo-check`.
Never: `--dangerously-bypass-approvals-and-sandbox`.

```powershell
$launcher = Resolve-CodexLauncher  # once per run; reuse
$skillRoot = "C:\Users\<You>\.cursor\skills\council"
$run = Join-Path $skillRoot "runs\<timestamp>"
$promptFile = Join-Path $run "seat-N-prompt.md"
$outLast = Join-Path $run "seat-N-last-message.txt"
$outFile = Join-Path $run "seat-N-stdout.txt"
$errFile = Join-Path $run "seat-N-stderr.txt"
$metaFile = Join-Path $run "seat-N-argv-meta.txt"
$utf8 = New-Object System.Text.UTF8Encoding $false
$shortPrompt = "Read $promptFile and the absolute paths it lists. Return only the Council sentinel envelope (see inlined reviewer-envelope.md / reviewer-prompt.md). Do not write workspace files."
$timeoutMs = 180000
$addDirs = @($skillRoot, $run)  # plus review_roots from meta.md

# Stale-output guard: remove -o target before every attempt (including retries)
if (Test-Path -LiteralPath $outLast) { Remove-Item -LiteralPath $outLast -Force }
$attemptStart = Get-Date

$argTokens = [System.Collections.Generic.List[string]]::new()
foreach ($a in $launcher.PrefixArgs) { $argTokens.Add($a) }
$argTokens.Add('exec')
$argTokens.Add('-s'); $argTokens.Add('read-only')
$argTokens.Add('--ephemeral')
$argTokens.Add('--ignore-user-config')
$argTokens.Add('--skip-git-repo-check')
$argTokens.Add('-C'); $argTokens.Add($skillRoot)
foreach ($d in $addDirs) { $argTokens.Add('--add-dir'); $argTokens.Add($d) }
# if config model/effort set:
# $argTokens.Add('-m'); $argTokens.Add($model)
# $argTokens.Add('-c'); $argTokens.Add("model_reasoning_effort=`"$effort`"")
$argTokens.Add('-o'); $argTokens.Add($outLast)
$argTokens.Add($shortPrompt)  # FINAL positional — never -p

function Stop-ProcessTree([int]$ProcessId) {
  Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-ProcessTree -ProcessId $_.ProcessId }
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Invoke-CodexWithArgs([string]$fileName, [string[]]$tokens) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $fileName
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $psi.StandardOutputEncoding = $utf8
  $psi.StandardErrorEncoding = $utf8
  foreach ($t in $tokens) { $psi.ArgumentList.Add($t) }
  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  $proc.Start() | Out-Null
  $outTask = $proc.StandardOutput.ReadToEndAsync()
  $errTask = $proc.StandardError.ReadToEndAsync()
  $timedOut = $false
  if (-not $proc.WaitForExit($timeoutMs)) {
    Stop-ProcessTree -ProcessId $proc.Id
    $timedOut = $true
  }
  try { [void]$outTask.Wait() } catch {}
  try { [void]$errTask.Wait() } catch {}
  [System.IO.File]::WriteAllText($outFile, $(if ($outTask.Result) { $outTask.Result } else { '' }), $utf8)
  [System.IO.File]::WriteAllText($errFile, $(if ($errTask.Result) { $errTask.Result } else { '' }), $utf8)
  return @{ ExitCode = $proc.ExitCode; TimedOut = $timedOut; Pid = $proc.Id }
}

$result = Invoke-CodexWithArgs -fileName $launcher.FileName -tokens $argTokens.ToArray()

# Accept -o only if this attempt wrote it
$fresh = $false
if (Test-Path -LiteralPath $outLast) {
  $item = Get-Item -LiteralPath $outLast
  if ($item.LastWriteTime -ge $attemptStart -and $item.Length -gt 0) { $fresh = $true }
}
if (-not $fresh) { throw 'codex-stale-or-missing-last-message' }
# Parse Council envelope from $outLast (not noisy stdout)

$meta = @(
  "launcher=$($launcher.FileName)",
  "script=$($launcher.Script)",
  "version=<from same launcher --version>",
  "login_mode=<from login status; no secrets>",
  "ps=$($PSVersionTable.PSVersion)",
  "add-dir=$($addDirs -join ';')",
  "ephemeral=true",
  "ignore_user_config=true",
  "sandbox=read-only",
  "model=<config or empty>",
  "effort=<config or empty>",
  "timeout_ms=$timeoutMs",
  "exit=$($result.ExitCode)",
  "timed_out=$($result.TimedOut)",
  "last_message_fresh=$fresh"
) -join "`n"
[System.IO.File]::WriteAllText($metaFile, $meta, $utf8)
```

**Seat failure (skip + continue)** when any of: resolve failed; auth required/failed; non-zero exit; missing/stale last-message; timeout (tree-killed); write-deny check failed; unusable interactive ask; `ps-runtime`.

Parse the Council envelope from the fresh `-o` file. Do not treat noisy stdout as the artifact source when `-o` succeeded.

