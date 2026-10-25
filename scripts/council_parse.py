#!/usr/bin/env python3
"""Council envelope parser and run-state keeper.

Reads one seat's raw reply, parses the Council sentinel envelope, and does the
bookkeeping the orchestrator used to do by hand: mint gap IDs, update the verdict
ledger, restore dissent, rebuild the packet files. The orchestrator reads only the
short summary this script prints, never the full reply.

State model: `gaps.json` is the single source of truth. `gaps.md`, `unresolved.md`,
`verdicts.md` and `open-gaps.md` are rendered from it on every command, so an
interrupted run heals itself on the next call. The only other file a parse writes is
`artifact.md`, which is written before the state is committed.

Standard library only. Run with `python -I scripts/council_parse.py <command> ...`.

Commands
  init        --run DIR
  open-ids    --run DIR                          open blocking IDs, comma separated
  seat-state  --run DIR --seat N                 whether the seat has a parsed review (JSON)
  parse       --run DIR --seat N --label TEXT --reply FILE
              [--role ROLE] [--hold-artifact] [--final-attempt]
  apply       --run DIR --seat N                 commit a held seat (Revised + its status changes)
  reject      --run DIR --seat N                 discard a held seat's Revised and status changes

Exit codes of `parse`
  0 ok                       3 malformed (retry once)
  4 illegal identity (retry once; --final-attempt keeps identity, drops statuses)
  2 usage / IO / corrupt-state error
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
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
    r"^\s*(G\d+-\d+)\s*:\s*(addressed \(pre-existing\)|addressed|unresolved|rejected)(?=\s|$|[:—–-])"
    r"\s*(?:[:—–-]\s*(.*))?$",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
TIER_RE = re.compile(r"^\*{0,2}(blocking|optional)\*{0,2}\s*:\s*(.*)$", re.IGNORECASE)
NONE_RE = re.compile(r"^\s*(?:[-*+]\s*|\d+[.)]\s*)?\**none\.?\**\s*$", re.IGNORECASE)


class UsageError(Exception):
    """Usage, IO or corrupt-state problem: exit code 2."""


def read(path, default=""):
    if not os.path.exists(path):
        return default
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def write(path, text):
    d = os.path.dirname(path) or "."
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
    if not os.path.exists(p):
        return {"version": 1, "gaps": {}, "seats": []}
    try:
        st = json.loads(read(p))
        assert isinstance(st, dict) and "gaps" in st and "seats" in st
        return st
    except (ValueError, AssertionError) as e:
        raise UsageError(f"gaps.json is corrupt ({e}); restore it before continuing")


def save_state(run, st):
    write(os.path.join(run, "gaps.json"), json.dumps(st, ensure_ascii=False, indent=2) + "\n")


def is_open(g):
    return g["status"] != "addressed"


def open_blocking(st):
    return [i for i, g in st["gaps"].items() if g["tier"] == "blocking" and is_open(g)]


def id_key(i):
    a, b = i[1:].split("-")
    return (int(a), int(b))


def render_all(run, st):
    """Render every derived packet file from gaps.json (idempotent)."""
    ids = sorted(st["gaps"], key=id_key)
    # gaps.md and unresolved.md: one section per seat, in order
    gl, ul = [], []
    for s in st["seats"]:
        head = f"### Seat {s['seat']} — {s['label']}" + (" (revised rejected)" if s.get("revised") == "rejected" else "")
        gl.append(head)
        gl += [f"- {i} [{st['gaps'][i]['tier']}] {st['gaps'][i]['text']}" for i in s.get("minted", [])] or ["- none"]
        ul.append(head)
        ul += [f"- {t}" for t in s.get("unresolved", [])] or ["- none"]
        if s.get("restored") and s.get("revised") != "held":
            ul.append("### Orchestrator — restored")
            ul += [f"- {i} [{st['gaps'][i]['tier']}] {st['gaps'][i]['text']}" for i in s["restored"]]
    write(os.path.join(run, "gaps.md"), ("\n".join(gl) + "\n") if gl else "none\n")
    write(os.path.join(run, "unresolved.md"), ("\n".join(ul) + "\n") if ul else "none\n")
    write(os.path.join(run, "verdicts.md"), "".join(f"{i}: {st['gaps'][i]['status']} ({st['gaps'][i]['tier']})\n" for i in ids))
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
        note = ""
        if last:
            note = f" — latest: {last['status']}" + (f" ({last['why']})" if last.get("why") else "")
        lines.append(f"- {i} [{g['tier']}] (seat {g['seat']}, {g['label']}){note}: {g['text']}")
    write(p, "\n".join(lines) + "\n")


def log_failure(run, line):
    p = os.path.join(run, "failures.md")
    data = (line + "\n").encode("utf-8")
    if sys.platform != "win32":
        # O_APPEND and a single write preserve concurrent records on POSIX.
        with open(p, "ab", buffering=0) as f:
            f.write(data)
        return
    import msvcrt
    # Windows append positioning needs serialization across processes. Keep the
    # lock file in place: unlinking it would let writers lock different files.
    with open(p + ".lock", "a+b") as lock:
        for attempt in range(200):
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if attempt == 199:
                    raise
                time.sleep(0.01)
        try:
            with open(p, "ab", buffering=0) as f:
                f.write(data)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def must_dir(run):
    if not os.path.isdir(run):
        raise UsageError(f"run dir not found: {run}")


# ------------------------------------------------------------------ parsing
def unwrap(raw):
    """Accept a plain-text reply or a JSON wrapper from a CLI (`text` or `result`)."""
    s = raw.lstrip()
    if s.startswith("{"):
        try:
            j = json.loads(s)
            for k in ("response", "text", "result"):    # agy, Grok CLI, others
                if isinstance(j, dict) and isinstance(j.get(k), str):
                    return j[k]
        except ValueError:
            pass
    return raw


def _find_after(text, s, start, anchored_only=False):
    """Next occurrence of sentinel s at or after start. A marker alone on its line is the real one;
    a quoted mention inside prose (a gap that discusses the protocol) is only a fallback."""
    m = re.compile(r"(?m)^[ \t]*" + re.escape(s) + r"[ \t]*$").search(text, start)
    if m:
        return m.start() + (m.group(0).index(s))
    if anchored_only:
        return -1
    return text.find(s, start)


def split_envelope(text):
    pos, cur = {}, 0
    for name in ORDER[:-1]:
        # search forward only, so the sections are in order by construction
        i = _find_after(text, SENT[name], cur)
        if i >= 0:
            pos[name] = i
            cur = i + len(SENT[name])
    # END: the last marker alone on its line, else the last occurrence anywhere (the last END wins)
    ends = [m.start() + m.group(0).index(SENT["END"]) for m in re.finditer(r"(?m)^[ \t]*" + re.escape(SENT["END"]) + r"[ \t]*$", text)]
    end = ends[-1] if ends else text.rfind(SENT["END"])
    if end >= 0 and end >= cur:
        pos["END"] = end
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
        sec[name] = text[pos[name] + len(SENT[name]):pos[present[idx + 1]]].strip("\n")
    return sec, None


def norm_status_line(ln):
    s = ln.strip()
    if s.startswith("|"):
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 2:
            s = f"{cells[0]}: {cells[1]}" + (f" — {cells[2]}" if len(cells) > 2 and cells[2] else "")
    s = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", s)
    return re.sub(r"[*_`]", "", s)


def parse_status(body):
    out = {}
    for ln in body.split("\n"):
        if not ln.strip() or NONE_RE.match(ln):
            continue
        m = STATUS_RE.match(norm_status_line(ln))
        if m:
            out[m.group(1).upper()] = {"status": m.group(2).lower(), "why": (m.group(3) or "").strip()}
    return out


def parse_bullets(body, tiers=True):
    """Bullets (-, *, +, 1., 1)). Unbulleted prose with real content becomes one item
    so a claim is never silently lost."""
    items = []
    loose = []
    for ln in body.split("\n"):
        if not ln.strip() or NONE_RE.match(ln):
            continue
        m = BULLET_RE.match(ln)
        if m:
            txt = m.group(1).strip()
            tier = "blocking"
            tm = TIER_RE.match(txt)
            if tm:
                tier, txt = tm.group(1).lower(), tm.group(2).strip()
            items.append({"tier": tier if tiers else None, "text": txt})
        elif items:
            items[-1]["text"] += " " + ln.strip()
        else:
            loose.append(ln.strip())
    if not items and loose:
        txt = " ".join(loose)
        tm = TIER_RE.match(txt)
        tier = "blocking"
        if tm:
            tier, txt = tm.group(1).lower(), tm.group(2).strip()
        items.append({"tier": tier if tiers else None, "text": txt})
    return items


def patch_like(body):
    if re.search(r"^\*\*\* (Begin Patch|Update File:|Add File:|Delete File:)", body, re.M):
        return True
    if re.search(r"^diff --git ", body, re.M) or re.search(r"^@@ .* @@", body, re.M):
        return True
    return bool(re.search(r"^--- \S", body, re.M) and re.search(r"^\+\+\+ \S", body, re.M))


# ------------------------------------------------------------------ commands
def cmd_init(a):
    os.makedirs(a.run, exist_ok=True)
    if not os.path.exists(os.path.join(a.run, "gaps.json")):
        save_state(a.run, {"version": 1, "gaps": {}, "seats": []})
    render_all(a.run, load_state(a.run))
    print("init ok")
    return 0


def cmd_open_ids(a):
    must_dir(a.run)
    st = load_state(a.run)
    render_all(a.run, st)  # self-heal derived files
    print(",".join(sorted(open_blocking(st), key=id_key)) or "none")
    return 0


def pending_path(run, seat):
    return os.path.join(run, f"seat-{seat}-pending.json")


def revised_path(run, seat):
    return os.path.join(run, f"seat-{seat}-revised.md")


def seat_entry(st, seat):
    for s in st["seats"]:
        if s["seat"] == seat:
            return s
    return None


def apply_changes(st, seat, statuses, restored):
    """Apply status transitions and dissent restorations (idempotent)."""
    for ch in statuses:
        g = st["gaps"].get(ch["id"])
        if not g:
            continue
        new = "addressed" if ch["status"].startswith("addressed") else ch["status"]
        if g["history"] and g["history"][-1] == {"seat": seat, "status": ch["status"], "why": ch["why"]}:
            continue
        g["status"] = new
        g["history"].append({"seat": seat, "status": ch["status"], "why": ch["why"]})
    for i in restored:
        g = st["gaps"].get(i)
        if g and not (g["history"] and g["history"][-1].get("status") == "restored" and g["history"][-1]["seat"] == seat):
            g["status"] = "unresolved"
            g["history"].append({"seat": seat, "status": "restored", "why": "missing from STATUS"})


def cmd_apply(a):
    must_dir(a.run)
    seat = a.seat
    st = load_state(a.run)
    pend_f, rev_f = pending_path(a.run, seat), revised_path(a.run, seat)
    entry = seat_entry(st, seat)
    if entry is None or entry.get("revised") != "held":
        raise UsageError(f"seat {seat} has no held revision")
    pend = json.loads(read(pend_f, "{}") or "{}")
    if os.path.exists(rev_f):
        write(os.path.join(a.run, "artifact.md"), read(rev_f))
    apply_changes(st, seat, pend.get("statuses", []), pend.get("restored", []))
    entry["revised"] = "replaced"
    save_state(a.run, st)
    render_all(a.run, st)
    for f in (rev_f, pend_f):
        if os.path.exists(f):
            os.remove(f)
    print(json.dumps({"status": "applied", "seat": seat, "open_blocking_after": len(open_blocking(st))}))
    return 0


def cmd_reject(a):
    must_dir(a.run)
    seat = a.seat
    st = load_state(a.run)
    entry = seat_entry(st, seat)
    if entry is None or entry.get("revised") != "held":
        raise UsageError(f"seat {seat} has no held revision")
    entry["revised"] = "rejected"
    save_state(a.run, st)
    render_all(a.run, st)
    for f in (revised_path(a.run, seat), pending_path(a.run, seat)):
        if os.path.exists(f):
            os.remove(f)
    log_failure(a.run, f"seat {seat} ({entry['label']}): revised-rejected (status changes not applied)")
    print(json.dumps({"status": "rejected", "seat": seat, "open_blocking_after": len(open_blocking(st))}))
    return 0


def finish_bad(a, status, reason, code):
    summary = {"status": status, "reason": reason, "seat": a.seat, "label": a.label,
               "action": ("retry once with: return only the Council envelope" if reason != "revised_not_allowed"
                          else "retry once: this seat is gaps-only, Revised must be exactly UNCHANGED; put every change under Gaps")
               if not a.final_attempt else "keep last good artifact; logged to failures.md; continue"}
    if a.final_attempt:
        log_failure(a.run, f"seat {a.seat} ({a.label}): {status} ({reason})")
    print(json.dumps(summary, ensure_ascii=False))
    return code


def cmd_parse(a):
    must_dir(a.run)
    run, seat, label = a.run, a.seat, a.label
    if not os.path.isfile(a.reply):
        raise UsageError(f"reply file not found: {a.reply}")
    st = load_state(run)
    render_all(run, st)  # self-heal derived files after an interrupted run
    if seat_entry(st, seat) is not None:
        raise UsageError(f"seat {seat} was already parsed; use a new seat number for a new dispatch")
    text = unwrap(read(a.reply))
    prior_open = open_blocking(st)
    prior_open_all = [i for i, g in st["gaps"].items() if is_open(g)]

    sec, err = split_envelope(text)
    if err:
        return finish_bad(a, "malformed", err, 3)
    revised = sec["REVISED"].strip("\n")
    identity = revised.strip().lower() == "unchanged"
    if not identity and not revised.strip():
        return finish_bad(a, "malformed", "empty_revised", 3)
    if not identity and patch_like(revised):
        return finish_bad(a, "malformed", "patch_like_revised", 3)
    # Gaps-only: only the integrator may return a changed artifact.
    role = (a.role or "reviewer").lower()
    if not identity and role != "integrator":
        if not a.final_attempt:
            return finish_bad(a, "malformed", "revised_not_allowed", 3)
        log_failure(run, f"seat {seat} ({label}): revised-not-allowed (role {role}; Revised ignored, gaps kept)")
        identity, revised = True, "UNCHANGED"

    statuses = parse_status(sec.get("STATUS", ""))
    bad_ids = [i for i, s in statuses.items() if identity and s["status"] == "addressed" and i in prior_open_all]
    illegal = bool(bad_ids)
    if illegal and not a.final_attempt:
        return finish_bad(a, "illegal_identity", "UNCHANGED with addressed on " + ",".join(bad_ids), 4)

    # ---- mint gaps (always committed, even when the Revised is held)
    minted = []
    for n, g in enumerate(parse_bullets(sec.get("GAPS", "")), 1):
        gid = f"G{seat}-{n}"
        st["gaps"][gid] = {"seat": seat, "label": label, "tier": g["tier"], "text": g["text"],
                           "status": "open", "history": []}
        minted.append(gid)

    # ---- status changes and dissent restoration
    changes, ignored = [], []
    for i, s in statuses.items():
        if i not in st["gaps"] or i in minted:
            ignored.append(i)
            continue
        if s["status"] == "addressed" and identity:
            ignored.append(i)  # illegal on identity; only reachable with --final-attempt
            continue
        changes.append({"id": i, "status": s["status"], "why": s["why"]})
    restored = [i for i in prior_open if i not in statuses]
    unresolved_items = [u["text"] for u in parse_bullets(sec.get("UNRESOLVED", ""), tiers=False)]

    held = False
    if identity:
        rev_state = "UNCHANGED"
    elif a.hold_artifact:
        rev_state, held = "held", True
    else:
        rev_state = "replaced"

    # ---- commit: artifact first, then state, then derived files
    if held:
        write(revised_path(run, seat), revised + "\n")
        write(pending_path(run, seat), json.dumps({"statuses": changes, "restored": restored}, ensure_ascii=False))
    elif not identity:
        write(os.path.join(run, "artifact.md"), revised + "\n")
    if not held:
        apply_changes(st, seat, changes, restored)
    if illegal:
        log_failure(run, f"seat {seat} ({label}): illegal-identity (statuses on {','.join(bad_ids)} ignored)")
    st["seats"].append({"seat": seat, "label": label, "at": now(), "revised": rev_state,
                        "role": a.role or "reviewer", "minted": minted,
                        "restored": restored, "unresolved": unresolved_items})
    save_state(run, st)
    render_all(run, st)

    n_block = sum(1 for i in minted if st["gaps"][i]["tier"] == "blocking")
    counts = {}
    for c in changes:
        k = "addressed" if c["status"].startswith("addressed") else c["status"]
        counts[k] = counts.get(k, 0) + 1
    print(json.dumps({
        "status": "ok" if not illegal else "ok_illegal_identity_dropped",
        "seat": seat, "label": label, "revised": rev_state,
        "gaps": {"blocking": n_block, "optional": len(minted) - n_block, "ids": minted},
        "statuses": counts, "restored": restored, "ignored_status_ids": ignored,
        "pending_until_apply": bool(held),
        "open_blocking_after": len(open_blocking(st)),
        "unresolved_items": len(unresolved_items),
    }, ensure_ascii=False))
    return 0


def cmd_seat_state(a):
    must_dir(a.run)
    entry = seat_entry(load_state(a.run), a.seat)
    print(json.dumps({"seat": a.seat, "parsed": entry is not None}))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="council_parse")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--run", required=True)
    s = sub.add_parser("open-ids"); s.add_argument("--run", required=True)
    for name in ("apply", "reject", "seat-state"):
        s = sub.add_parser(name); s.add_argument("--run", required=True); s.add_argument("--seat", required=True, type=int)
    s = sub.add_parser("parse")
    s.add_argument("--run", required=True); s.add_argument("--seat", required=True, type=int)
    s.add_argument("--label", required=True); s.add_argument("--reply", required=True)
    s.add_argument("--role", default=None, help="reviewer (default) | breadth-auditor | integrator; only integrator may return a changed Revised")
    s.add_argument("--hold-artifact", action="store_true")
    s.add_argument("--final-attempt", action="store_true")
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)  # argparse exits 2 on bad usage (e.g. non-integer seat)
    fn = {"init": cmd_init, "open-ids": cmd_open_ids, "apply": cmd_apply, "reject": cmd_reject,
          "parse": cmd_parse, "seat-state": cmd_seat_state}[a.cmd]
    try:
        return fn(a) or 0
    except (UsageError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
