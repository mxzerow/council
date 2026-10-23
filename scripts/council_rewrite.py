#!/usr/bin/env python3
"""Council readability rewriter support.

The final stage asks a cheap model (Gemini Flash) to rewrite the integrated artifact
for a non-expert reader. A model with a ~64k output cap cannot be trusted with one
big call, and it must not change meaning. This script makes that safe:

  prepare   mask protected spans (code, tables, URLs, paths, IDs, quotes) as placeholders
            and split the artifact into chunks the model can finish
  check     validate one rewritten chunk (truncation, placeholders, numbers, headings,
            negations, length, added explanations); one retry, then keep the original
  assemble  stitch the accepted chunks, restore the protected text, verify the
            headings, and write artifact.md (or restore the pre-rewrite artifact)

The original text of any failed chunk is kept, and if more than half the chunks fall
back the whole pre-rewrite artifact is restored. Nothing is lost silently: the report
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
import shutil
import sys
import tempfile
import time

DEFAULT_MAX_CHARS = 24000
LEN_MIN, LEN_MAX = 0.70, 2.20
MAX_ADDITION_CHARS = 140       # one explanation, not a paragraph
FALLBACK_ALL_RATIO = 0.5       # more than this share of chunks original -> restore the pre-rewrite artifact
WARN_RATIO = 0.3
ADD_OPEN, ADD_CLOSE = "{{+", "+}}"
ADD_RE = re.compile(re.escape(ADD_OPEN) + r"(.*?)" + re.escape(ADD_CLOSE), re.S)
END_RE = re.compile(r"<<<REWRITE_END\s+(\d+)>>>")
# Negations must all survive (as a class: "must not" -> "cannot" is fine); qualifiers are checked loosely.
NEG_RE = re.compile(r"\b(?:not|no|never|cannot|without|nor|none|neither)\b|n't\b")
QUALIFIERS = ["must", "only", "unless", "except", "always", "required", "forbidden"]
# a number: optional sign (only when not glued to a word, so "3-5" is two unsigned numbers), dotted versions, %
NUM_RE = re.compile(r"(?<![\w.@])([-+]?)(\d[\d,]*(?:\.\d+)*%?)")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(\S.*?)[ \t]*$", re.M)
SETEXT_RE = re.compile(r"^([^\s#>|*+\-`~=@][^\n]*)\n(=+|-+)[ \t]*$", re.M)


class UsageError(Exception):
    pass


# ------------------------------------------------------------------ io
def normalize(raw):
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def read(path):
    try:
        with open(path, "rb") as f:
            return normalize(f.read())
    except OSError as e:
        raise UsageError(str(e))


def write(path, text, eol="lf"):
    if eol == "crlf":
        text = text.replace("\n", "\r\n")
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        for attempt in range(20):   # Windows denies the replace while a reader has the target open
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
def pick_prefix(text):
    """A placeholder prefix that does not occur anywhere in the document, so literal
    '@@P1@@'-looking text can never be mistaken for a placeholder."""
    p = "@@P"
    while p in text:
        p += "P"
    return p


def ph_re(prefix):
    return re.compile(re.escape(prefix) + r"(\d+)@@")


FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
TABLE = re.compile(r"(?:^[ \t]*\|[^\n]*\n?)+", re.M)
INLINE = re.compile(r"(?<!`)(`+)(?!`)[^\n]*?(?<!`)\1(?!`)")   # closing run must match the opening run's length
URL = re.compile(r"https?://[^\s<>)\]]+")
IDS = [
    re.compile(r"\bG\d+-\d+\b"),                                              # gap IDs
    re.compile(r"\d{4}-\d{2}-\d{2}T[\d\-:]+"),                                # run ids / timestamps
    re.compile(r"(?<![\w/])#\d+\b"),                                          # #123 references
    re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"),       # commit hashes
]
PATHS = [
    re.compile(r"\b[A-Za-z]:\\[^\s`]+"),                                      # C:\path\to
    re.compile(r"(?<![\w@])(?:~|\.{1,2})?(?:[\\/][\w.\-]+)+(?::\d+)?"),       # /a/b, ./a/b, ~/a/b
    re.compile(r"\b[\w\-]+(?:[\\/][\w.\-]+)+(?::\d+)?"),                      # a/b/c.py:12
    re.compile(r"\b[\w\-]+\.(?:md|py|js|ts|json|ya?ml|txt|toml|sh|ps1|html|css|sql|cfg|ini)\b(?::\d+)?"),
]
QUOTES = [re.compile(r'"[^"\n]{1,200}"'), re.compile("\u201c[^\u201d\n]{1,200}\u201d")]


def mask_code_blocks(text, put):
    """Linear scan: fenced blocks (any indent up to 3, closing fence at least as long, an
    unclosed fence runs to the end) and indented code blocks become one placeholder each."""
    lines = text.split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        ln = lines[i]
        m = FENCE_OPEN.match(ln)
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            ch, size = m.group(1)[0], len(m.group(1))
            close = re.compile(r"^ {0,3}" + re.escape(ch) + "{%d,}[ \\t]*$" % size)
            j = i + 1
            while j < n and not close.match(lines[j]):
                j += 1
            end = j if j < n else n - 1
            out.append(put("\n".join(lines[i:end + 1])))
            i = end + 1
            continue
        if ln.strip() and (ln.startswith("    ") or ln.startswith("\t")) and (i == 0 or not lines[i - 1].strip()):
            j = i
            while j + 1 < n and (not lines[j + 1].strip() or lines[j + 1].startswith(("    ", "\t"))):
                j += 1
            while j > i and not lines[j].strip():
                j -= 1                               # trailing blank lines are not part of the block
            out.append(put("\n".join(lines[i:j + 1])))
            i = j + 1
            continue
        out.append(ln)
        i += 1
    return "\n".join(out)


def mask(text):
    """Return (masked_text, table, prefix). table maps placeholder id -> original text."""
    prefix = pick_prefix(text)
    table = {}

    def put(span):
        n = len(table) + 1
        table[n] = span
        return f"{prefix}{n}@@"

    def sub_with(pattern, s, trim=False):
        def r(m):
            span, tail = m.group(0), ""
            if trim:  # sentence punctuation after a URL or path belongs to the prose, not the span
                core = span.rstrip(".,;:!?)]}'\"")
                tail, span = span[len(core):], core
                if not span:
                    return m.group(0)
            return put(span) + tail
        return pattern.sub(r, s)

    s = mask_code_blocks(text, put)
    s = sub_with(TABLE, s)
    s = sub_with(INLINE, s)
    s = sub_with(URL, s, trim=True)
    for p in IDS:
        s = sub_with(p, s)
    for p in PATHS:
        s = sub_with(p, s, trim=True)
    for p in QUOTES:
        s = sub_with(p, s)
    return s, table, prefix


def unmask(text, table, prefix="@@P"):
    """Restore placeholders (repeatedly, since a quote can contain an earlier placeholder)."""
    ph = ph_re(prefix)
    for _ in range(10):
        new = ph.sub(lambda m: table.get(int(m.group(1)), m.group(0)), text)
        if new == text:
            return new
        text = new
    return text


# ------------------------------------------------------------------ chunking
def split_sections(masked):
    """Split at H2 headings. Returns pieces that concatenate back to masked."""
    out, cur = [], []
    for ln in masked.split("\n"):
        if ln.startswith("## ") and cur:
            out.append("\n".join(cur) + "\n")
            cur = []
        cur.append(ln)
    out.append("\n".join(cur))
    return out


def safe_cut(buf, cut, prefix):
    """Move a cut point out of any placeholder so a token is never split."""
    for m in ph_re(prefix).finditer(buf, max(0, cut - 40)):
        if m.start() < cut < m.end():
            return m.start() if m.start() > 0 else m.end()
        if m.start() >= cut:
            break
    return cut


def split_oversize(section, max_chars, prefix):
    """Split an oversize section at H3, then at paragraph boundaries, then on a line boundary."""
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
        toks = re.split(r"(\n{2,})", p)       # paragraph units keep their blank lines: joining is lossless
        units = ["".join(toks[k:k + 2]) for k in range(0, len(toks), 2)]
        buf = ""
        for unit in units:
            if buf and len(buf) + len(unit) > max_chars:
                out.append(buf)
                buf = ""
            buf += unit
            while len(buf) > max_chars:       # one enormous paragraph: hard split, never inside a placeholder
                nl = buf.rfind("\n", 0, max_chars)
                cut = nl + 1 if nl > 0 else safe_cut(buf, max_chars, prefix)
                cut = cut if cut > 0 else safe_cut(buf, max_chars, prefix) or max_chars
                out.append(buf[:cut])
                buf = buf[cut:]
        if buf:
            out.append(buf)
    return out


def make_chunks(masked, max_chars, prefix="@@P"):
    pieces = []
    for sec in split_sections(masked):
        pieces += split_oversize(sec, max_chars, prefix)
    chunks, buf = [], ""                       # pack small neighbours so a tiny section costs no model call
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


def visible_numbers(text, prefix="@@P"):
    t = strip_additions(text)
    t = ph_re(prefix).sub(" ", t)
    t = re.sub(r"^[ \t]*\d+[.)][ \t]+", "", t, flags=re.M)   # list numbering is not content
    return set(m.group(1) + m.group(2).rstrip(",") for m in NUM_RE.finditer(t))


def headings(text):
    """Headings as written, ATX and setext. Additions are NOT stripped: a heading that
    contains an addition marker must fail the chunk check, not the final assembly."""
    found = [(m.start(), len(m.group(1)), m.group(2)) for m in HEADING_RE.finditer(text)]
    found += [(m.start(), 1 if m.group(2)[0] == "=" else 2, m.group(1).strip()) for m in SETEXT_RE.finditer(text)]
    return [(lvl, txt) for _, lvl, txt in sorted(found)]


def negation_count(text, prefix="@@P"):
    low = strip_additions(ph_re(prefix).sub(" ", text)).lower()
    return len(NEG_RE.findall(low))


def qualifier_counts(text, prefix="@@P"):
    low = strip_additions(ph_re(prefix).sub(" ", text)).lower()
    return {k: len(re.findall(r"\b" + k + r"\b", low)) for k in QUALIFIERS}


def check_chunk(src, out, index, prefix="@@P"):
    """Return the reasons the rewritten chunk is unacceptable (empty list = accept)."""
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
    reasons = []
    ph = ph_re(prefix)
    s_ph = sorted(int(x) for x in ph.findall(src))
    o_ph = sorted(int(x) for x in ph.findall(body))
    if s_ph != o_ph:
        missing = sorted(set(s_ph) - set(o_ph))
        extra = sorted(set(o_ph) - set(s_ph))
        dup = sorted({x for x in o_ph if o_ph.count(x) > 1})
        reasons.append("placeholders: missing=%s extra=%s duplicated=%s" % (missing[:8], extra[:8], dup[:8]))
    elif body.count(prefix) != len(o_ph):
        reasons.append("placeholders: a placeholder was altered or broken")
    a, b = visible_numbers(src, prefix), visible_numbers(body, prefix)
    if a != b:
        reasons.append("numbers: lost=%s added=%s" % (sorted(a - b)[:8], sorted(b - a)[:8]))
    adds = ADD_RE.findall(body)
    for add in adds:
        if re.search(r"\d", add):
            reasons.append("digit_in_addition: %r" % add[:60])
            break
    for add in adds:
        if len(add.strip()) > MAX_ADDITION_CHARS:
            reasons.append("addition_too_long (%d chars; keep explanations to about 12 words): %r" % (len(add.strip()), add.strip()[:50]))
            break
    if body.count(ADD_OPEN) != body.count(ADD_CLOSE):
        reasons.append("unbalanced_addition_markers")
    elif re.search(r"\{\+|\+\}|\{\{|\}\}", ADD_RE.sub("", body)):
        reasons.append("stray_markers: additions must be wrapped exactly in {{+ and +}}")   # a near-miss would pass as unaudited prose
    if headings(src) != headings(body):
        reasons.append("headings_changed")
    ns, no = negation_count(src, prefix), negation_count(body, prefix)
    if no < ns:
        reasons.append("negations_lost: %d in the source, %d in the rewrite" % (ns, no))
    qs, qo = qualifier_counts(src, prefix), qualifier_counts(body, prefix)
    lost = [k for k in QUALIFIERS if qs[k] > 0 and qo[k] < max(1, -(-qs[k] * 6 // 10))]
    if lost:
        reasons.append("qualifiers_lost: " + ",".join(lost))
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
4. A token like @@P12@@ stands for protected text (code, paths, links, IDs, quotes, tables). Keep each one exactly once, unchanged, where its meaning fits. Never add, remove, split or alter one.
5. Prefer short sentences, active voice and concrete words. Keep list structure. Keep every negation and condition (not, never, must, only, unless, except).
6. Output ONLY the rewritten chunk, then a final line exactly: <<<REWRITE_END {n}>>>
"""


