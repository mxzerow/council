# Council model catalog (Cursor Task seats)

Used by the Cursor harness for [Family slug resolve](SKILL.md#family-slug-resolve) and for Reconfigure. The live Task `model` enum is authoritative; this table is a **preference and tie-break list**. **Do not reorder it to mirror a roster:** within a family, row order is the tie-break when version and effort scores are equal (earlier row wins).

Resolve by **provider family** and the seat's `line`, never by exact slug alone. Skip only when no family member is listed (`rejected-family`). Log `family-remap` when the dispatched slug differs from a `pin`. Do not remap across families or lines. Do not retry a slug after Task rejects it.

| Label | `model` slug | Family | Line | Notes |
|-------|--------------|--------|------|-------|
| Gemini 4 Argon Medium | `gemini-4-argon-medium` | `gemini` | argon | Default breadth / final-pass seat |
| Claude Opus 5.5 Medium | `claude-opus-5-5-medium` | `claude` | opus | Default Claude seat and the integration stage |
| Claude Opus 5 Thinking High | `claude-opus-5-thinking-high` | `claude` | opus | Older Opus |
| Claude Sonnet 5 Thinking Medium | `claude-sonnet-5-thinking-medium` | `claude` | sonnet | Optional |
| Claude Sonnet 5 Thinking High | `claude-sonnet-5-thinking-high` | `claude` | sonnet | Optional |
| GPT 5.6 Sol Medium | `gpt-5.6-sol-medium` | `gpt` | sol | Default GPT seat in Cursor (Claude Code uses the Codex CLI) |
| Cursor Grok 4.6 Medium | `cursor-grok-4.6-medium` | `grok` | grok | Prefer other `cursor-grok-4.6-*` before older Grok |
| Cursor Grok 4.6 High | `cursor-grok-4.6-high` | `grok` | grok | Same family and version as 4.6 Medium |
| Cursor Grok 4.6 High Fast | `cursor-grok-4.6-high-fast` | `grok` | grok | Same family and version as 4.6 Medium |
| Cursor Grok 4.5 High | `cursor-grok-4.5-high-fast` | `grok` | grok | Older generation; only when no `4.6` exists |
| Composer 2.5 Fast | `composer-2.5-fast` | `composer` | composer | Usually listed |

The initial draft is always the current chat model; it is not a catalog seat. Map it to a family for same-model skip.
