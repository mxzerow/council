#!/usr/bin/env python3
"""Council readability rewriter support (step 7).

The final stage asks a cheap model (Gemini Flash) to rewrite the integrated artifact
for a non-expert reader. A model with a ~64k output cap cannot be trusted with one
big call, and it must not change meaning. This script makes that safe:

  prepare   mask protected spans (code, tables, URLs, paths, IDs, quotes) as @@P<n>@@
            placeholders and split the artifact into chunks the model can finish
  check     validate one rewritten chunk (truncation, placeholders, numbers, headings,
            negations, length, added explanations); one retry, then keep the original
  assemble  stitch the accepted chunks, restore the protected text, verify the
            headings, and write artifact.md (or keep the pre-rewrite artifact)

The original text of any failed chunk is kept, and if more than half the chunks fall
back the whole pre-rewrite artifact is kept. Nothing is ever lost silently: the report
lists every addition the model made so a human can audit it.

Standard library only. Run with `python -I scripts/council_rewrite.py <command> ...`.

Commands
  prepare  --run DIR [--max-chars N]
  check    --run DIR --chunk I --reply FILE
  assemble --run DIR
  status   --run DIR

Exit codes: 0 ok; 3 `check` failed, retry once; 4 `check` failed again, original kept;
2 usage / IO error.
"""
import argparse
import json
import os
import re
import sys
import tempfile

DEFAULT_MAX_CHARS = 24000
LEN_MIN, LEN_MAX = 0.70, 2.20
MAX_ADDITION_CHARS = 140       # one explanation, not a paragraph
FALLBACK_ALL_RATIO = 0.5       # more than this share of chunks original -> keep the pre-rewrite artifact
WARN_RATIO = 0.3
PH = re.compile(r"@@P(\d+)@@")
ADD_OPEN, ADD_CLOSE = "{{+", "+}}"
ADD_RE = re.compile(re.escape(ADD_OPEN) + r"(.*?)" + re.escape(ADD_CLOSE), re.S)
END_RE = re.compile(r"<<<REWRITE_END\s+(\d+)>>>")
NEGATIONS = ["not", "no", "never", "must", "only", "unless", "except", "cannot", "without", "always", "required", "forbidden"]
NUM_RE = re.compile(r"(?<![\w.@])\d[\d,]*(?:\.\d+)?%?")
HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$", re.M)


class UsageError(Exception):
    pass


# ------------------------------------------------------------------ io
def read(path):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    except OSError as e:
        raise UsageError(str(e))


def write(path, text):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                import time
                time.sleep(0.02)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def rw_dir(run):
    return os.path.join(run, "rewrite")


def plan_path(run):
    return os.path.join(rw_dir(run), "plan.json")


def load_plan(run):
    p = plan_path(run)
    if not os.path.exists(p):
        raise UsageError("no rewrite plan; run `prepare` first")
    try:
        return json.loads(read(p))
    except ValueError as e:
        raise UsageError(f"rewrite plan is corrupt ({e})")


def save_plan(run, plan):
    write(plan_path(run), json.dumps(plan, ensure_ascii=False, indent=2) + "\n")


# ------------------------------------------------------------------ masking
FENCE = re.compile(r"^(`{3,}|~{3,})[^\n]*\n.*?^\1[ \t]*$", re.M | re.S)
TABLE = re.compile(r"(?:^[ \t]*\|[^\n]*\n?)+", re.M)
INLINE = re.compile(r"`[^`\n]+`")
URL = re.compile(r"https?://[^\s<>)\]]+")
IDS = [
    re.compile(r"\bG\d+-\d+\b"),                                   # gap IDs
    re.compile(r"\d{4}-\d{2}-\d{2}T[\d\-:]+"),                      # run ids / timestamps
    re.compile(r"(?<![\w/])#\d+\b"),                                # #123 references
    re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"),  # commit hashes
]
PATHS = [
    re.compile(r"\b[A-Za-z]:\\[^\s`]+"),                            # C:\path\to
    re.compile(r"(?<![\w@])(?:~|\.{1,2})?(?:[\\/][\w.\-]+)+(?::\d+)?"),  # /a/b, ./a/b, ~/a/b
    re.compile(r"\b[\w\-]+(?:[\\/][\w.\-]+)+(?::\d+)?"),            # a/b/c.py:12
    re.compile(r"\b[\w\-]+\.(?:md|py|js|ts|json|ya?ml|txt|toml|sh|ps1|html|css|sql|cfg|ini)\b(?::\d+)?"),
]
QUOTES = [re.compile(r'"[^"\n]{1,200}"'), re.compile("\u201c[^\u201d\n]{1,200}\u201d")]


