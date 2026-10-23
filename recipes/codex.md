# `gpt` seats: Codex CLI

Family `gpt`. Common rules and the `Invoke-Seat` helper: [common.md](common.md). Not a Cursor Task seat.

- **Install / sign in:** install per OpenAI docs; in a normal terminal run `codex login` (ChatGPT browser flow). Record the mode from `codex login status`; `Logged in using ChatGPT` means ChatGPT/Codex quota applies. For automation-only API-key login use only what the CLI documents (`OPENAI_API_KEY` with `codex login --with-api-key`).
- **Catalog:** `~/.codex/models_cache.json`, read as **UTF-8**, entries with `visibility` `list` (also `codex` model listing). Codex-native ids, e.g. `gpt-6.1-sol`, not `gpt-5.6-sol-medium`; effort goes in `-c model_reasoning_effort="medium"`.

## Launcher (resolve once per run, reuse everywhere)

`Get-Command codex` often yields `codex.ps1`, which `ProcessStartInfo` cannot run, and bare PATH `codex` may hit a different WindowsApps build. Resolve in this order and record the choice and `--version` from **that** launcher in argv-meta:

1. native `.exe` → `FileName = that.exe`
2. `%APPDATA%\npm\node_modules\@openai\codex\bin\codex.js` exists → `FileName = node.exe`, first arg = that `codex.js`
3. `codex.ps1` → `FileName = pwsh`, args `-NoProfile -File <codex.ps1>`

Never set `FileName` to a `.ps1` alone. The prefix args come before `exec`.

## Invoke

```powershell
if (Test-Path $outLast) { Remove-Item $outLast -Force }   # stale-output guard, every attempt incl. retries
$tokens = $launcher.PrefixArgs + @('exec', '-s', 'read-only', '--ephemeral', '--ignore-user-config',
  '-c', 'windows.sandbox="unelevated"', '--skip-git-repo-check', '-C', $skillRoot,
  '--add-dir', $skillRoot, '--add-dir', $run, '-m', $slug, '-c', 'model_reasoning_effort="medium"',
  '-o', $outLast, "Read $promptFile and the absolute paths it lists. Return only the Council sentinel envelope. Do not write workspace files.")
$r = Invoke-Seat $launcher.FileName $tokens ($timeoutSec * 1000) $outFile $errFile
```

- The prompt is the **final positional** argument. Never `-p` (that is `--profile`).
- `-c windows.sandbox="unelevated"` is **required** with `--ignore-user-config`; without it the Windows sandbox setting is dropped and every shell read is rejected as "blocked by policy" (verified 2026-10-23).
- Add one `--add-dir` per review root. Never `--dangerously-bypass-approvals-and-sandbox`.
- The reply is the **fresh** `-o` last-message file (written during this attempt, non-empty), not the noisy stdout.
- A model the server rejects can still exit 0: if the reply is missing or `model` errors appear in stderr, tell the user; do not silently fall back to another model.

## Preflight (`cli-preflight-codex`)

Capture the version via the resolved launcher; confirm login usable; run the invoke recipe with a prompt to read `cli-preflight.txt` and reply with its contents (plus: try to create a file under the skill root and say `WRITE-DENIED` or `WRITE-CREATED`). Success: exit 0, fresh `-o` contains `PREFLIGHT-OK`, and no file was created. A write that succeeds is `codex-write-deny`.
