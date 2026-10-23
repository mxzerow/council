#!/usr/bin/env python3
"""Council envelope parser and run-state keeper (step 4).

Reads one seat's raw reply, parses the Council sentinel envelope, and does the
bookkeeping the orchestrator used to do by hand: mint gap IDs, update the verdict
ledger, restore dissent, rebuild the packet files. The orchestrator reads only the
short summary this script prints, never the full reply.

Standard library only. Run with `python -I scripts/council_parse.py <command> ...`.

Commands
  init        --run DIR                          create placeholder files and gaps.json
  open-ids    --run DIR                          print open blocking IDs, comma separated
  parse       --run DIR --seat N --label TEXT --reply FILE
              [--role ROLE] [--hold-artifact] [--final-attempt]
  apply       --run DIR --seat N                 move a held Revised into artifact.md

Exit codes of `parse`
  0 ok                       3 malformed (retry once)
  4 illegal identity (retry once; --final-attempt keeps identity, drops statuses)
  2 usage / IO error
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

SENT = {
    "STATUS": "<<<COUNCIL_STATUS>>>",
    "GAPS": "<<<COUNCIL_GAPS>>>",
    "UNRESOLVED": "<<<COUNCIL_UNRESOLVED>>>",
    "REVISED": "<<<COUNCIL_REVISED>>>",
    "END": "<<<COUNCIL_END>>>",
}
ORDER = ["STATUS", "GAPS", "UNRESOLVED", "REVISED", "END"]
PLACEHOLDERS = {"none", "(none yet)", ""}
STATUS_RE = re.compile(
    r"^\s*[-*]?\s*(G\d+-\d+)\s*:\s*(addressed \(pre-existing\)|addressed|unresolved|rejected)(?=\s|$|[:—–-])"
    r"\s*(?:[:—–-]\s*(.*))?$",
    re.IGNORECASE,
)
GAP_RE = re.compile(r"^\s*[-*]\s+(?:(blocking|optional)\s*:\s*)?(.*)$", re.IGNORECASE)
NONE_RE = re.compile(r"^\s*[-*]?\s*none\.?\s*$", re.IGNORECASE)


def fail(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def read(path, default=""):
    if not os.path.exists(path):
        return default
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def write(path, text):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ state
def load_state(run):
    p = os.path.join(run, "gaps.json")
    if os.path.exists(p):
        return json.loads(read(p))
    return {"version": 1, "gaps": {}, "seats": []}


def save_state(run, st):
    write(os.path.join(run, "gaps.json"), json.dumps(st, ensure_ascii=False, indent=2) + "\n")


def is_open(g):
    return g["status"] != "addressed"


def open_blocking(st):
    return [i for i, g in st["gaps"].items() if g["tier"] == "blocking" and is_open(g)]


def id_key(i):
    a, b = i[1:].split("-")
    return (int(a), int(b))


def render_packets(run, st):
    """Rebuild verdicts.md and open-gaps.md from gaps.json."""
    ids = sorted(st["gaps"], key=id_key)
    write(
        os.path.join(run, "verdicts.md"),
        "".join(f"{i}: {st['gaps'][i]['status']} ({st['gaps'][i]['tier']})\n" for i in ids),
    )
    p = os.path.join(run, "open-gaps.md")
    opens = [i for i in ids if is_open(st["gaps"][i])]
    if not opens:
        if os.path.exists(p):
            os.remove(p)
        return
    lines = ["# Open gaps (blocking IDs need STATUS)", ""]
    for i in opens:
        g = st["gaps"][i]
        last = g["history"][-1] if g["history"] else None
        note = f" — latest: {last['status']}" + (f" ({last['why']})" if last and last.get("why") else "") if last else ""
        lines.append(f"- {i} [{g['tier']}] (seat {g['seat']}, {g['label']}){note}: {g['text']}")
    write(p, "\n".join(lines) + "\n")


def append_md(path, heading, body_lines):
    cur = read(path)
    if cur.strip().lower() in PLACEHOLDERS:
        cur = ""
    if cur and not cur.endswith("\n"):
        cur += "\n"
    write(path, cur + f"### {heading}\n" + "\n".join(body_lines) + "\n")


# ------------------------------------------------------------------ parsing
def unwrap(raw):
    """Accept a plain-text reply or a JSON wrapper from a CLI (`text` or `result`)."""
    s = raw.lstrip()
    if s.startswith("{"):
        try:
            j = json.loads(s)
            for k in ("text", "result"):
                if isinstance(j, dict) and isinstance(j.get(k), str):
                    return j[k]
        except ValueError:
            pass
    return raw


def split_envelope(text):
    """Return (sections dict, error). Preamble before the first sentinel is dropped."""
    pos = {}
    for name in ORDER:
        s = SENT[name]
        i = text.rfind(s) if name == "END" else text.find(s)
        if i >= 0:
            pos[name] = i
    if not pos:
        return None, "no_sentinel"
    for need in ("REVISED", "END"):
        if need not in pos:
            return None, f"missing_{need.lower()}_marker"
    present = [n for n in ORDER if n in pos]
    for a, b in zip(present, present[1:]):
        if pos[a] > pos[b]:
            return None, "sentinels_out_of_order"
    sec = {}
    for idx, name in enumerate(present[:-1]):
        start = pos[name] + len(SENT[name])
        end = pos[present[idx + 1]]
        sec[name] = text[start:end].strip("\n")
    return sec, None


def parse_status(body):
    out = {}
    for ln in body.split("\n"):
        if not ln.strip() or NONE_RE.match(ln):
            continue
        m = STATUS_RE.match(ln)
        if m:
            val = m.group(2).lower()
            out[m.group(1)] = {"status": val, "why": (m.group(3) or "").strip()}
    return out


def parse_bullets(body, tiers=True):
    items = []
    for ln in body.split("\n"):
        if not ln.strip():
            continue
        if NONE_RE.match(ln):
            continue
        m = GAP_RE.match(ln)
        if m and (ln.lstrip().startswith(("-", "*"))):
            tier = (m.group(1) or "blocking").lower() if tiers else None
            items.append({"tier": tier, "text": m.group(2).strip()})
        elif items:
            items[-1]["text"] += " " + ln.strip()
    return items


def patch_like(body):
    if re.search(r"^diff --git ", body, re.M) or re.search(r"^@@ .* @@", body, re.M):
        return True
    return bool(re.search(r"^--- \S", body, re.M) and re.search(r"^\+\+\+ \S", body, re.M))


# ------------------------------------------------------------------ commands
def cmd_init(a):
    run = a.run
    os.makedirs(run, exist_ok=True)
    for name, init in (("gaps.md", "none\n"), ("unresolved.md", "none\n"), ("verdicts.md", "")):
        p = os.path.join(run, name)
        if not os.path.exists(p):
            write(p, init)
    if not os.path.exists(os.path.join(run, "gaps.json")):
        save_state(run, {"version": 1, "gaps": {}, "seats": []})
    print("init ok")


def cmd_open_ids(a):
    print(",".join(sorted(open_blocking(load_state(a.run)), key=id_key)) or "none")


def log_failure(run, line):
    p = os.path.join(run, "failures.md")
    cur = read(p)
    write(p, cur + (line + "\n"))


def cmd_apply(a):
    held = os.path.join(a.run, f"seat-{a.seat}-revised.md")
    if not os.path.exists(held):
        fail(f"no held Revised for seat {a.seat}")
    write(os.path.join(a.run, "artifact.md"), read(held))
    os.remove(held)
    print(f"seat {a.seat}: held Revised applied to artifact.md")


def cmd_parse(a):
    run, seat, label = a.run, int(a.seat), a.label
    if not os.path.isdir(run):
        fail(f"run dir not found: {run}")
    raw = read(a.reply)
    text = unwrap(raw)
    st = load_state(run)
    prior_open = open_blocking(st)
    prior_open_all = [i for i, g in st["gaps"].items() if is_open(g)]

    sec, err = split_envelope(text)
    if err:
        return finish_bad(a, st, "malformed", err, 3)

    revised = sec["REVISED"].strip("\n")
    identity = revised.strip().lower() == "unchanged"
    if not identity and not revised.strip():
        return finish_bad(a, st, "malformed", "empty_revised", 3)
    if not identity and patch_like(revised):
        return finish_bad(a, st, "malformed", "patch_like_revised", 3)

    statuses = parse_status(sec.get("STATUS", ""))
    # illegal identity: identity + ordinary `addressed` on a currently open ID
    bad_ids = [i for i, s in statuses.items() if identity and s["status"] == "addressed" and i in prior_open_all]
    illegal = bool(bad_ids)
    if illegal and not a.final_attempt:
        return finish_bad(a, st, "illegal_identity", "UNCHANGED with addressed on " + ",".join(bad_ids), 4)

    # ---- mint gaps
    new_gaps = parse_bullets(sec.get("GAPS", ""))
    minted = []
    for n, g in enumerate(new_gaps, 1):
        gid = f"G{seat}-{n}"
        st["gaps"][gid] = {
            "seat": seat, "label": label, "tier": g["tier"], "text": g["text"],
            "status": "open", "history": [],
        }
        minted.append(gid)

    # ---- apply statuses (never from a rejected/invalid Revised)
    applied, ignored = {}, []
    for i, s in statuses.items():
        if i not in st["gaps"] or i in minted:
            ignored.append(i)
            continue
        val = s["status"]
        if val == "addressed" and identity:
            ignored.append(i)  # illegal on identity; only reachable with --final-attempt
            continue
        new = "addressed" if val.startswith("addressed") else val
        st["gaps"][i]["status"] = new
        st["gaps"][i]["history"].append({"seat": seat, "status": val, "why": s["why"]})
        applied[i] = new

    # ---- dissent check: open blocking IDs must appear in STATUS
    restored = [i for i in prior_open if i not in statuses]
    unresolved_items = parse_bullets(sec.get("UNRESOLVED", ""), tiers=False)
    if restored:
        for i in restored:
            st["gaps"][i]["status"] = "unresolved"
            st["gaps"][i]["history"].append({"seat": seat, "status": "restored", "why": "missing from STATUS"})

    # ---- write packets
    gap_lines = [f"- {gid} [{st['gaps'][gid]['tier']}] {st['gaps'][gid]['text']}" for gid in minted] or ["- none"]
    append_md(os.path.join(run, "gaps.md"), f"Seat {seat} — {label}", gap_lines)
    un_lines = [f"- {u['text']}" for u in unresolved_items] or ["- none"]
    append_md(os.path.join(run, "unresolved.md"), f"Seat {seat} — {label}", un_lines)
    if restored:
        rl = [f"- {i} [{st['gaps'][i]['tier']}] {st['gaps'][i]['text']}" for i in restored]
        append_md(os.path.join(run, "unresolved.md"), "Orchestrator — restored", rl)

    held = False
    if identity:
        pass
    elif a.hold_artifact:
        write(os.path.join(run, f"seat-{seat}-revised.md"), revised + "\n")
        held = True
    else:
        write(os.path.join(run, "artifact.md"), revised + "\n")

    if illegal:
        log_failure(run, f"seat {seat} ({label}): illegal-identity (statuses on {','.join(bad_ids)} ignored)")
    st["seats"].append({
        "seat": seat, "label": label, "at": now(), "revised": "UNCHANGED" if identity else ("held" if held else "replaced"),
        "role": a.role or "reviewer", "minted": minted, "restored": restored,
    })
    save_state(run, st)
    render_packets(run, st)

    n_block = sum(1 for g in new_gaps if g["tier"] == "blocking")
    n_opt = len(new_gaps) - n_block
    counts = {}
    for v in applied.values():
        counts[v] = counts.get(v, 0) + 1
    summary = {
        "status": "ok" if not illegal else "ok_illegal_identity_dropped",
        "seat": seat, "label": label, "revised": st["seats"][-1]["revised"],
        "gaps": {"blocking": n_block, "optional": n_opt, "ids": minted},
        "statuses": counts, "restored": restored, "ignored_status_ids": ignored,
        "open_blocking_after": len(open_blocking(st)),
        "unresolved_items": len(unresolved_items),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def finish_bad(a, st, status, reason, code):
    summary = {"status": status, "reason": reason, "seat": int(a.seat), "label": a.label,
               "action": "retry once with: return only the Council envelope" if not a.final_attempt
               else "keep last good artifact; log to failures.md; continue"}
    if a.final_attempt:
        log_failure(a.run, f"seat {a.seat} ({a.label}): {status} ({reason})")
    print(json.dumps(summary, ensure_ascii=False))
    return code


def main(argv=None):
    p = argparse.ArgumentParser(prog="council_parse")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--run", required=True)
    s = sub.add_parser("open-ids"); s.add_argument("--run", required=True)
    s = sub.add_parser("apply"); s.add_argument("--run", required=True); s.add_argument("--seat", required=True)
    s = sub.add_parser("parse")
    s.add_argument("--run", required=True); s.add_argument("--seat", required=True)
    s.add_argument("--label", required=True); s.add_argument("--reply", required=True)
    s.add_argument("--role", default=None)
    s.add_argument("--hold-artifact", action="store_true")
    s.add_argument("--final-attempt", action="store_true")
    a = p.parse_args(argv)
    return {"init": cmd_init, "open-ids": cmd_open_ids, "apply": cmd_apply, "parse": cmd_parse}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