def detect_eol(raw):
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    return "crlf" if crlf > lf else "lf"


def cmd_prepare(a):
    run = a.run
    art = os.path.join(run, "artifact.md")
    if not os.path.exists(art):
        raise UsageError("artifact.md not found")
    pre = os.path.join(run, "artifact-pre-rewrite.md")
    if not os.path.exists(pre):
        shutil.copyfile(art, pre)                    # raw bytes: the snapshot is byte-exact
    with open(pre, "rb") as f:
        raw = f.read()
    eol = detect_eol(raw)
    original = normalize(raw)
    masked, table, prefix = mask(original)
    chunks = make_chunks(masked, a.max_chars, prefix)
    shutil.rmtree(rw_dir(run), ignore_errors=True)    # a fresh prepare discards earlier chunk results
    os.makedirs(rw_dir(run), exist_ok=True)
    meta = []
    header = HEADER.replace("@@P12@@", f"{prefix}12@@")
    for i, c in enumerate(chunks, 1):
        write(os.path.join(rw_dir(run), f"chunk-{i}-in.txt"), c)
        # NOT str.format(): the header contains literal {{+ and +}} markers that format() would collapse
        write(os.path.join(rw_dir(run), f"chunk-{i}-prompt.md"),
              header.replace("{n}", str(i)) + f"\nChunk {i} of {len(chunks)}. The text to rewrite is below.\n\n-----\n{c}\n-----\n")
        meta.append({"i": i, "chars": len(c), "attempts": 0, "status": "pending", "reasons": []})
    save_plan(run, {"version": 2, "table": {str(k): v for k, v in table.items()}, "prefix": prefix, "eol": eol,
                    "chunks": meta, "max_chars": a.max_chars})
    print(json.dumps({"status": "prepared", "chunks": len(chunks), "protected_spans": len(table),
                      "chars": len(original), "prefix": prefix,
                      "prompt_files": [f"rewrite/chunk-{i}-prompt.md" for i in range(1, len(chunks) + 1)]}))
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
    reasons = check_chunk(src, out, i, plan["prefix"])
    if not reasons:
        m = list(END_RE.finditer(out))[-1]
        lead = len(src) - len(src.lstrip("\n"))     # keep the chunk's own edge newlines so reassembly is seamless
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