def mask(text):
    """Return (masked_text, table) where table maps placeholder id -> original text."""
    table = {}

    def sub_with(pattern, s, trim=False):
        def r(m):
            span = m.group(0)
            tail = ""
            if trim:  # sentence punctuation after a URL or path belongs to the prose, not the span
                core = span.rstrip(".,;:!?)]}'\"")
                tail, span = span[len(core):], core
                if not span:
                    return m.group(0)
            n = len(table) + 1
            table[n] = span
            return f"@@P{n}@@{tail}"
        return pattern.sub(r, s)

    s = text
    s = sub_with(FENCE, s)
    s = sub_with(TABLE, s)
    s = sub_with(INLINE, s)
    s = sub_with(URL, s, trim=True)
    for p in IDS:
        s = sub_with(p, s)
    for p in PATHS:
        s = sub_with(p, s, trim=True)
    for p in QUOTES:
        s = sub_with(p, s)
    return s, table


def unmask(text, table):
    """Restore placeholders (recursively, since a quote can contain an earlier placeholder)."""
    for _ in range(10):
        new = PH.sub(lambda m: table.get(int(m.group(1)), m.group(0)), text)
        if new == text:
            return new
        text = new
    return text


# ------------------------------------------------------------------ chunking
def split_sections(masked):
    """Split at H2 headings. Returns a list of strings that concatenate back to masked."""
    lines = masked.split("\n")
    out, cur = [], []
    for ln in lines:
        if ln.startswith("## ") and cur:
            out.append("\n".join(cur) + "\n")
            cur = []
        cur.append(ln)
    out.append("\n".join(cur))
    return out


def split_oversize(section, max_chars):
    """Split an oversize section at H3, then at paragraph boundaries."""
    if len(section) <= max_chars:
        return [section]
    parts, cur = [], []
    for ln in section.split("\n"):
        if ln.startswith("### ") and cur:
            parts.append("\n".join(cur) + "\n")
            cur = []
        cur.append(ln)
    parts.append("\n".join(cur))
    out = []
    for p in parts:
        if len(p) <= max_chars:
            out.append(p)
            continue
        # paragraph units keep their trailing blank lines, so joining the units is lossless
        toks = re.split(r"(\n{2,})", p)
        units = ["".join(toks[k:k + 2]) for k in range(0, len(toks), 2)]
        buf = ""
        for unit in units:
            if buf and len(buf) + len(unit) > max_chars:
                out.append(buf)
                buf = ""
            buf += unit
            while len(buf) > max_chars:     # one enormous paragraph: hard split on a line boundary
                cut = buf.rfind("\n", 0, max_chars)
                cut = cut if cut > 0 else max_chars - 1
                out.append(buf[:cut + 1])
                buf = buf[cut + 1:]
        if buf:
            out.append(buf)
    return out


def make_chunks(masked, max_chars):
    pieces = []
    for sec in split_sections(masked):
        pieces += split_oversize(sec, max_chars)
    # pack small neighbours so we do not spend a model call on a tiny section
    chunks, buf = [], ""
    for p in pieces:
        if buf and len(buf) + len(p) > max_chars:
            chunks.append(buf)
            buf = ""
        buf += p
    if buf:
        chunks.append(buf)
    assert "".join(chunks) == masked, "chunking must be lossless"
    return chunks


