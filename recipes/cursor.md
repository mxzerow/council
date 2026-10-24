# Fallback lane: Cursor's CLI (`cursor-agent`)

Cursor's CLI can run the same models Council uses (Gemini Argon and Flash, Claude, GPT, Grok) on **Cursor's** quota. Use it when a seat's own CLI cannot run: `agy` out of quota, signed out, or unavailable. Common rules and the `Invoke-Seat` helper: [common.md](common.md). Tested live 2026-10-23 for `gemini` seats only; other families are plausible but untested.

- **Catalog:** `python -I scripts/council_resolve.py resolve ... --source cursor` (runs `cursor-agent --list-models`, about 265 models). This is the CLI's list, **not** the shorter list a Cursor *Task* seat is offered.
- **Sign-in:** in a normal terminal, `cursor-agent login`. Never paste credentials into chat.
- **Windows:** `cursor-agent` is a PowerShell script (`cursor-agent.ps1`), which a plain process launch cannot run, so call it through `pwsh -File`. Use the PowerShell tool, not Git Bash: Git Bash sets `MSYSTEM`, and Cursor's hook importer then breaks every call (see the project note on the Cursor CLI hook bug).

## Invoke (one call, no tools)

```powershell
$cur   = (Get-Command cursor-agent).Source                       # ...\cursor-agent.ps1
$empty = "$runsDir\_cwd"                                         # empty working directory
$tokens = @('-NoProfile','-File',$cur,'-p',$prompt,'--model',$slug,'--output-format','json',
            '--mode','ask','--trust','--workspace',$empty)
$r = Invoke-Seat (Get-Command pwsh).Source $tokens ($timeoutSec * 1000) $outFile $errFile $empty
```

- `$prompt` is the **inline** prompt from `council_prompt.py build` (or a rewrite chunk prompt plus its tail), exactly as for `agy`.
- `--mode ask` is read-only question answering; the seat edits nothing. `--workspace` is the empty folder, so there is nothing to scan.
- The reply is JSON: take `.result` (the parsers unwrap it); usage is in `.usage.inputTokens` / `.outputTokens`; `.is_error` is true on failure.
- **Preflight (`cli-preflight-cursor`):** a prompt `Reply with exactly: OK` must return `result` = `OK` with exit 0.
- **Failure classes:** as in common.md; also `not logged in` (run `cursor-agent login`), and a model slug not in the CLI's list.

## Measured (2026-10-23, same packets as the `agy` trials)

| Call | Model | Input tokens | API time | Result |
|---|---|---|---|---|
| trivial "reply OK" | Gemini 3.8 Flash low | 15,008 | 5-6 s | OK |
| breadth review, 2.5 KB packet, inline | Gemini 4 Argon medium | 17,034 | 24 s | parsed; 8 blocking + 2 optional; 5/5 planted flaws |
| rewrite chunk, inline | Gemini 3.8 Flash low | 17,243 | 16 s | passed every check on the first try |

The fixed overhead (about 15k tokens per call) is the same as `agy`, so the same rules apply: inline prompts, low effort for mechanical work, one call per seat.

## When to use it

`agy` first (its quota is separate and free of Cursor billing); fall back here on a seat-level failure of `agy` (`quota`, `auth`, `network`, `timeout`, no catalog). It spends Cursor usage, so say so when it is used.
