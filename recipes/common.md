# Seat dispatch: common rules

```text
python -I <shared>/scripts/council_seat.py run --transport T --root <shared> --run <run> --seat N --label L --model SLUG --prompt-file FILE --reply OUT
python -I <shared>/scripts/council_seat.py preflight --transport T --root <shared> --run <run> --model SLUG
python -I <shared>/scripts/council_seat.py skip --run <run> --seat N --label L --reason REASON --detail TEXT
```

Use `scripts/council_seat.py`. Do not write ad-hoc wrapper scripts.
Seat failures are logged to failures.md by the script; invalid arguments exit 2 without a failure entry. Concurrent appends preserve each UTF-8, LF-terminated record; Windows serializes them with `failures.md.lock`. The script holds dispatch logic on Windows, macOS and Linux; recipes hold installation, catalog and measurement notes.

- In Claude Code on Windows, use the **PowerShell tool**. Refresh PATH once per session:
  `$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")`
- Never pass secrets, `.env` contents or credentials in a prompt. Sign in in the user's own terminal; never ask for keys in chat.
- Build prompts with `council_prompt.py`. Translate builder `mode: file` to runner `--delivery pointer`; `inline` to `--delivery inline`. Codex and Grok always use pointer delivery. API always builds inline and reads the file.
- Optional run flags: `--kind review|rewrite`, `--effort E`, repeated `--add-dir D`, `--timeout SEC`, `--stem NAME`, `--cwd DIR`. `--reply` must resolve inside the run folder. For chunks use `--kind rewrite --stem rewrite/chunk-<i>`; the runner appends the rewrite tail. Do not append it yourself.
- Exactly one dispatch per invocation. The harness owns retries, resolver calls, mark-good/mark-rejected, the two-dispatch rule and the API fallback table in core.md.

## Summary and exit codes

Run prints exactly one JSON line with `status` (`ok|failed|refused`), `class`, `transport`, `seat`, `model`, `exit`, `timed_out`, `seconds`, `usage`, `reply`, `free_tier_warning`, `detail`. Read this summary, then parse the reply file on success. Exit 0 = ok, 1 = seat failure, 2 = usage/IO/refusal. A failed call leaves the reply file untouched. Preflight prints `status`, `class`, `transport`, `version`, `launcher`, `seconds`, `detail`; exit 0/1 for transport outcomes. Skip prints `{"status":"skipped"}` and exits 0; invalid arguments exit 2.

CLI timeout defaults to `cli_timeout_sec` in local config, then shared config, then 600 seconds (30..3600). API uses its own configured timeout; the runner allows an additional 120 seconds for subprocess overhead. The whole process tree is killed at the hard limit. In Claude Code the foreground limit is 600 s: when timeout + 120 > 540, use `run_in_background: true` and resume on completion, without polling or sleeping. Otherwise give the foreground tool `(timeout + 120) * 1000` ms, including the API outer allowance.

## Working directory and files

The default child cwd is `<runs_dir>/_cwd`, created when missing. It should be empty: a trivial agy call measured 14,252 input tokens and 5 s there, against 15,434 and 10 s in a large repository (2026-10-23). Nothing is deleted if it is nonempty; meta records `cwd_not_empty: true`. `--cwd DIR` overrides it. Packet paths are absolute; add-dir grants are independent of cwd.

Per call: `<stem>-stdout.txt`, `<stem>-stderr.txt`, `<stem>-argv-meta.txt`; Codex also `<stem>-last.txt`. Default stem is `seat-<n>`. Meta contains redacted argv (prompt length and hash only), model, usage, cwd, timeout and exit. Cursor on Windows drops MSYSTEM and records `msystem_stripped`; a Windows PowerShell 5.1 fallback adds a `cursor-ps51` warning to the summary, without a failures.md entry. The unverified mitigation still needs the orchestrator's live rerun. API rewrite also saves `<stem>-api-prompt.md`. Prompt metadata still names seat, label, total, mode, family, role, open blocking IDs and absolute packet paths, never meta.md; reviewers are gaps only.

## Failures and preflight

Every failed run/preflight and every explicit skip appends exactly one UTF-8 LF line via council_parse.log_failure; success writes none:

```text
seat <n> (<label>): <transport>-<class> [<transport> <model>] (<detail>)
preflight (<transport>): cli-preflight-<name> (<class>: <detail>)
seat <n> (<label>): <reason> (<detail>)
```

API configuration/refusal uses `api-refused`. Grok preflight keeps `cli-preflight-agent`; other names are `agy|cursor|codex|api`. Details are redacted, collapsed to one line, home-shortened and capped at 160 characters (`-` when empty). Reasons for skip match `[a-z0-9][a-z0-9-]*`. Parser outcomes are already logged by council_parse.py: never log them again.

Classes include `not-found`, `ps-runtime`, `timeout`, `permission-gate`, `cursor-hook-bug`, `unusable-stdout`, `empty`, `exit-nonzero`, `argv-too-long`, and resolver/API classes such as `quota`, `auth`, `model-rejected`, `network`, `unavailable`, `config`. Never bypass permissions to fix a permission gate. Windows batch launchers are refused; inline argv over 30000 characters is refused before launch.

Before a transport's first seat, run its runner preflight with the first resolved model (API needs no model). The runner writes cli-preflight.txt, records one launcher version call, and exercises the real transport. CLI success requires PREFLIGHT-OK; Codex also must fail to create the write probe. A failed preflight skips remaining seats of that transport only.
