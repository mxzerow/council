# Council

A sequential **multi-model review** for [Cursor](https://cursor.com) and [Claude Code](https://docs.anthropic.com/en/docs/claude-code), run with `/council`. Both harnesses share one set of rules ([`core.md`](core.md)); only the way a model is called differs.

## How a run works

1. **Draft (seat 0).** Opus writes the draft, or you supply one.
2. **Review, gaps only.** Reviewers from other model families (default: Gemini Argon, then GPT Sol) each read the draft and report gaps. They never rewrite it.
3. **Integrate, once.** Opus resolves every gap (accept or reject with a reason) and writes the revision. Anything it rejects is printed at the end, with its reason.
4. **Rewrite, once.** A writer model polishes the result for a reader who is not a specialist, in small chunks, adding short explanations of jargon. The writers are an ordered fallback chain (default: Gemini Argon, then Gemini Flash, then Claude Sonnet); the first that works does the whole rewrite. A script checks every chunk and keeps the original wording of any chunk that fails.

There is no parallel first-answers pass and no chairman synthesis. See [`core.md`](core.md#stages).

## What you need

| Piece | Requirement |
|-------|-------------|
| Orchestrator | Cursor agent chat, or Claude Code |
| Python 3.10+ | Runs the helper scripts in [`scripts/`](scripts/) (standard library only) |
| `gemini` seats | Configured `gemini_transport`: `agy`, opt-in Gemini Developer API (own key), or Cursor. Default `[agy, cursor]` |
| `gpt` seats | [Codex CLI](https://github.com/openai/codex) (`codex login`). ChatGPT login uses ChatGPT/Codex quota |
| `grok` seats | The Grok CLI (`agent` on PATH) |
| Fallback for `gemini` seats | Cursor's CLI (`cursor-agent login`): used when `agy` is out of quota; spends Cursor usage |
| Cursor | Task for non-Gemini families. Gemini seats use the shell like Claude Code (`agy`, the API helper, `cursor-agent`), so those need to be installed on the same machine |

The seat runner works on Windows, macOS and Linux with Python 3.10+ (standard library only). PowerShell is needed only for cursor-agent on Windows (and as a fallback for a Codex .ps1 launcher). [`recipes/`](recipes/) hold setup and transport notes; `scripts/council_seat.py` holds dispatch logic.

## Choosing how Gemini is called

`gemini_transport` is an ordered fallback list, independent of `gemini_api.tier`. Default `[agy, cursor]` and `gemini_api.enabled: false` preserve existing behavior. Both Claude Code and Cursor call all three through the shell (Cursor no longer uses Task for Gemini). A seat-level failure advances to the next configured transport, resolving the same family and line again.

| Route | Needs | Latency | Cost | Privacy | Quota |
|---|---|---|---|---|---|
| `agy` | Antigravity CLI signed in with Google; Claude Code | Model/account dependent | Subscription quota | Antigravity account terms | Subscription quota |
| `api`, free | Own Gemini Developer API key; enabled + free acknowledgment | Free 3.8 Flash ~150–170 s to first token in handoff measurements | Free within limits | Google may use prompts/replies for product improvement; human review possible | Argon returned 429 on free in handoff measurements |
| `api`, paid | Own key with billing enabled; enabled + `tier: paid` | Expected faster; not measured here | Per-token API billing | Prompts/replies not used for product improvement under paid API terms | API project/model limits |
| `cursor` | Cursor Task, or signed-in `cursor-agent` in Claude Code | Model/account dependent | Cursor usage | Cursor account terms | Cursor usage limits |

**Do not send PHI or confidential text through the free tier.** Set `tier` to match the API project's billing; the setting does not provision billing or verify it. The latency figures are observations from a free-tier key on 2026-10-24, not guarantees: `gemini-3.8-flash` took about 120 s for a short review prompt (about 150–170 s to first token on a trivial one), `gemini-3.5-flash` about 1 s, `gemini-3.6-flash` returned 503, and Argon returned 429 on the free tier. The newest model is the slowest because free-tier traffic queues behind paid traffic. Paid-tier speed has not been measured. The privacy descriptions are Google's published terms as understood on that date; check them for your account. See [the API recipe](recipes/api.md) for observed model errors, call details and the fallback table.

Key-file setup (PowerShell 7, in your own terminal; never paste a key into chat):

1. Create the private directory:

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.council"
   ```

2. Enter the key without echo:

   ```powershell
   $s = Read-Host -AsSecureString "Gemini API key"
   ```

3. Write it locally:

   ```powershell
   [Net.NetworkCredential]::new('', $s).Password | Set-Content -NoNewline -Encoding ascii "$env:USERPROFILE\.council\gemini.key"
   ```

4. Check the account name with `whoami`, then restrict the file:

   ```powershell
   icacls "$env:USERPROFILE\.council\gemini.key" /inheritance:r /grant:r "$($env:USERDOMAIN)\$($env:USERNAME):(R,W)"
   ```

   A wrong principal fails with "No mapping between account names". Review the resulting ACL; Windows ACLs are managed by you, never modified by the script. On macOS/Linux use `chmod 600 ~/.council/gemini.key`; group/other access is refused.

5. In `config.local.yaml` (gitignored), choose the chain and tier:

   ```yaml
   gemini_transport: [api, agy, cursor]
   gemini_api:
     enabled: true
     tier: free           # or paid, matching your API project
     free_tier_ack: true  # required only on free
   ```

   Per-key precedence: local config > shared config > defaults. Optional `key_file` goes inside `gemini_api`; see [config.local.example.yaml](config.local.example.yaml). Cloud/CI uses the `COUNCIL_GEMINI_API_KEY` secret, which overrides the file. Keys and `config.local*` are never copied by the installer.

6. From the shared root, validate without generation:

   ```powershell
   python -I scripts/council_gemini_api.py preflight
   ```

Free calls default to 1200 s, paid to 300 s (`timeout_sec` overrides). Slow calls never downgrade a model. Only model rejection, quota or 503 can step down, at most `max_model_steps`, always within the same family and line. API thinking defaults to `high`, independently of seat effort. Review/rewrite output caps default to 32768/65536, clamped to the model's output limit. See [core.md](core.md#gemini-transport) for the complete contract.

## Install

**Cursor.** Clone this repository to `~/.cursor/skills/council` (Windows: `%USERPROFILE%\.cursor\skills\council`), restart Cursor or start a new agent chat, and run `/council`.

**Claude Code.** The Claude Code skill is the `claude/` folder of the same repository, linked into Claude's skills folder so both read one copy of the shared files:

```powershell
# Windows (no admin rights needed for a junction)
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\council" -Target "$env:USERPROFILE\.cursor\skills\council\claude"
```

```bash
# macOS / Linux
ln -s ~/.cursor/skills/council/claude ~/.claude/skills/council
```

Do **not** copy `claude/SKILL.md` on its own: it finds `core.md`, `config.yaml` and the scripts in the parent folder, and stops if they are missing.

### Cursor cloud agents

Cloud agents work on a fresh clone of a repository and do not see `~/.cursor/skills`. Pick one route.

**Route A: sync your user skills.**

1. In Cursor, open Settings, then Agents, then Context and Tools.
2. Turn on "Sync Skills for Cloud Agents".
3. Start a new cloud agent and run `/council`.

**Route B: put a copy in the project repository.** Use this when the repository should carry the skill.

1. Run (replace the path with your project):

   ```bash
   python -I ~/.cursor/skills/council/scripts/council_install.py install --target /path/to/your/project
   ```

2. Review `.cursor/skills/council/` in the project, then commit and push it. It holds the Cursor runtime files and five scripts only, no run data, key files or local configs.
3. Start a cloud agent on that repository and run `/council`.
4. To update later, run the same command again. A file you edited in the copy (for example `config.yaml`) is reported and left alone; add `--force` to overwrite it. `python -I .../council_install.py check --target ...` shows drift without changing anything.

On a cloud machine the skill runs on Linux: paths use `/`, the scripts run as `python3` if `python` is missing, and run data goes to `~/.council/runs` on that machine and is discarded with it. A cloud agent has no `agy` or `cursor-agent`, so its Gemini seats rely on the API helper (key via `COUNCIL_GEMINI_API_KEY` secret) and are skipped if that is off; other families use Task. Not yet tested on a real cloud agent; the installed copy was checked to be discovered by a local `cursor-agent`.

## Use

```
/council
/council proceed
/council this architecture doc
/council this time only: models: gemini argon, gpt sol
/council configure
```

Say `/council` with a topic, point it at an existing draft, or ask for a council or wide review. It confirms the roster unless you already said `proceed` or gave the topic in the same message.

## Configure

[`config.yaml`](config.yaml) holds one roster for both harnesses:

```yaml
seats:
  - family: gemini     # gemini | gpt | claude | grok | composer
    line: argon        # the model line; the version is resolved to the newest of that line
    effort: medium
    role: breadth-auditor   # reviewer (default) | breadth-auditor | rewriter
```

A seat names a model family and line, never a version: the newest version of that line is picked from the live catalog on every run, and a different line is never substituted (Flash never becomes Argon). Add `pin: <exact slug>` to freeze a seat. An unrecognized `role` is a config error. A family with no way to be called in the current harness is skipped. Claude Code skips `claude` seats (same family as the orchestrator).

Other keys in `config.yaml`: `show_exchange` (`ask|always|never`), `max_reviewers`, `cli_timeout_sec`, run retention (`run_retention_count`, `run_retention_days`), `stall_checkin_sec`, `gemini_transport` (ordered Gemini routes, default `[agy, cursor]`) and the `gemini_api` block (`enabled`, `tier`, `free_tier_ack`, `thinking_level`, `max_output_review`, `max_output_rewrite`, `max_model_steps`, `timeout_sec`; see [Choosing how Gemini is called](#choosing-how-gemini-is-called)).

Machine-specific overrides go in `config.local.yaml` (gitignored; see [`config.local.example.yaml`](config.local.example.yaml)), for example `runs_dir`. Run folders default to `~/.council/runs` (outside the skill folder).

## Layout

| Path | Role |
|------|------|
| [`core.md`](core.md) | The shared rules: roster, stages, parsing, model resolution, return format |
| [`SKILL.md`](SKILL.md) | Cursor skill: paths, Start, Task/API dispatch |
| [`claude/SKILL.md`](claude/SKILL.md) | Claude Code skill: Start, the CLI mapping, the Opus integration stage |
| [`config.yaml`](config.yaml) | The roster and defaults (shared) |
| [`reviewer-envelope.md`](reviewer-envelope.md), [`reviewer-prompt.md`](reviewer-prompt.md) | What a reviewer seat is told |
| [`catalog.md`](catalog.md) | Cursor Task model reference |
| [`recipes/`](recipes/) | Transport recipes (`common`, `agy`, `api`, `cursor`, `codex`, `grok`) |
| [`partitioning.md`](partitioning.md) | What to do when the input is too big for one cascade |
| [`scripts/`](scripts/) | `council_parse.py` (reply parser/state), `council_resolve.py` (newest model version), `council_rewrite.py` (safe chunked rewrite), `council_prompt.py` (seat prompts), `council_gemini_api.py` (opt-in API lane), `council_seat.py` (cross-platform dispatch, preflight and failure logging) |
| [`recipes/api.md`](recipes/api.md) | API key setup pointer, tiers/timeouts, failures and rewrite calls |

## Tests

```bash
cd scripts && python -B -W error::ResourceWarning -m unittest discover -p "test_*.py"
```

The scripts are what keeps the model-written parts honest: replies are parsed and bookkept by code, a reviewer cannot change the artifact, and a rewritten chunk is thrown away if it loses a number, a heading, a negation or any protected text.

## Status

Verified live on 2026-10-23 in Claude Code (a full run: Gemini Argon, GPT Sol, Opus integration, Gemini Flash rewrite) and on 2026-10-25 in Cursor's own agent (three full runs on a synthetic doc). In the last Cursor run, with gemini_transport: [api, cursor, agy] and a free-tier API key:

- Argon was refused by the free API tier (429), fell through to cursor-agent, and reviewed in 43 s; the rewrite also ran on cursor-agent (about 35 s a chunk, one chunk retried after a failed check).
- In the run before it, Gemini Flash rewrote through the free API (about 200 s for a chunk).
- GPT Sol and the Opus integration ran through Task; ailures.md was written by council_seat.py and no ad-hoc wrapper scripts were created.

Not yet verified: a Cursor cloud agent, the paid API tier, whether stripping MSYSTEM fixes the Cursor hook bug when cursor-agent is launched from Git Bash, and POSIX (macOS/Linux) execution of the runner (the code and its tests are written for it but ran on Windows only).

## Run data

`~/.council/runs` holds working state (prompts, artifacts, CLI logs). It can contain the text you sent through Council, which is why it lives outside the skill folder and outside git. On Windows with a small system drive, make `~/.council` a junction to another drive (`New-Item -ItemType Junction -Path $env:USERPROFILE\.council -Target D:\.council`).

## License

[MIT](LICENSE)
