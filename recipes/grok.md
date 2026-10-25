# `grok` seats: Grok CLI (`agent`)

```text
python -I <shared>/scripts/council_seat.py preflight --transport grok --root <shared> --run <run> --model <slug>
python -I <shared>/scripts/council_seat.py run --transport grok --root <shared> --run <run> --seat <n> --label <label> --model <slug> --prompt-file <prompt-file> --reply <reply-file>
```

Family `grok`. Common runner rules: [common.md](common.md).

On this machine `agent` is the **Grok Build CLI** (`~/.grok/bin/agent.exe`, `grok 1.0.62` when verified 2026-10-23), not Cursor's `agent`. The old Cursor flags (`--mode plan --trust --workspace`) fail with `unexpected argument '--mode'`; do not use them.

- **Catalog:** `agent models` (prints "You are not authenticated" yet headless calls work; lists e.g. `grok-4.7`). Resolve the newest version of the seat's `line` from it.

## Calls and preflight

The runner always uses pointer delivery, `--permission-mode plan`, JSON output, run cwd and 12 maximum turns. Plan mode is read-only (verified by a write probe on 2026-10-23); never drop it. It extracts `.text`, which may contain a preamble before the sentinel. Preflight reads cli-preflight.txt and requires PREFLIGHT-OK with exit 0; failure logs cli-preflight-agent. Read only the redacted summary for failure details.
