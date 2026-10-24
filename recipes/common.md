# Seat dispatch: common rules (Claude Code, PowerShell)

Read this once before the first CLI seat, then only the recipe for each transport the run uses (`agy.md`, `codex.md`, `grok.md`; `cursor.md` only as a fallback).

- Use the **PowerShell tool**, not Bash. Bash does not reliably see `agy`/`codex`/`agent` on PATH, and a Bash command whose text mentions an agent CLI is intercepted by the delegation hook. PowerShell needs its PATH refreshed once per session:
  `$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")`
- Never pass secrets, `.env` contents or credentials in a prompt. Never request API keys in chat; sign-in happens in the user's own terminal.
- Keep the CLI prompt **short**: tell the seat to read `seat-<n>-prompt.md` and the absolute paths it lists, and return only the Council envelope. Never put `artifact.md` on the command line (argv limit).
- Do not use `Start-Process -ArgumentList` (it space-splits), `Invoke-Expression`, `cmd /c`, or `2>&1` merging.

## Shared helper

```powershell
function Invoke-Seat([string]$exe, [string[]]$tokens, [int]$ms, [string]$outFile, [string]$errFile, [string]$cwd = "") {
  $utf8 = New-Object System.Text.UTF8Encoding $false
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $exe; $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
  if ($cwd) { $psi.WorkingDirectory = $cwd }               # see "Empty working directory" below
  $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
  $psi.StandardOutputEncoding = $utf8; $psi.StandardErrorEncoding = $utf8
  foreach ($t in $tokens) { $psi.ArgumentList.Add($t) }   # one argv entry per token
  $p = [System.Diagnostics.Process]::Start($psi)
  $p.StandardInput.Close()                                  # a CLI waiting on stdin hangs otherwise
  $o = $p.StandardOutput.ReadToEndAsync(); $e = $p.StandardError.ReadToEndAsync()
  $timedOut = $false
  if (-not $p.WaitForExit($ms)) { Stop-Tree $p.Id; $timedOut = $true }
  try { [void]$o.Wait() } catch {}; try { [void]$e.Wait() } catch {}
  [IO.File]::WriteAllText($outFile, [string]$o.Result, $utf8); [IO.File]::WriteAllText($errFile, [string]$e.Result, $utf8)
  @{ Exit = $p.ExitCode; TimedOut = $timedOut }
}
function Stop-Tree([int]$id) {
  Get-CimInstance Win32_Process -Filter "ParentProcessId=$id" -EA 0 | ForEach-Object { Stop-Tree $_.ProcessId }
  Stop-Process -Id $id -Force -EA 0
}
```

If `ProcessStartInfo.ArgumentList` is missing (Windows PowerShell 5.1), run the same code under `pwsh -NoProfile`; if neither works, fail `ps-runtime`. Timeout = `cli_timeout_sec` from `config.yaml` (default 180, shipped 600) × 1000 ms.

## Empty working directory (`agy`)

Run `agy` with `$cwd` set to an **empty folder** (`<runs_dir>\_cwd`, create it if missing), not the project folder. Measured 2026-10-23: a trivial call cost 14,252 input tokens from an empty folder against 15,434 from a large git repo, and started in 5 s instead of 10 s. `--disable-slash-commands` changed nothing. The rest of the roughly 14k is the agent's own fixed prompt and cannot be reduced from outside. Pass packet paths as absolute paths; an empty working directory does not affect `--add-dir`.

## Files per seat (in the run folder)

`seat-<n>-prompt.md` (pointer, not the artifact), `seat-<n>-stdout.txt`, `seat-<n>-stderr.txt`, and an `seat-<n>-argv-meta.txt` with the executable, version, add-dir list, model/effort, timeout and exit (no secrets). Prompt metadata keys: `seat`, `label`, `total`, `mode`, `family`, `role`, the open blocking IDs (comma-separated), and absolute packet paths (`request.md`, `artifact.md`, `open-gaps.md` when present, `unresolved.md` only with real dissent; never `meta.md`). Say that the seat is **gaps only**.

## Failure classes (seat is skipped, run continues)

binary not found · authentication required or login rejected · `permission-gate` (stderr has `permission check failed` / `user denied permission`; almost always a missing `--add-dir`; never "fix" it with a permission-bypass flag) · `ps-runtime` · non-zero exit · empty stdout / missing last-message · interactive question instead of an envelope (`unusable stdout`) · `timeout` (process tree killed).

## Preflight

Before the first seat of each transport, write `<run>\cli-preflight.txt` containing exactly `PREFLIGHT-OK`, run the transport's real invoke recipe with a prompt to read that file and reply with its contents, and require exit 0 and `PREFLIGHT-OK` in the reply. A failure skips remaining seats of **that transport only** (`cli-preflight-agent` / `-agy` / `-codex` / `-cursor`).
