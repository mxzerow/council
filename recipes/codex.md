# `gpt` seats: Codex CLI

```text
python -I <shared>/scripts/council_seat.py preflight --transport codex --root <shared> --run <run> --model <slug>
python -I <shared>/scripts/council_seat.py run --transport codex --root <shared> --run <run> --seat <n> --label <label> --model <slug> --prompt-file <prompt-file> --reply <reply-file>
```

Family `gpt`. Common runner rules: [common.md](common.md). Not a Cursor Task seat.

- **Install / sign in:** install per OpenAI docs; in a normal terminal run `codex login` (ChatGPT browser flow). Record the mode from `codex login status`; `Logged in using ChatGPT` means ChatGPT/Codex quota applies. For automation-only API-key login use only what the CLI documents (`OPENAI_API_KEY` with `codex login --with-api-key`).
- **Catalog:** `~/.codex/models_cache.json`, read as **UTF-8**, entries with `visibility` `list` (also `codex` model listing). Codex-native ids, e.g. `gpt-6.1-sol`, not `gpt-5.6-sol-medium`; effort goes in `-c model_reasoning_effort="medium"`.

## Launcher and calls

The script resolves Windows launchers in order: native codex.exe, then node with `%APPDATA%/npm/node_modules/@openai/codex/bin/codex.js`, then codex.ps1 through PowerShell. POSIX uses codex from PATH. Batch files are refused; version comes from the same launcher.

The runner uses `exec -s read-only --ephemeral --ignore-user-config`, `--skip-git-repo-check`, shared root with `-C`, shared/run/review roots with `--add-dir`, and the prompt as the final positional argument. Add repeated `--add-dir <review-root>` and optional `--effort medium`. Never use `-p` (profile) or permission-bypass flags. On Windows it also passes `-c windows.sandbox="unelevated"`, required because ignoring user config drops that setting. Other systems omit it.

The script deletes stale last-message output before dispatch and accepts only the fresh nonempty `-o` file. Model rejection on stderr is a failure even if exit is 0.

## Preflight (`cli-preflight-codex`)

The runner asks Codex to read cli-preflight.txt and try creating `<shared>/.council-write-probe`, answering WRITE-DENIED or WRITE-CREATED. Success requires PREFLIGHT-OK in the fresh last-message file and no created probe. A created probe is removed and reported as `codex-write-deny`.
