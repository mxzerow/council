# Grok seats: the Cursor CLI

```text
python -I <shared>/scripts/council_seat.py preflight --transport cursor --root <shared> --run <run> --model <slug>
python -I <shared>/scripts/council_seat.py run --transport cursor --root <shared> --run <run> --seat <n> --label <label> --model <slug> --prompt-file <prompt-file> --reply <reply-file>
```

Council uses the Cursor CLI only for Grok seats, in either origin. Config transport `cursor_cli` maps to runner `cursor` and resolver `cursor`. This spends Cursor quota. Gemini never routes through Cursor, and this transport is never a backup for another family. Common runner rules: [common.md](common.md).

- **Catalog:** `python -I scripts/council_resolve.py resolve ... --source cursor` (runs `cursor-agent --list-models`, about 265 models). This is the CLI's list, **not** the shorter list a Cursor *Task* seat is offered.
- **Sign-in:** in a normal terminal, `cursor-agent login`. Never paste credentials into chat.
- **Windows:** `cursor-agent` is a PowerShell script (`cursor-agent.ps1`), which a plain process launch cannot run, so the runner launches it through PowerShell. Use the PowerShell tool, not Git Bash: Git Bash sets `MSYSTEM`, and Cursor's hook importer then breaks every call (see the project note on the Cursor CLI hook bug).

## Calls

The runner sends the builder's inline prompt (or pointer delivery) using ask mode and an empty workspace. For rewrites add `--kind rewrite --stem rewrite/chunk-<i>` with the chunk prompt file. It extracts `.result`, records `.usage`, and treats `.is_error: true` as failure. Preflight requires `PREFLIGHT-OK` and exit 0. Authentication/model failures use the common classes.

On Windows cursor-agent.ps1 is launched through PowerShell by the script. Never run cursor-agent from Git Bash: MSYSTEM triggers the documented hook-import bug. The runner strips MSYSTEM on Windows; that mitigation remains unverified live.

The runner prefers `pwsh`. When only Windows PowerShell 5.1 is available, the summary includes a `cursor-ps51` warning: embedded double quotes in prompts may be garbled by node-based launchers. Install PowerShell 7 (`pwsh`) for reliable argument forwarding; the runner keeps the same argv quoting and permission settings.

## History

Historical Gemini measurements for the retired Cursor route are in git history.
