# Council

A sequential **multi-model review** for [Cursor](https://cursor.com) and [Claude Code](https://docs.anthropic.com/en/docs/claude-code), run with `/council`. Both harnesses share one set of rules ([`core.md`](core.md)); only the way a model is called differs.

## How a run works

1. **Draft (seat 0).** Opus writes the draft, or you supply one.
2. **Review, gaps only.** Reviewers from other model families (default: Gemini Argon, then GPT Sol) each read the draft and report gaps. They never rewrite it.
3. **Integrate, once.** Opus resolves every gap (accept or reject with a reason) and writes the revision. Anything it rejects is printed at the end, with its reason.
4. **Rewrite, once.** A writer model polishes the result for a reader who is not a specialist, in small chunks, adding short explanations of jargon. The writers are an ordered fallback chain (default: Gemini Flash, then Claude Sonnet); the first that works does the whole rewrite. A script checks every chunk and keeps the original wording of any chunk that fails.

There is no parallel first-answers pass and no chairman synthesis. See [`core.md`](core.md#stages).

## What you need

| Piece | Requirement |
|-------|-------------|
| Orchestrator | Cursor agent chat, or Claude Code |
| Python 3.10+ | Standard library only; run helpers with `python -I` |
| Gemini seats | Antigravity CLI (`agy`), or the optional Gemini Developer API with your own key |
| GPT seats | Codex CLI in default-origin runs (`codex login`); Cursor Task in Cursor-originated runs |
| Grok seats | Signed-in Cursor CLI (`cursor-agent login`); Grok is reached through the Cursor CLI |
| Claude seats | Claude Code Agent subagent in default origin; Cursor Task in cursor origin |

**Argon works out of the box for anyone with agy; API access is optional.** The seeded roster is Gemini Argon (breadth), a conditional Gemini Flash breadth fallback, GPT Sol, then a Gemini Flash / Claude Sonnet rewriter chain. Opus drafts and integrates. Gemini is NEVER routed through Cursor or Task in any run.

The seat runner supports Windows, macOS and Linux with Python 3.10+. PowerShell is needed for cursor-agent on Windows (and as a fallback for a Codex .ps1 launcher). [`recipes/`](recipes/) hold setup notes; `scripts/council_seat.py` holds dispatch logic.

## Choosing how Gemini is called

Both origins default to `routes.<origin>.gemini: [api, agy]`. The API is disabled out of the box: its preflight logs `api-refused` once, skips that transport for the run, and Argon uses agy. When enabled and acknowledged, the API is tried first on every run, then agy if needed. Resolve the same family and line again when changing transport. Gemini has only these two transports; Cursor/Task are never Gemini routes.

| Route | Needs | Cost/quota | Privacy |
|---|---|---|---|
| agy | Antigravity CLI signed in with Google | Account subscription quota | Antigravity account terms |
| api, free | Own key; API enabled and free-tier acknowledgment | Free project/model limits | Prompts/replies may be used for product improvement and human review |
| api, paid | Own key, billing enabled, `tier: paid` | Per-token billing and project/model limits | Check paid API terms for your account |

**Free-tier prompts and replies may be used by Google to improve its products and reviewed by humans.** `tier` must match the API project's billing; it does not enable or verify billing. The wizard does not enable the API or change privacy acknowledgment. See [the API recipe](recipes/api.md) for measurements and setup notes.

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

5. In `config.local.yaml` (gitignored), enable the optional API; the shipped route already tries api then agy:

   ```yaml
   based_on_config_version: 2
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

Free calls default to 1200 s, paid to 300 s (`timeout_sec` overrides). On API 503, wait 20 s plus 0..2 s jitter and retry the same request/model once; `unavailable_retry_wait_sec` (0..120) and `unavailable_retries` (0..3) configure this. Persistent 503 gets a one-day cooldown, then the next-lower version of the same line, then the next transport. API 429 never waits: try the next-lower version within `max_model_steps`, then the next transport. Argon tries the enabled API again next run. Timeouts advance transport and never lower a model. The API outer timeout is `timeout_sec * (1 + unavailable_retries) + unavailable_retries * (unavailable_retry_wait_sec + 2) + 120` (2542 s for free defaults), exposed by the API `config` command. Internal 503 retries do not consume another dispatch.

API thinking defaults to high independently of seat effort; review/rewrite output caps default to 32768/65536. See [core.md](core.md#gemini-transport) for all failure classes and call limits.

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

After installing, put ALL changes in `config.local.yaml`, never edit `config.yaml`. Updates never overwrite or upload local config. Both harnesses validate and check migration versions at Start; see [Configure](#configure).

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

2. Review `.cursor/skills/council/` in the project, then commit and push it. It holds the Cursor runtime files, `config.migrations.yaml` and seven scripts (including `council_roster.py`); no recipes, presets, run data, keys or local configs.
3. Start a cloud agent on that repository and run `/council`.
4. To update later, run the same command again. A changed runtime file is reported and left alone; `--force` replaces runtime files. Keep every customization in the project copy's gitignored `config.local.yaml`, never in `config.yaml`. Local configs and keys are outside the installer allowlist and are never copied, uploaded or overwritten, including with `--force`. `python -I .../council_install.py check --target ...` shows drift without changing anything.

On a cloud machine the skill runs on Linux: paths use `/`, the scripts run as `python3` if `python` is missing, and run data goes to `~/.council/runs` on that machine and is discarded with it. A cloud agent has no `agy` or `cursor-agent`, so its Gemini seats rely on the API helper (key via `COUNCIL_GEMINI_API_KEY` secret) and are skipped if that is off; Grok is skipped without the Cursor CLI; GPT/Claude/Composer use Task. Not yet tested on a real cloud agent; the installed copy was checked to be discovered by a local `cursor-agent`.

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

Put **ALL changes in `config.local.yaml`; never edit `config.yaml`**. The local file is gitignored and is never overwritten or uploaded by an update. Start with [`config.local.example.yaml`](config.local.example.yaml), or run `/council configure` (also `configure` / `reconfigure`).

The wizard starts from shipped defaults, the current roster, or one of [`presets/`](presets/): `api-only` (Gemini API only; API enablement stays separate), `claude-code-full` (default seats plus Grok after GPT Sol), or `cursor-originated` (default seats plus Claude Opus after GPT Sol). Presets are complete local roster bodies, not installed into project copies. The wizard asks for seat count, family/line/effort/role/label, route order, reviewer fallbacks, rewriter chain order and exchange preference. Claude Code probes newly chosen runner transports before accepting them. It validates `<runs_dir>/configure-candidate.yaml`, previews BOTH origins, asks approval, and saves with `council_roster.py write-local`. Saving preserves API and unrelated settings, backs up to `config.prev.local.yaml`, validates the merged config in both origins and writes atomically. It never edits shipped config or the changelog.

Origins describe the orchestrator: `cursor` is Cursor IDE; `default` is any other orchestrator. These are the allowed transports and built-in ordered routes:

| Family | default origin | cursor origin |
|---|---|---|
| gemini | api, agy | api, agy |
| gpt | codex | task |
| grok | cursor_cli | cursor_cli |
| claude | agent | task |
| composer | none | task |

`cursor_cli` is the Cursor CLI (Grok only; runner/source name `cursor`); task/agent are harness tools. Override `routes.<origin>.<family>` per pair; missing entries use built-ins, `[]` skips with no-transport. Example local route override:

```yaml
based_on_config_version: 2
routes:
  default:
    gemini: [agy, api]
  cursor:
    gemini: [agy, api]
```

A seat's optional `transports` REPLACES its route, including the two seeded breadth seats' explicit `[api, agy]` overrides. To change those seats' order, save a complete local `seats:` list with updated overrides (or remove overrides to use routes). Local `seats:` replaces the ENTIRE shipped list. Each seat names family and line, optional effort, role, label, pin, transports and fallback_for. Versions resolve newest within the same line; pin freezes an exact slug. Invalid roles/routes stop at Start. Transports are filtered per origin and dropped entries are shown. Claude Code skips Claude reviewers as same-family; the Sonnet rewriter still runs via Agent.

The seeded Flash breadth fallback has `fallback_for: Gemini Argon (breadth)`, immediately follows Argon and inherits breadth-auditor. It runs only when Argon remains unparsed after ALL api/agy attempts, retries and final parsing. A parsed Argon causes no fallback dispatch/log. The fallback reuses review slot 1 and G1-* gaps, leaving Sol at slot 2 and integration at slot 3; failure of both contributes no review. Labels must uniquely identify the immediately preceding primary reviewer; a fallback cannot target a fallback. Rewriters form a separate ordered chain: Flash then Sonnet. `max_reviewers` caps primary review slots and keeps their fallbacks together; one-run overrides carry no transports/fallback_for.

Other local settings include `show_exchange` (ask/always/never), `max_reviewers`, `cli_timeout_sec`, `runs_dir` (default `~/.council/runs`, outside the skill), retention counts/days and `stall_checkin_sec`. API settings stay in `gemini_api` (see above); credentials belong only in the private key file or environment secret.

Shipped `config_version: 2` changes only for breaking changes. Local `based_on_config_version` records reviewed upgrades; a missing stamp in an existing local file means version 1, and no local file requires no migration. Every Start runs `council_roster.py load`. When shipped version exceeds the stamp, show pending [config.migrations.yaml](config.migrations.yaml) notes (title, key, action, example) and ask **Apply the proposed edits / Mark as reviewed (no edits) / Not now**. Apply or mark-reviewed requires approval before edits/stamping; Not now proceeds unchanged and asks next run. Invalid config stops with safe diagnostics and a proposed local fix, never an automatic edit. Additive settings with safe defaults do not bump the version. The deprecated `gemini_transport` alias accepts only api/agy; use routes instead, and never mix the alias with routes.*.gemini in one file.

## Layout

| Path | Role |
|------|------|
| [`core.md`](core.md) | The shared rules: roster, stages, parsing, model resolution, return format |
| [`SKILL.md`](SKILL.md) | Cursor skill: paths, Start, Task/API dispatch |
| [`claude/SKILL.md`](claude/SKILL.md) | Claude Code skill: Start, the CLI mapping, the Opus integration stage |
| [`config.yaml`](config.yaml) | Shipped roster/defaults; customize only config.local.yaml |
| [`config.migrations.yaml`](config.migrations.yaml) | Breaking upgrade notes keyed by config version |
| [`presets/`](presets/) | Validated starting rosters for Configure |
| [`scripts/council_roster.py`](scripts/council_roster.py) | Load/validate rosters, check migrations, stamp, write-local |
| [`reviewer-envelope.md`](reviewer-envelope.md), [`reviewer-prompt.md`](reviewer-prompt.md) | What a reviewer seat is told |
| [`catalog.md`](catalog.md) | Cursor Task model reference |
| [`recipes/`](recipes/) | Transport recipes (`common`, `agy`, `api`, `cursor` (Grok), `codex`) |
| [`partitioning.md`](partitioning.md) | What to do when the input is too big for one cascade |
| [`scripts/`](scripts/) | `council_parse.py` (reply parser/state), `council_resolve.py` (newest model version), `council_rewrite.py` (safe chunked rewrite), `council_prompt.py` (seat prompts), `council_gemini_api.py` (opt-in API lane), `council_seat.py` (cross-platform dispatch, preflight and failure logging) |
| [`recipes/api.md`](recipes/api.md) | API key setup pointer, tiers/timeouts, failures and rewrite calls |

## Tests

```bash
cd scripts && python -B -W error::ResourceWarning -m unittest discover -p "test_*.py"
```

The scripts are what keeps the model-written parts honest: replies are parsed and bookkept by code, a reviewer cannot change the artifact, and a rewritten chunk is thrown away if it loses a number, a heading, a negation or any protected text.

## Status

Verified live on 2026-10-23 in Claude Code (a full run: Gemini Argon, GPT Sol, Opus integration, Gemini Flash rewrite) and on 2026-10-25 in Cursor's own agent (three full runs on a synthetic doc). Those Cursor runs used the now-retired Gemini-through-Cursor route; they do not verify routing v2. In the last historical run, with a free-tier API key:

- Argon was refused by the free API tier (429), fell through to `cursor-agent`, and reviewed in 43 s; the rewrite also ran on `cursor-agent` (about 35 s a chunk, one chunk retried after a failed check).
- In the run before it, Gemini Flash rewrote through the free API (about 200 s for a chunk).
- GPT Sol and the Opus integration ran through Task; `failures.md` was written by `council_seat.py` and no ad-hoc wrapper scripts were created.

The Cursor hook bug (`MSYSTEM` set plus any tool call makes `cursor-agent` evaluate its PowerShell hook wrapper in bash) was reproduced on 2026-10-25 and does not occur without `MSYSTEM`. Council seats make no tool calls, and the runner also strips `MSYSTEM` for `cursor-agent`, so it cannot affect them.

Not yet verified: a Cursor cloud agent, the paid API tier, and POSIX (macOS/Linux) execution of the runner (the code and its tests are written for it but ran on Windows only).

## Run data

`~/.council/runs` holds working state (prompts, artifacts, CLI logs). It can contain the text you sent through Council, which is why it lives outside the skill folder and outside git. On Windows with a small system drive, make `~/.council` a junction to another drive (`New-Item -ItemType Junction -Path $env:USERPROFILE\.council -Target D:\.council`).

## License

[MIT](LICENSE)
