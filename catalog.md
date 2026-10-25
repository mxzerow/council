# Council model catalog (Cursor Task seats)

What a Cursor session's **Task tool** offered on 2026-10-23, as reported by the Cursor agent itself. The Task tool's own list is authoritative and is resolved by `scripts/council_resolve.py` ([core.md](core.md#model-resolution)); this table only documents what has been seen.

**Do not use `cursor-agent --list-models` as the Task list:** it prints about 265 models (for example Gemini 3.8 Flash and GPT-5.6 Sol High), far more than the Task tool accepts.

| Slug | Family | Line | Notes |
|------|--------|------|-------|
| `gemini-4-argon-medium` | `gemini` | argon | Not used: Gemini is never routed through Task; catalog fact only |
| `claude-opus-5-5-medium` | `claude` | opus | Seat 0 and the integration stage |
| `claude-sonnet-5-5-high` | `claude` | sonnet | |
| `claude-fable-5-1-thinking-high` | `claude` | fable | |
| `gpt-5.6-sol-medium` | `gpt` | sol | Default GPT reviewer in Cursor (Claude Code uses the Codex CLI and gets 6.1) |
| `cursor-grok-4.6-medium` | `grok` | grok | |
| `grok-4.7-medium` | `grok` | grok | Newer Grok; the resolver prefers it |
| `muse-wonder-1.0-high` | none | | Not a Council family; ignored |

The default rewrite chain is Gemini Flash (api, then agy, via the shell), then Claude Sonnet (Task). **No Gemini Flash is offered by Task**, which does not affect Flash's shell routes. Gemini is never routed through Cursor/Task, including the Argon slug listed above. Claude Sonnet is offered as `claude-sonnet-5-5-high`.

The initial draft is always the current chat model (or an Opus Task seat); it is not a catalog seat.
