# `grok` seats: Grok CLI (`agent`)

Family `grok`. Common rules and the `Invoke-Seat` helper: [common.md](common.md).

On this machine `agent` is the **Grok Build CLI** (`~/.grok/bin/agent.exe`, `grok 1.0.62` when verified 2026-10-23), not Cursor's `agent`. The old Cursor flags (`--mode plan --trust --workspace`) fail with `unexpected argument '--mode'`; do not use them.

- **Catalog:** `agent models` (prints "You are not authenticated" yet headless calls work; lists e.g. `grok-4.7`). Resolve the newest version of the seat's `line` from it.
- **Invoke:**

```powershell
$tokens = @('-p', "Read $promptFile and the absolute paths it lists. Return only the Council sentinel envelope. Do not write workspace files.",
            '--model', $slug, '--permission-mode', 'plan', '--output-format', 'json', '--cwd', $run, '--max-turns', '12')
$r = Invoke-Seat $agentExe $tokens ($timeoutSec * 1000) $outFile $errFile
```

- `--permission-mode plan` is read-only (a write probe created no file, verified 2026-10-23); never drop it for a review run. `--cwd $run` lets the seat read the packet files. stdin must be closed (the helper does).
- The reply is the JSON `.text` field; the parser unwraps it. It may carry a sentence of preamble before the first sentinel (stripped, not an error).
- **Preflight (`cli-preflight-agent`):** run the invoke with the preflight prompt; success = exit 0, JSON parses, `.text` contains `PREFLIGHT-OK`. On failure show the user the raw error.