def restore_original(run):
    shutil.copyfile(os.path.join(run, "artifact-pre-rewrite.md"), os.path.join(run, "artifact.md"))


def cmd_assemble(a):
    plan = load_plan(a.run)
    prefix, eol = plan["prefix"], plan.get("eol", "lf")
    table = {int(k): v for k, v in plan["table"].items()}
    pre = read(os.path.join(a.run, "artifact-pre-rewrite.md"))
    masked, _, _ = mask(pre)
    chunks = make_chunks(masked, plan["max_chars"], prefix)
    n = len(chunks)
    if n != len(plan["chunks"]):
        raise UsageError("the plan no longer matches the pre-rewrite artifact; run `prepare` again")
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

    def keep_original(extra):
        restore_original(a.run)                      # never leave a previous rewrite in place
        report.update(extra)
        report["outcome"] = "kept_pre_rewrite_artifact"
        write(os.path.join(a.run, "rewrite-report.json"), json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    if ratio > FALLBACK_ALL_RATIO:
        keep_original({})
        print(json.dumps({"status": "fallback_all", "original_chunks": len(original_chunks), "chunks": n,
                          "action": "artifact.md restored to the pre-rewrite text"}))
        return 0
    text = unmask("".join(pieces), table, prefix)
    problems = []
    if ph_re(prefix).search(text):
        problems.append("unrestored_placeholders")
    if headings(text) != headings(pre):
        problems.append("headings_differ_after_assembly")
    if problems:
        keep_original({"problems": problems})
        print(json.dumps({"status": "fallback_all", "problems": problems, "action": "artifact.md restored to the pre-rewrite text"}))
        return 0
    if ratio > WARN_RATIO:
        report["warning"] = f"{len(original_chunks)} of {n} chunks kept their original wording"
    report["outcome"] = "rewritten"
    write(os.path.join(a.run, "artifact.md"), text, eol)
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
