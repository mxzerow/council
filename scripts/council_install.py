#!/usr/bin/env python3
"""Install or check a project-level copy of the Cursor /council skill.

Cursor cloud agents run on a fresh clone of a repository and do not see your personal
`~/.cursor/skills`. They do find skills in the repository itself, under
`.cursor/skills/<name>/SKILL.md`. This copies the files the Cursor skill needs into
`<repo>/.cursor/skills/council/` so it can be committed.

What is copied: SKILL.md, core.md, config.yaml, catalog.md, partitioning.md, reviewer-envelope.md,
reviewer-prompt.md, LICENSE and the four scripts. Not copied: claude/, recipes/, tests, docs, run data,
config.local*. Run data lives in ~/.council/runs on whatever machine runs the skill.

Standard library only.

  install --target REPO [--source DIR] [--force] [--dry-run]
  check   --target REPO [--source DIR]

An update never overwrites a file you changed in the copy (it is reported); `--force` overwrites.
`config.yaml` is the file most likely to be customised per repository.
Exit codes: 0 ok / in sync, 1 drift or skipped files, 2 usage or IO error.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

FILES = [
    "SKILL.md", "core.md", "config.yaml", "catalog.md", "partitioning.md",
    "reviewer-envelope.md", "reviewer-prompt.md", "LICENSE",
    "scripts/council_parse.py", "scripts/council_resolve.py",
    "scripts/council_rewrite.py", "scripts/council_prompt.py",
]
MANIFEST = ".council-install.json"
DEST_REL = os.path.join(".cursor", "skills", "council")


class UsageError(Exception):
    pass


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def default_source():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source_commit(src):
    try:
        r = subprocess.run(["git", "-C", src, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def check_source(src):
    missing = [f for f in FILES if not os.path.isfile(os.path.join(src, f))]
    if missing:
        raise UsageError(f"source is missing files: {', '.join(missing)}")
    with open(os.path.join(src, "SKILL.md"), encoding="utf-8") as f:
        head = f.read(600)
    m = re.search(r"^name:\s*(\S+)", head, re.M)
    if not m or m.group(1) != "council":
        raise UsageError("SKILL.md front matter must say `name: council` (Cursor requires the name to match the folder)")


def dest_of(target):
    if not os.path.isdir(target):
        raise UsageError(f"target is not a folder: {target}")
    return os.path.join(target, DEST_REL)


def load_manifest(dest):
    p = os.path.join(dest, MANIFEST)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("files", {})
    except ValueError:
        return {}


def cmd_install(a):
    src, dest = a.source, dest_of(a.target)
    check_source(src)
    prior = load_manifest(dest)
    written, skipped, same = [], [], []
    for rel in FILES:
        s, d = os.path.join(src, rel), os.path.join(dest, rel)
        s_hash = sha(s)
        if os.path.exists(d):
            d_hash = sha(d)
            if d_hash == s_hash:
                same.append(rel)
                continue
            recorded = prior.get(rel)
            locally_changed = recorded is not None and d_hash != recorded
            untracked_existing = recorded is None
            if (locally_changed or untracked_existing) and not a.force:
                skipped.append(rel)                 # protect a customised or unknown file
                continue
        written.append(rel)
        if not a.dry_run:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copyfile(s, d)
    if not a.dry_run:
        # a skipped (customised) file keeps its previously recorded hash so it stays reported as locally changed
        files = {}
        for rel in FILES:
            if rel in skipped:
                if rel in prior:
                    files[rel] = prior[rel]
            elif os.path.exists(os.path.join(dest, rel)):
                files[rel] = sha(os.path.join(dest, rel))
        with open(os.path.join(dest, MANIFEST), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"source": os.path.abspath(src), "commit": source_commit(src), "files": files}, f, indent=2)
            f.write("\n")
    print(json.dumps({"status": "dry-run" if a.dry_run else "installed", "dest": dest, "written": written,
                      "unchanged": same, "skipped_local_changes": skipped}))
    return 1 if skipped else 0


def cmd_check(a):
    src, dest = a.source, dest_of(a.target)
    check_source(src)
    if not os.path.isdir(dest):
        print(json.dumps({"status": "not-installed", "dest": dest}))
        return 1
    prior = load_manifest(dest)
    missing, outdated, local = [], [], []
    for rel in FILES:
        s, d = os.path.join(src, rel), os.path.join(dest, rel)
        if not os.path.exists(d):
            missing.append(rel)
            continue
        if sha(d) != sha(s):
            (local if prior.get(rel) and sha(d) != prior[rel] else outdated).append(rel)
    ok = not (missing or outdated or local)
    print(json.dumps({"status": "in-sync" if ok else "drift", "dest": dest, "missing": missing,
                      "outdated_vs_source": outdated, "changed_locally": local}))
    return 0 if ok else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="council_install")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("install", "check"):
        s = sub.add_parser(name)
        s.add_argument("--target", required=True)
        s.add_argument("--source", default=default_source())
        if name == "install":
            s.add_argument("--force", action="store_true")
            s.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    try:
        return {"install": cmd_install, "check": cmd_check}[a.cmd](a)
    except (UsageError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