# ------------------------------------------------------------------ checks
def strip_additions(text):
    return ADD_RE.sub("", text)


def visible_numbers(text):
    t = strip_additions(text)
    t = PH.sub(" ", t)
    t = re.sub(r"^[ \t]*\d+[.)][ \t]+", "", t, flags=re.M)   # list numbering is not content
    return set(m.group(0).rstrip(",") for m in NUM_RE.finditer(t))


def headings(text):
    """Heading list as written. Additions are NOT stripped: a heading that contains an
    addition marker must fail the chunk check, not the final assembly."""
    return [(len(m.group(1)), m.group(2)) for m in HEADING_RE.finditer(text)]


def keyword_counts(text):
    low = strip_additions(PH.sub(" ", text)).lower()
    return {k: len(re.findall(r"\b" + k + r"\b", low)) for k in NEGATIONS}


def check_chunk(src, out, index):
    """Return a list of reasons the rewritten chunk is unacceptable (empty list = accept)."""
    reasons = []
    if not out.strip():
        return ["empty_output: the CLI returned nothing (check stderr; classify it with council_resolve.py)"]
    m = None
    for m in END_RE.finditer(out):
        pass
    if not m or int(m.group(1)) != index:
        return ["truncated_or_no_end_marker"]
    body = out[:m.start()].strip("\n")
    if not body.strip():
        return ["empty_output"]
    s_ph = sorted(int(x) for x in PH.findall(src))
    o_ph = sorted(int(x) for x in PH.findall(body))
    if s_ph != o_ph:
        missing = sorted(set(s_ph) - set(o_ph))
        extra = sorted(set(o_ph) - set(s_ph))
        dup = sorted({x for x in o_ph if o_ph.count(x) > 1})
        reasons.append("placeholders: missing=%s extra=%s duplicated=%s" % (missing[:8], extra[:8], dup[:8]))
    a, b = visible_numbers(src), visible_numbers(body)
    if a != b:
        reasons.append("numbers: lost=%s added=%s" % (sorted(a - b)[:8], sorted(b - a)[:8]))
    for add in ADD_RE.findall(body):
        if re.search(r"\d", add):
            reasons.append("digit_in_addition: %r" % add[:60])
            break
    for add in ADD_RE.findall(body):
        if len(add.strip()) > MAX_ADDITION_CHARS:
            reasons.append("addition_too_long (%d chars; keep explanations to about 12 words): %r" % (len(add.strip()), add.strip()[:50]))
            break
    if body.count(ADD_OPEN) != body.count(ADD_CLOSE):
        reasons.append("unbalanced_addition_markers")
    elif re.search(r"\{\+|\+\}|\{\{|\}\}", ADD_RE.sub("", body)):
        # a near-miss such as {+ ... +} would slip through as unaudited prose
        reasons.append("stray_markers: additions must be wrapped exactly in {{+ and +}}")
    if headings(src) != headings(body):
        reasons.append("headings_changed")
    ks, ko = keyword_counts(src), keyword_counts(body)
    lost = [k for k in NEGATIONS if ks[k] > 0 and ko[k] < max(1, -(-ks[k] * 6 // 10))]
    if lost:
        reasons.append("negations_or_qualifiers_lost: " + ",".join(lost))
    ratio = len(body) / max(1, len(src))
    if not (LEN_MIN <= ratio <= LEN_MAX):
        reasons.append("length_ratio_%.2f_outside_%.2f-%.2f" % (ratio, LEN_MIN, LEN_MAX))
    return reasons


# ------------------------------------------------------------------ commands
HEADER = """You are rewriting ONE chunk of a longer document for readability.

Audience: an intelligent reader who is NOT a specialist in this subject.

Rules (all are checked by a script; a chunk that breaks one is thrown away):
1. Keep every claim, decision, number, name, recommendation and conclusion exactly. Add none, remove none, change none.
2. Your main job is to make the text understandable to someone who does not know this field. The first time a technical term, acronym, tool name or piece of jargon appears in the chunk, add a short plain-language explanation right after it (a clause or one sentence), for example: a hook {{+ (a small script that runs automatically when something happens) +}}. Wrap EVERY addition in {{+ and +}}. An addition must not contain any digit, new fact, new name, recommendation or opinion, and must never change what the sentence claims. Explain only terms a non-specialist would genuinely not know (skip plain words, names and anything already clear). Keep each explanation under about 12 words and put it after the WHOLE term, never inside a phrase, so the sentence still reads naturally. Explain a term once per chunk. A chunk full of unexplained jargon means the job was not done.
3. Keep every heading line exactly as written (same # level, same words) and in the same order.
4. A token like @@P12@@ stands for protected text (code, paths, links, IDs, quotes, tables). Keep each one exactly once, unchanged, where its meaning fits. Never add, remove or alter one.
5. Prefer short sentences, active voice and concrete words. Keep list structure. Keep every negation and condition (not, never, must, only, unless, except).
6. Output ONLY the rewritten chunk, then a final line exactly: <<<REWRITE_END {n}>>>
"""


def cmd_prepare(a):
    run = a.run
    art = os.path.join(run, "artifact.md")
    if not os.path.exists(art):
        raise UsageError("artifact.md not found")
    pre = os.path.join(run, "artifact-pre-rewrite.md")
    if not os.path.exists(pre):
        write(pre, read(art))
    original = read(pre)
    masked, table = mask(original)
    chunks = make_chunks(masked, a.max_chars)
    os.makedirs(rw_dir(run), exist_ok=True)
    meta = []
    for i, c in enumerate(chunks, 1):
        write(os.path.join(rw_dir(run), f"chunk-{i}-in.txt"), c)
        # NOT str.format(): the header contains literal {{+ and +}} markers that format() would collapse
        write(os.path.join(rw_dir(run), f"chunk-{i}-prompt.md"), HEADER.replace("{n}", str(i)) + f"\nChunk {i} of {len(chunks)}. The text to rewrite is below.\n\n-----\n{c}\n-----\n")
        meta.append({"i": i, "chars": len(c), "attempts": 0, "status": "pending", "reasons": []})
    save_plan(run, {"version": 1, "table": {str(k): v for k, v in table.items()}, "chunks": meta, "max_chars": a.max_chars,
                    "n_headings": len(headings(masked))})
    print(json.dumps({"status": "prepared", "chunks": len(chunks), "protected_spans": len(table),
                      "chars": len(original), "prompt_files": [f"rewrite/chunk-{i}-prompt.md" for i in range(1, len(chunks) + 1)]}))
    return 0


def cmd_check(a):
    plan = load_plan(a.run)
    i = a.chunk
    if not 1 <= i <= len(plan["chunks"]):
        raise UsageError(f"chunk {i} out of range 1..{len(plan['chunks'])}")
    meta = plan["chunks"][i - 1]
    if meta["status"] in ("accepted", "fallback"):
        raise UsageError(f"chunk {i} is already {meta['status']}")
    src = read(os.path.join(rw_dir(a.run), f"chunk-{i}-in.txt"))
    out = read(a.reply)
    meta["attempts"] += 1
    reasons = check_chunk(src, out, i)
    if not reasons:
        m = list(END_RE.finditer(out))[-1]
        # keep the chunk's own leading/trailing newlines so reassembly is seamless
        lead = len(src) - len(src.lstrip("\n"))
        trail = len(src) - len(src.rstrip("\n"))
        write(os.path.join(rw_dir(a.run), f"chunk-{i}-out.txt"), "\n" * lead + out[:m.start()].strip("\n") + "\n" * trail)
        meta["status"], meta["reasons"] = "accepted", []
        save_plan(a.run, plan)
        print(json.dumps({"status": "ok", "chunk": i, "attempts": meta["attempts"]}))
        return 0
    meta["reasons"] = reasons
    if meta["attempts"] >= 2:
        meta["status"] = "fallback"
        save_plan(a.run, plan)
        print(json.dumps({"status": "fallback", "chunk": i, "reasons": reasons, "action": "original text kept for this chunk"}))
        return 4
    meta["status"] = "retry"
    save_plan(a.run, plan)
    print(json.dumps({"status": "retry", "chunk": i, "reasons": reasons,
                      "action": "re-dispatch once; append these reasons to the prompt"}))
    return 3


def cmd_assemble(a):
    plan = load_plan(a.run)
    table = {int(k): v for k, v in plan["table"].items()}
    pre = read(os.path.join(a.run, "artifact-pre-rewrite.md"))
    masked, _ = mask(pre)
    chunks = make_chunks(masked, plan["max_chars"])
    n = len(chunks)
    pieces, additions, original_chunks = [], [], []
    for i, c in enumerate(chunks, 1):
        meta = plan["chunks"][i - 1]
        outp = os.path.join(rw_dir(a.run), f"chunk-{i}-out.txt")
        if meta["status"] == "accepted" and os.path.exists(outp):
            body = read(outp)
            for add in ADD_RE.findall(body):
                additions.append({"chunk": i, "text": " ".join(add.split())})
            pieces.append(ADD_RE.sub(lambda m: m.group(1).strip(), body))
        else:
            original_chunks.append(i)
            pieces.append(c)
    ratio = len(original_chunks) / max(1, n)
    report = {"chunks": n, "original_chunks": original_chunks, "original_ratio": round(ratio, 2),
              "additions": additions, "warning": None}
    if ratio > FALLBACK_ALL_RATIO:
        report["outcome"] = "kept_pre_rewrite_artifact"
        write(os.path.join(a.run, "rewrite-report.json"), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": "fallback_all", "original_chunks": len(original_chunks), "chunks": n,
                          "action": "artifact.md unchanged (pre-rewrite text kept)"}))
        return 0
    text = unmask("".join(pieces), table)
    problems = []
    if PH.search(text):
        problems.append("unrestored_placeholders")
    if headings(text) != headings(pre):
        problems.append("headings_differ_after_assembly")
    if problems:
        report["outcome"] = "kept_pre_rewrite_artifact"
        report["problems"] = problems
        write(os.path.join(a.run, "rewrite-report.json"), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": "fallback_all", "problems": problems, "action": "artifact.md unchanged"}))
        return 0
    if ratio > WARN_RATIO:
        report["warning"] = f"{len(original_chunks)} of {n} chunks kept their original wording"
    report["outcome"] = "rewritten"
    write(os.path.join(a.run, "artifact.md"), text)
    write(os.path.join(a.run, "rewrite-report.json"), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "rewritten", "chunks": n, "original_chunks": original_chunks,
                      "additions": len(additions), "warning": report["warning"]}))
    return 0


def cmd_status(a):
    plan = load_plan(a.run)
    counts = {}
    for c in plan["chunks"]:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    print(json.dumps({"chunks": len(plan["chunks"]), "by_status": counts,
                      "pending": [c["i"] for c in plan["chunks"] if c["status"] in ("pending", "retry")]}))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="council_rewrite")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prepare"); s.add_argument("--run", required=True); s.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    s = sub.add_parser("check"); s.add_argument("--run", required=True); s.add_argument("--chunk", required=True, type=int); s.add_argument("--reply", required=True)
    s = sub.add_parser("assemble"); s.add_argument("--run", required=True)
    s = sub.add_parser("status"); s.add_argument("--run", required=True)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    fn = {"prepare": cmd_prepare, "check": cmd_check, "assemble": cmd_assemble, "status": cmd_status}[a.cmd]
    try:
        return fn(a) or 0
    except (UsageError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
