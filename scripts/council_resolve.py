#!/usr/bin/env python3
"""Council model resolver (step 6): latest version within a family and line.

A seat stores family + line + effort, never a version. This script reads the live
catalog and picks the newest version of that line. It never crosses lines (a new
line such as "argon" is not a newer "flash"), never guesses a slug, remembers
models the server rejected (7-day cooldown) and the last slug that worked, and
classifies CLI failures so only a real model rejection is cached.

Standard library only. Run with `python -I scripts/council_resolve.py <command> ...`.

Commands
  resolve  --family F --line L [--effort E] [--pin SLUG] --source S [--exclude SLUG ...] [--state FILE] [--today YYYY-MM-DD]
  mark-rejected --slug SLUG --state FILE [--reason TEXT] [--days N] [--today YYYY-MM-DD]
  mark-good     --key family/line/effort --slug SLUG --state FILE
  classify      --file FILE            print: model-rejected|quota|auth|network|timeout|other
  list          --source S [--family F]  parsed catalog entries, one per line

Sources (--source)
  agy          run `agy models`
  api          Gemini Developer API catalog (opt-in); stable generateContent models only
  codex        read ~/.codex/models_cache.json (UTF-8), entries with visibility "list"
  grok         run `agent models`
  cursor       run `cursor-agent --list-models` (a fallback lane for any family Cursor serves)
  file:PATH    slugs one per line (optionally `slug<TAB>label`), e.g. a Cursor Task enum

Exit codes: 0 ok / skip (read the JSON `status`), 2 usage or IO error.
"""
import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAMILIES = ("gemini", "gpt", "claude", "grok", "composer")
EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]   # `-none` / `-minimal` slugs exist (Cursor, Gemini 3.6)
# Modifier tokens never name a line. Plain variants win over these at equal version.
MODS = {"thinking", "fast", "latest", "preview", "beta", "alpha", "exp", "experimental", "rc"}
COOLDOWN_DAYS = 7
DATE_TOKEN = re.compile(r"\d{6,8}")  # 261001 / 20261001 release stamps are not versions


class UsageError(Exception):
    pass


# ------------------------------------------------------------------ slug parsing
def parse_slug(slug):
    """Return dict(slug, family, line, version(tuple), effort, mods, extras) or None."""
    toks = slug.strip().lower().split("-")
    if toks and toks[0] == "cursor":
        toks = toks[1:]
    if not toks or toks[0] not in FAMILIES:
        return None
    family, body = toks[0], toks[1:]
    effort = None
    for i in range(len(body) - 1, -1, -1):
        if body[i] in EFFORTS:
            effort = body[i]
            del body[i]
            break
    # a trailing 2026-10-01 style date: drop the three tokens
    if len(body) >= 3 and re.fullmatch(r"20\d\d", body[-3]) and re.fullmatch(r"\d\d", body[-2]) and re.fullmatch(r"\d\d", body[-1]):
        body = body[:-3]
    nums, alpha = [], []
    for t in body:
        if DATE_TOKEN.fullmatch(t):
            continue
        (nums if re.fullmatch(r"\d+(\.\d+)*", t) else alpha).append(t)
    version = [int(p) for t in nums for p in t.split(".")]
    while version and version[-1] == 0:  # 5, 5.0 and 5.0.0 are the same version
        version.pop()
    version = tuple(version)
    mods = [a for a in alpha if a in MODS]
    lines = [a for a in alpha if a not in MODS]
    return {"slug": slug.strip(), "family": family, "line": lines[0] if lines else family,
            "version": version, "effort": effort, "mods": mods, "extras": lines[1:]}


