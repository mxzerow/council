# Gemini Developer API (opt-in Python transport)

```text
python -I <shared>/scripts/council_seat.py preflight --transport api --root <shared> --run <run>
python -I <shared>/scripts/council_seat.py run --transport api --root <shared> --run <run> --seat <n> --label <label> --model <slug> --prompt-file <prompt-file> --reply <reply-file>
```

This is a direct REST call using your own API key, quota and billing, independent of `agy` and Cursor. Python 3.10+; standard library only; always run with `python -I`. Enable it and choose its position in `gemini_transport` through `config.local.yaml`. Defaults keep it disabled. Key-file setup and privacy comparison: [README, Choosing how Gemini is called](../README.md#choosing-how-gemini-is-called).

## Preflight and config

```powershell
python -I "$shared\scripts\council_gemini_api.py" config --root $shared
python -I "$shared\scripts\council_gemini_api.py" preflight --root $shared
python -I "$shared\scripts\council_gemini_api.py" models --root $shared
```

`config` returns effective config JSON, resolved timeout, key source (`env|file|missing`) and key-file path, never the key. Local overrides merge per key. Invalid settings name the offending key and exit 2. `preflight` validates enabled, privacy acknowledgment and key, then GETs `/models?pageSize=1`; no generation. Failure skips only this transport (`cli-preflight-api`, plus `api-refused` for configuration refusal). `models` pages through the live catalog, retains `generateContent` entries and drops token variants `latest`, `preview`, `exp`, `experimental`. The resolver additionally excludes unintended `lite`, `image`, `tts`, `customtools` variants for a Flash seat.

The key is loaded from `COUNCIL_GEMINI_API_KEY`, then the effective `gemini_api.key_file`, then `~/.council/gemini.key`. There is no `--key` flag. Key files accept UTF-8 (with or without BOM) and BOM-marked UTF-16 LE/BE, contain one key line, and must match letters/digits/underscore/hyphen/dot (at least 20 characters). POSIX files must have no group/other permissions (`chmod 600`). Windows ACLs are documented in the README; the script never checks or changes them. The endpoint is fixed to `https://generativelanguage.googleapis.com/v1beta`; no endpoint flag or environment override is accepted, and redirects are refused. The key goes only in `x-goog-api-key`, never the URL. Errors are redacted and limited to 300 characters.

## Review call

Resolve with `council_resolve.py resolve --family gemini --line flash --source api --state <runs_dir>/_resolved.json`. Build reviews with `council_prompt.py build --run <run> --seat <n> --label <label> --family gemini --mode inline`, supplying role and total. Use council_seat.py run above as the dispatch entry point; it delegates to council_gemini_api.py and keeps stdout/stderr/meta itself. Never put prompt text in argv.

Read only the runner's one-line summary and parse its reply file on success. The API summary (status/class, HTTP, usage, finish reason, seconds, tier, maximum output, detail) is retained in argv-meta. Reply files stay untouched on failure. Surface free_tier_warning once per run. On success mark-good the slug.

The API uses `gemini_api.thinking_level` (default `high`), independent of the seat's effort. Do not forward the seat effort. An explicit API `--effort low|medium|high` overrides the config; `minimal` is invalid. It sends `generationConfig.thinkingConfig.thinkingLevel`; `includeThoughts` is not set. Reviews default to 32768 output tokens; rewrites to 65536. `--max-output` explicitly overrides with a positive integer, clamped to `outputTokenLimit` from a model GET before generation; config caps must be 1024..65536. No token optimisation or `service_tier` is sent. `service_tier` is a possible future option once verified for this endpoint.

## Tiers and long calls

Free defaults to **1200 seconds per call**, paid to **300**. `gemini_api.timeout_sec` overrides (integer 30..3600); `call --timeout` explicitly overrides (integer >=1, including offline tests). `--tier free|paid` overrides the tier and recomputes its default timeout unless a config timeout was set. There is no latency-based model fallback. Let every call run its full timeout.

Free refuses unless `free_tier_ack: true`. A free invocation prints exactly one stderr reminder:

```text
council: Gemini API free tier - Google may use prompts and replies to improve its products and humans may review them; do not send PHI or confidential text.
```

Capture this stderr and show the reminder once per council run. Paid has no reminder. In Claude Code the shell foreground limit is 600 s: if timeout + 120 > 540 s, use `run_in_background: true` and resume on the completion notification; never poll or sleep. Otherwise set foreground tool timeout to `(timeout + 120) * 1000` ms. The script enforces the socket timeout. The runner uses the API timeout plus 120 seconds as its outer hard limit; include that allowance in the shell tool budget.

## Failures and bounded fallback

| Class / condition | Harness action |
|---|---|
| `ok`, non-empty text | Mark good and parse |
| `empty`, including blocked/no candidates or thought-only/MAX_TOKENS without text | Retry once, same model; then next transport |
| `model-rejected`: 404 GET/POST or 400 model unsupported/not found | `mark-rejected --slug <slug> --state <state>` (7 days); resolve next-lower same line |
| `quota`: 429 | Resolve with repeatable `--exclude <slug>` for every slug excluded this run; no persistent cooldown |
| `unavailable`: 503 | `mark-rejected --slug <slug> --state <state> --days 1 --reason unavailable`; resolve next-lower same line |
| `network`: 500/502 | Retry once on same model; then next transport |
| `network`: URL/DNS/TLS/reset | Next transport |
| `timeout`: 504 or socket timeout | Next transport; never a lower model |
| `auth`: 401/403 | Next transport; no model step |
| `other`: other 400 or unclassified failure | Log and use next transport |
| `config`: disabled, missing ack/key, bad key/perms, usage/IO | `api-refused: <reason>`; next transport |

The runner logs each failed step in `failures.md` with `api-<class>` (configuration refusals use `api-refused`); do not duplicate it. At most `max_model_steps` step-downs (default 2), same family and line only. Pins never downgrade. Two dispatches per (seat, transport, model), total API generation calls per seat <= `2 * (1 + max_model_steps)` (including chunks/retries); exhausting this budget advances to the next transport.

## Rewrite chunks

```text
python -I <shared>/scripts/council_seat.py run --transport api --root <shared> --run <run> --seat <n> --label <label> --model <slug> --prompt-file <run>/rewrite/chunk-<i>-prompt.md --reply <run>/rewrite/chunk-<i>-reply.txt --kind rewrite --stem rewrite/chunk-<i>
```

The runner writes the chunk plus rewrite tail to `<run>/rewrite/chunk-<i>-api-prompt.md` and delegates with `--kind rewrite`. Do not append the tail yourself.

Run the existing chunk checker. Once chunk 1 succeeds, hold transport and model fixed. A later seat-level failure or exhausted budget restarts with `prepare` on the next transport/model, so no run mixes voices. Try remaining transports before the next rewriter seat. A failed chunk validation follows the rewrite checker's retry/fallback rules; it is not a reason to change the model.

## Measurements supplied with this lane (2026-10-24)

These are observations from the handoff, not runtime guarantees or new live tests: free 3.8 Flash took about 150–170 s to first token; 3.5 Flash about 1.2 s; 3.6 Flash returned 503; Argon returned 429 on free; 2.5 Flash returned 404. Paid is expected to be faster, but no paid latency was measured here. Tests for this implementation use a local fake server and synthetic data only.
