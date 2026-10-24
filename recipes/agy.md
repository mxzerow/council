# `gemini` seats: Antigravity CLI (`agy`)

Family `gemini`. Common rules and the `Invoke-Seat` helper: [common.md](common.md).

- **Detect:** `Get-Command agy` (legacy `gemini` CLI only as a fallback; consumer Google login for it may be blocked after June 2026).
- **Install / sign in (no API key):** `irm https://antigravity.google/cli/install.ps1 | iex`, open a **new** terminal, run `agy`, choose **Google OAuth**. Binary: `%LOCALAPPDATA%\agy\bin\agy.exe`. Paste any auth code into that terminal, never into chat. Never set or request `GEMINI_API_KEY`.
- **Catalog:** `agy models` (tab-separated `slug<TAB>label`).

## Invoke (reviewer seats): inline by default

Build the prompt with the builder; do not assemble it by hand:

```powershell
$b = python -I "$shared\scripts\council_prompt.py" build --run $run --seat $n --label $label --family gemini --role $role --total $total | ConvertFrom-Json
if ($b.mode -eq 'inline') {
  # everything is in the prompt: one model call, no tools, no --add-dir
  $tokens = @('--model', $slug, '--output-format', 'json', '-p', [IO.File]::ReadAllText($b.prompt_file))
} else {
  # the packet did not fit on the command line: the seat reads the files itself (a second model call)
  $tokens = @('--add-dir', $shared, '--add-dir', $run) + ($reviewRoots | ForEach-Object { '--add-dir', $_ }) + @('--model', $slug, '--output-format', 'json', '-p', $b.pointer)
}
$r = Invoke-Seat $agy $tokens ($timeoutSec * 1000) $outFile $errFile "$runsDir\_cwd"      # empty working directory
```

- `auto` mode chooses inline when the whole prompt is at most 24,000 characters (the Windows command line allows about 32k), else file mode. Force one with `--mode inline|file`.
- The reply is JSON: take `.response` (the parsers unwrap it) and keep `.usage` in `seat-<n>-argv-meta.txt`.
- **`--add-dir` is required in file mode, not optional.** Headless `agy` auto-denies `read_file` for any file inside a recognized project or git workspace that was not passed with `--add-dir`, even when the working directory is that project (reproduced 2026-09-06). Pass the shared root and the run folder, **plus every review root** that `meta.md` / `request.md` names. A scratch or non-project path does not need it. Never pass `--dangerously-skip-permissions`, `--yolo` or `--approval-mode yolo`.
- `--workspace` and `--trust` no longer exist on `agy`, and `--print` followed by another flag needs the attached form `--print=<prompt>`; an argument-parsing error (not a permission one) means this drift.
- Failure classes: see common.md. Preflight: common.md, with `--model` set to the first configured `gemini` slug.

**Measured on the breadth review (Argon, 2026-10-23, one sample per cell):**

| Packet | Variant | Input tokens | Thinking | Time | Gaps | Coverage |
|---|---|---|---|---|---|---|
| 2.5 KB doc | file-read, medium (old) | 20,845 | 229 | 45 s | 9+2 | 5/5 planted flaws |
| | inline, medium | 15,520 | 967 | 41 s | 9+2 | 5/5 |
| | inline, low | 15,524 | 0 | 24 s | 9+2 | 5/5 |
| 8.5 KB plan | file-read, medium (old) | 21,539 | 3,601 | 77 s | 9+2 | 8/9 themes |
| | inline, medium | 17,364 | 2,420 | 63 s | 8+2 | 7/9 |
| | inline, low | 17,359 | 0 | 30 s | 9+2 | 7/9 |

Inline saves 19-25% of input. Low effort removes the thinking tokens and halves the time; coverage was the same within the noise of one sample (the 8/9 vs 7/9 is one keyword theme). The breadth seat stays at `medium` in `config.yaml` until more samples justify `low`.

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
$r = Invoke-Seat $agy $tokens ($timeoutSec * 1000) $outFile $errFile "$runsDir\_cwd"   # $outFile is JSON; pass it to council_rewrite.py check
```

- Use the rewriter seat's effort (`-low` model variant). Rewriting is mechanical; on the same real chunk, low and medium both passed the validator, and low added fewer explanations (25 against 34).
- The whole prompt is on the command line (Windows limit about 32k characters). `council_rewrite.py prepare` prints `inline_ok`; if it is `false`, use `--max-chars` smaller or fall back to the file-read form for that run (`--add-dir <run>` and `-p "Read <chunk prompt> and do exactly what it says. ..."`).
- An occasional empty reply happens (seen: two in a row once, then fine). Treat empty output as a seat failure: classify stderr, retry once, then move down the rewriter chain.

## Evaluated and not adopted (2026-10-23)

- **Automating the Gemini web chat** (a browser bridge, or a headless Playwright script against `gemini.google.com/app`): not built or run. `https://gemini.google.com/robots.txt` says `User-agent: *` / `Disallow: /app/` and `Disallow: /chat/`; Google's Terms of Service prohibit "using automated means to access content from any of our services in violation of the machine-readable instructions" and "bypassing our systems or protective measures", and allow suspending or deleting the Google Account. The suggested script also launches with `--disable-blink-features=AutomationControlled`, which exists to hide automation from Google's detection. The account at risk is the one your Gemini subscription and `agy` login use. Separately, the web chat keeps a subset of conversations for human review for up to three years and uses activity for training unless Keep Activity is off ("don't enter confidential information that you wouldn't want a reviewer to see"), and there is no business data agreement, so it is unsuitable for project text that may contain protected information. A model telling you it is fine does not change Google's terms.
- **Direct Gemini API transport** (own key): now available, opt-in; see [api.md](api.md). It has independent quota, billing and privacy settings. The free tier requires an explicit acknowledgment and must never receive PHI or confidential text. Provision the key locally using the README instructions, never in chat or committed files.
- **`thinkingBudget` in `~/.gemini/settings.json`:** that is the older Gemini CLI's file; `agy` has `--effort` and `-low`/`-medium` model variants, which already control it.
- **Context caching:** an API feature; Council never re-sends a large shared document, so there is nothing to cache.
