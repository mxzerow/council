# Fallback lane: Cursor's CLI (`cursor-agent`)

```text
python -I <shared>/scripts/council_seat.py preflight --transport cursor --root <shared> --run <run> --model <slug>
python -I <shared>/scripts/council_seat.py run --transport cursor --root <shared> --run <run> --seat <n> --label <label> --model <slug> --prompt-file <prompt-file> --reply <reply-file>
```

Cursor's CLI can run the same models Council uses (Gemini Argon and Flash, Claude, GPT, Grok) on **Cursor's** quota. Use it when a seat's own CLI cannot run: `agy` out of quota, signed out, or unavailable. Common runner rules: [common.md](common.md). Tested live 2026-10-23 for `gemini` seats only; other families are plausible but untested.

- **Catalog:** `python -I scripts/council_resolve.py resolve ... --source cursor` (runs `cursor-agent --list-models`, about 265 models). This is the CLI's list, **not** the shorter list a Cursor *Task* seat is offered.
- **Sign-in:** in a normal terminal, `cursor-agent login`. Never paste credentials into chat.
- **Windows:** `cursor-agent` is a PowerShell script (`cursor-agent.ps1`), which a plain process launch cannot run, so the runner launches it through PowerShell. Use the PowerShell tool, not Git Bash: Git Bash sets `MSYSTEM`, and Cursor's hook importer then breaks every call (see the project note on the Cursor CLI hook bug).

## Calls

The runner sends the builder's inline prompt (or pointer delivery) using ask mode and an empty workspace. For rewrites add `--kind rewrite --stem rewrite/chunk-<i>` with the chunk prompt file. It extracts `.result`, records `.usage`, and treats `.is_error: true` as failure. Preflight requires `PREFLIGHT-OK` and exit 0. Authentication/model failures use the common classes.

On Windows cursor-agent.ps1 is launched through PowerShell by the script. Never run cursor-agent from Git Bash: MSYSTEM triggers the documented hook-import bug. The runner strips MSYSTEM on Windows; that mitigation remains unverified live.

The runner prefers `pwsh`. When only Windows PowerShell 5.1 is available, the summary includes a `cursor-ps51` warning: embedded double quotes in prompts may be garbled by node-based launchers. Install PowerShell 7 (`pwsh`) for reliable argument forwarding; the runner keeps the same argv quoting and permission settings.

## Measured (2026-10-23, same packets as the `agy` trials)

| Call | Model | Input tokens | API time | Result |
|---|---|---|---|---|
| trivial "reply OK" | Gemini 3.8 Flash low | 15,008 | 5-6 s | OK |
| breadth review, 2.5 KB packet, inline | Gemini 4 Argon medium | 17,034 | 24 s | parsed; 8 blocking + 2 optional; 5/5 planted flaws |
| rewrite chunk, inline | Gemini 3.8 Flash low | 17,243 | 16 s | passed every check on the first try |

The fixed overhead (about 15k tokens per call) is the same as `agy`, so the same rules apply: inline prompts, low effort for mechanical work, one call per seat.

## When to use it

`agy` first (its quota is separate and free of Cursor billing); fall back here on a seat-level failure of `agy` (`quota`, `auth`, `network`, `timeout`, no catalog). It spends Cursor usage, so say so when it is used.