# ------------------------------------------------------------------ catalog sources
def run_cli(names, args):
    exe = None
    for n in names:
        exe = shutil.which(n)
        if exe:
            break
    if not exe:
        raise UsageError(f"{names[0]} not found on PATH")
    r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL, timeout=60)
    if r.returncode != 0:
        raise UsageError(f"{names[0]} exited {r.returncode}: {(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout


def catalog(source):
    """Return a non-empty list of slugs, or raise UsageError if the catalog is unreachable or empty."""
    try:
        if source == "api":
            import council_gemini_api as cga
            try:
                slugs = cga.list_models(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            except cga.APIError as e:
                raise UsageError(f"catalog unreachable (api): {e}") from None
        elif source == "agy":
            out = run_cli(["agy"], ["models"]).replace("\r\n", "\n")
            slugs = [m.group(1) for m in re.finditer(r"^([a-z0-9][a-z0-9.\-]*)\t", out, re.M)]
        elif source == "grok":
            out = run_cli(["agent"], ["models"]).replace("\r\n", "\n")
            slugs = [m.group(1) for m in re.finditer(r"^\s*\*?\s*(grok-[\w.\-]+)", out, re.M)]
        elif source == "cursor":
            # Cursor's CLI can serve Gemini, GPT, Claude and Grok models from Cursor's own quota
            out = run_cli(["cursor-agent"], ["--list-models"]).replace("\r\n", "\n")
            slugs = [m.group(1) for m in re.finditer(r"^[ \t]*([a-z0-9][a-z0-9.\-]*) - ", out, re.M)]
        elif source == "codex":
            p = os.path.join(os.path.expanduser("~"), ".codex", "models_cache.json")
            with open(p, "rb") as f:
                data = json.loads(f.read().decode("utf-8", errors="replace"))
            models = data.get("models") if isinstance(data, dict) else data
            if not isinstance(models, list):
                raise ValueError("models_cache.json has no model list")
            slugs = [m["slug"] for m in models if isinstance(m, dict) and m.get("visibility") == "list" and isinstance(m.get("slug"), str)]
        elif source.startswith("file:"):
            with open(source[5:], "rb") as f:
                text = f.read().decode("utf-8", errors="replace")
            # one slug per line; `slug<TAB>label` and `slug - label` (cursor-agent --list-models) both work.
            # Prose lines such as "Available models" start with a capital and are ignored.
            slugs = [m.group(1) for m in re.finditer(r"^[ \t]*([a-z0-9][a-z0-9.\-]*)(?=\s|$)", text.replace("\r\n", "\n"), re.M)]
        else:
            raise UsageError(f"unknown source {source!r}")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as e:
        raise UsageError(f"catalog unreachable ({source}): {e}")
    if not slugs:
        raise UsageError(f"catalog empty ({source})")
    return slugs


# ------------------------------------------------------------------ state
def load_state(path):
    if not path or not os.path.exists(path):
        return {"rejected": {}, "last_good": {}}
    try:
        with open(path, "rb") as f:
            st = json.loads(f.read().decode("utf-8"))
        if not isinstance(st, dict):
            raise ValueError("top level is not an object")
        st.setdefault("rejected", {})
        st.setdefault("last_good", {})
        if not isinstance(st["rejected"], dict) or not isinstance(st["last_good"], dict):
            raise ValueError("rejected and last_good must be objects")
        for slug, rec in st["rejected"].items():
            if not isinstance(rec, dict) or not isinstance(rec.get("date"), str):
                raise ValueError(f"bad rejected entry for {slug!r}")
        return st
    except ValueError as e:
        raise UsageError(f"state file is corrupt ({e}): {path}")


def save_state(path, st):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(st, indent=2, sort_keys=True) + "\n")
        for attempt in range(20):  # Windows denies the replace while a reader has the target open
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.02)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


