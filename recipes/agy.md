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

## Cost: what a call really costs (measured 2026-10-23)

`agy -p ... --output-format json` prints `{"response": "...", "usage": {"input_tokens", "output_tokens", "thinking_tokens", ...}, "num_turns", "duration_seconds"}`. Use it for every call: take the reply from `.response` (the parsers unwrap it) and log `.usage` in `seat-<n>-argv-meta.txt`.

- **Fixed overhead:** a trivial "reply OK" costs about **15.4k input tokens**. That is the agent's own system prompt and tool set, paid on every model call.
- **A file read is a second call.** Asking `agy` to read a file (`Read <path> ...`) costs a tool round trip inside the same turn, so input tokens double (34k against 17.7k for the same 9 KB chunk) even though `num_turns` still says 1.
- **Thinking can spike.** The identical medium-effort file-read call once spent **13.9k thinking tokens and 100 s**; the same task inline, or at low effort, spent 0 and took about 19 s. Only one sample each, but the spike is real and avoidable.

## Rewriter chunk calls (inline, low effort)

Put the chunk **in the prompt**; do not ask the model to read it:

```powershell
$text  = [IO.File]::ReadAllText("$run\rewrite\chunk-$i-prompt.md")
$tail  = "`n`nDo not use any tools or read any files. Output only the rewritten chunk followed by the final REWRITE_END line."
$tokens = @('--model', $slug, '--output-format', 'json', '-p', ($text + $tail))     # no --add-dir needed: nothing is read
$r = Invoke-Seat $agy $tokens ($timeoutSec * 1000) $outFile $errFile                # $outFile is JSON; pass it to council_rewrite.py check
```

- Use the rewriter seat's effort (`-low` model variant). Rewriting is mechanical; on the same real chunk, low and medium both passed the validator, and low added fewer explanations (25 against 34).
- The whole prompt is on the command line (Windows limit about 32k characters). `council_rewrite.py prepare` prints `inline_ok`; if it is `false`, use `--max-chars` smaller or fall back to the file-read form for that run (`--add-dir <run>` and `-p "Read <chunk prompt> and do exactly what it says. ..."`).
- An occasional empty reply happens (seen: two in a row once, then fine). Treat empty output as a seat failure: classify stderr, retry once, then move down the rewriter chain.
