# `gemini` seats: Antigravity CLI (`agy`)

Family `gemini`. Common rules and the `Invoke-Seat` helper: [common.md](common.md).

- **Detect:** `Get-Command agy` (legacy `gemini` CLI only as a fallback; consumer Google login for it may be blocked after June 2026).
- **Install / sign in (no API key):** `irm https://antigravity.google/cli/install.ps1 | iex`, open a **new** terminal, run `agy`, choose **Google OAuth**. Binary: `%LOCALAPPDATA%\agy\bin\agy.exe`. Paste any auth code into that terminal, never into chat. Never set or request `GEMINI_API_KEY`.
- **Catalog:** `agy models` (tab-separated `slug<TAB>label`).

## Invoke (review-only)

```powershell
$tokens = @('--add-dir', $skillRoot, '--add-dir', $run)
foreach ($r in $reviewRoots) { $tokens += @('--add-dir', $r) }
$tokens += @('--model', $slug)            # optionally also '--effort', $effort
$tokens += @('-p', "Read $promptFile and the absolute paths it lists. Return only the Council sentinel envelope. Do not write workspace files.")
$r = Invoke-Seat $agy $tokens ($timeoutSec * 1000) $outFile $errFile
```

**`--add-dir` is required, not optional.** Headless `agy` auto-denies `read_file` for any file inside a recognized project or git workspace that was not passed with `--add-dir`, even when the process's own working directory is that project (reproduced 2026-09-06). Always pass the skill root and the run folder, **plus every review root** that `meta.md` / `request.md` names; one `--add-dir` per distinct root. A scratch or non-project path does not need it. Never pass `--dangerously-skip-permissions`, `--yolo` or `--approval-mode yolo`.

`--workspace` and `--trust` no longer exist on `agy`, and `--print` followed by another flag needs the attached form `--print=<prompt>`; an argument-parsing error (not a permission one) means this drift.

Reply = stdout. Failure classes: see common.md. Preflight: common.md, with `--model` set to the first configured `gemini` slug.

## Rewriter chunk calls

Same invoke, with `-p "Read <run>\rewrite\chunk-<i>-prompt.md and do exactly what it says. Output only the rewritten chunk followed by the final REWRITE_END line. Do not write any files."` and `--add-dir <run>`. Save stdout to a file and pass it to `council_rewrite.py check`. Verified live 2026-10-23 with `gemini-3.8-flash-medium`: an occasional empty reply happens (two in a row once, then fine); treat empty output as a seat failure, classify stderr, and retry once.