@contextlib.contextmanager
def state_lock(path, timeout=10.0):
    """Serialize read-modify-write of the state file across processes and threads."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    lock = path + ".lock"
    deadline = time.time() + timeout
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except (FileExistsError, PermissionError):  # Windows raises PermissionError while the holder deletes the lock
            try:
                if time.time() - os.path.getmtime(lock) > 30:  # stale lock from a crashed process
                    os.remove(lock)
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise UsageError(f"state lock busy: {lock}")
            time.sleep(0.01)
    try:
        yield
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def today_of(a):
    return datetime.strptime(a.today, "%Y-%m-%d").date() if getattr(a, "today", None) else date.today()


def cooling(st, slug, today):
    r = st["rejected"].get(slug)
    if not r:
        return False
    try:
        d = datetime.strptime(r["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return False
    days = r.get("days", COOLDOWN_DAYS)
    if type(days) is not int or days < 1:
        raise UsageError("state rejected days must be a positive integer")
    return (today - d).days < days


def vstr(v):
    return ".".join(str(x) for x in v) if v else ""


# ------------------------------------------------------------------ resolve
def effort_distance(want, have):
    """0 = exact. A slug with no effort (e.g. gpt-6.1-sol, effort is a flag) is acceptable but
    loses to an exact match; otherwise nearer is better, and the lower of two equals wins."""
    if want is None:
        return 0
    if have is None:
        return 1
    return abs(EFFORTS.index(want) - EFFORTS.index(have)) * 2 + (1 if EFFORTS.index(have) > EFFORTS.index(want) else 0)


def cmd_resolve(a):
    if a.family not in FAMILIES:
        raise UsageError(f"unknown family {a.family!r}")
    key = f"{a.family}/{a.line}/{a.effort or ''}"
    if a.pin:
        print(json.dumps({"status": "ok", "slug": a.pin, "from": "pin", "key": key}))
        return 0
    st = load_state(a.state)
    today = today_of(a)
    excluded = set(a.exclude)
    try:
        slugs = catalog(a.source)
    except UsageError as e:
        lg = st["last_good"].get(key)
        if lg and lg not in excluded and not cooling(st, lg, today):
            print(json.dumps({"status": "ok", "slug": lg, "from": "last_good", "key": key, "warning": str(e)}))
        else:
            print(json.dumps({"status": "skip", "reason": "no-catalog", "key": key, "detail": str(e)}))
        return 0
    parsed = [p for p in (parse_slug(s) for s in slugs) if p]
    if a.source == "api":
        same_line = [p for p in parsed if p["family"] == a.family and
                     ((p["line"] == a.line and not p["extras"]) or p["extras"] == [a.line])]
    else:
        same_line = [p for p in parsed if p["family"] == a.family and p["line"] == a.line]
    if not same_line:
        print(json.dumps({"status": "skip", "reason": "no-line-match", "key": key,
                          "detail": "no catalog entry for this family and line; lines never cross"}))
        return 0
    skipped = [p["slug"] for p in same_line if p["slug"] in excluded or cooling(st, p["slug"], today)]
    usable = [p for p in same_line if p["slug"] not in skipped]
    if not usable:
        print(json.dumps({"status": "skip", "reason": "all-cooling-down", "key": key, "cooldown_skipped": skipped}))
        return 0
    best_version = max(p["version"] for p in usable)
    top = [p for p in usable if p["version"] == best_version]
    top.sort(key=lambda p: (effort_distance(a.effort, p["effort"]), len(p["mods"]), usable.index(p)))
    best = top[0]
    prev = st["last_good"].get(key)
    print(json.dumps({"status": "ok", "slug": best["slug"], "version": vstr(best["version"]),
                      "effort": best["effort"], "from": "catalog", "key": key,
                      "upgraded_from": prev if prev and prev != best["slug"] else None,
                      "cooldown_skipped": skipped,
                      "effort_note": None if (a.effort is None or best["effort"] in (None, a.effort))
                      else f"{a.effort} not offered at {vstr(best['version'])}; using {best['effort']}"}))
    return 0


def cmd_mark_rejected(a):
    if a.days < 1:
        raise UsageError("--days must be a positive integer")
    with state_lock(a.state):
        st = load_state(a.state)
        st["rejected"][a.slug] = {"date": today_of(a).isoformat(), "reason": a.reason or "model-rejected", "days": a.days}
        save_state(a.state, st)
    print(json.dumps({"status": "ok", "rejected": a.slug, "until_days": a.days}))
    return 0


def cmd_mark_good(a):
    with state_lock(a.state):
        st = load_state(a.state)
        st["last_good"][a.key] = a.slug
        st["rejected"].pop(a.slug, None)
        save_state(a.state, st)
    print(json.dumps({"status": "ok", "key": a.key, "slug": a.slug}))
    return 0


# ------------------------------------------------------------------ failure classification
# Order matters: quota and auth are checked BEFORE model-rejected, because their messages
# often name the model ("401: model gpt-6.1-sol: invalid API key") and must never be cached
# as a model rejection.
CLASSES = [
    ("quota", r"quota|rate.?limit|\b429\b|resource_exhausted|usage limit|out of credits|insufficient credit|too many requests"),
    ("auth", r"not authenticated|login required|please log ?in|unauthori[sz]ed|\b401\b|invalid api key|authentication (required|failed)|sign in"),
    ("model-rejected", r"not supported when using codex|model[^\n]{0,60}(not supported|not found|does not exist|unsupported|not available)"
                       r"|unsupported model|unknown model|invalid model|no such model"),
    ("network", r"econn|etimedout|enotfound|getaddrinfo|network|dns|socket|connection (reset|refused|closed)|tls|certificate"),
    ("timeout", r"timed out|timeout|deadline exceeded"),
]


def classify_text(text):
    low = text.lower()
    for name, pat in CLASSES:
        if re.search(pat, low):
            return name
    return "other"


def cmd_classify(a):
    try:
        with open(a.file, "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
    except OSError as e:
        raise UsageError(str(e))
    print(classify_text(text))
    return 0


def cmd_list(a):
    for s in catalog(a.source):
        p = parse_slug(s)
        if p and (not a.family or p["family"] == a.family):
            print(f"{s}\t{p['family']}/{p['line']}\tv{vstr(p['version'])}\t{p['effort'] or '-'}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="council_resolve")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("resolve")
    s.add_argument("--family", required=True); s.add_argument("--line", required=True)
    s.add_argument("--effort", default=None, choices=EFFORTS); s.add_argument("--pin", default=None)
    s.add_argument("--source", required=True); s.add_argument("--state", default=None); s.add_argument("--today", default=None)
    s.add_argument("--exclude", action="append", default=[])
    s = sub.add_parser("mark-rejected")
    s.add_argument("--slug", required=True); s.add_argument("--state", required=True)
    s.add_argument("--reason", default=None); s.add_argument("--today", default=None)
    s.add_argument("--days", type=int, default=COOLDOWN_DAYS)
    s = sub.add_parser("mark-good")
    s.add_argument("--key", required=True); s.add_argument("--slug", required=True); s.add_argument("--state", required=True)
    s = sub.add_parser("classify"); s.add_argument("--file", required=True)
    s = sub.add_parser("list"); s.add_argument("--source", required=True); s.add_argument("--family", default=None)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    fn = {"resolve": cmd_resolve, "mark-rejected": cmd_mark_rejected, "mark-good": cmd_mark_good,
          "classify": cmd_classify, "list": cmd_list}[a.cmd]
    try:
        return fn(a) or 0
    except (UsageError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
