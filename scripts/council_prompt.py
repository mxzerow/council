#!/usr/bin/env python3
"""Council seat prompt builder.

Builds `seat-<n>-prompt.md` for a reviewer seat from the run's packet files, in one of two modes:

  inline  Everything the seat needs is IN the prompt (contract, addendum, request, artifact,
          open gaps, real dissent). The seat makes one model call and uses no tools.
  file    A short pointer prompt that lists absolute paths; the seat reads the files itself.

Why inline is the default: every `agy` model call carries ~15k tokens of fixed agent overhead, and
a file read is a second call (measured 2026-10-23: 20.8k vs 15.5k input tokens on a small packet,
21.5k vs 17.4k on an 8.5 KB one). The command line has a ~32k character limit, so a packet that
does not fit falls back to file mode automatically.

Standard library only. Run with `python -I scripts/council_prompt.py build ...`.

  build --run DIR --seat N --label TEXT --family F [--role ROLE] [--total N]
        [--shared DIR] [--mode auto|inline|file] [--max-inline CHARS]

Prints one JSON line: {"mode", "prompt_file", "chars", "pointer"}; `pointer` is the short -p text to
use in file mode, or null in inline mode (then -p is the prompt file's contents).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import council_parse as cp  # noqa: E402

DEFAULT_MAX_INLINE = 24000
POINTER = ("Read {f} and the absolute paths it lists. Return only the Council sentinel envelope. "
           "Do not write workspace files.")


def read(path):
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def has_content(text):
    return text.strip().lower() not in cp.PLACEHOLDERS


def header(a, open_ids):
    lines = [
        f"seat: {a.seat}" + (f" of {a.total}" if a.total else ""),
        f"label: {a.label}",
        "mode: review-only",
        f"family: {a.family}",
        f"role: {a.role or 'reviewer'}",
        f"open blocking IDs requiring STATUS: {open_ids or 'none'}",
        "GAPS ONLY: Revised must be exactly UNCHANGED. Put every change you want under Gaps.",
    ]
    return "\n".join(lines) + "\n"


def build(a):
    run, shared = a.run, a.shared
    if not os.path.isdir(run):
        raise cp.UsageError(f"run dir not found: {run}")
    st = cp.load_state(run)
    open_ids = ",".join(sorted(cp.open_blocking(st), key=cp.id_key))
    envelope = read(os.path.join(shared, "reviewer-envelope.md"))
    if not envelope:
        raise cp.UsageError(f"reviewer-envelope.md not found under {shared}")
    breadth = (a.role or "") == "breadth-auditor"
    addendum = read(os.path.join(shared, "reviewer-prompt.md")) if breadth else ""
    # packet: dissent / open gaps only when they carry real content (never the init `none` placeholders)
    packet = [("request.md", read(os.path.join(run, "request.md"))), ("artifact.md", read(os.path.join(run, "artifact.md")))]
    og = read(os.path.join(run, "open-gaps.md"))
    if has_content(og):
        packet.append(("open-gaps.md", og))
    un = read(os.path.join(run, "unresolved.md"))
    if has_content(un) and "- " in un:
        packet.append(("unresolved.md", un))
    head = header(a, open_ids)

    inline = head + "\nEverything you need is below. Do not use any tools and do not read any files.\n\n=== CONTRACT ===\n" + envelope
    if addendum:
        inline += "\n\n=== BREADTH-AUDITOR ADDENDUM ===\n" + addendum
    for name, body in packet:
        inline += f"\n\n=== {name} ===\n{body.rstrip()}"
    inline += "\n\n=== END OF PACKET ===\nReturn only the Council sentinel envelope.\n"

    mode = a.mode
    if mode == "auto":
        mode = "inline" if len(inline) <= a.max_inline else "file"
    out_path = os.path.join(run, f"seat-{a.seat}-prompt.md")
    if mode == "inline":
        text, pointer = inline, None
    else:
        paths = [os.path.join(run, n) for n, _ in packet]
        if addendum:
            paths.append(os.path.join(shared, "reviewer-prompt.md") + "  (breadth-auditor addendum)")
        text = (head + "\nRead exactly these absolute paths and nothing else:\n"
                + "\n".join(f"- {p}" for p in paths) + "\n\nContract (inlined):\n\n" + envelope)
        pointer = POINTER.format(f=out_path)
    cp.write(out_path, text)
    print(json.dumps({"mode": mode, "prompt_file": out_path, "chars": len(text),
                      "inline_chars": len(inline), "pointer": pointer, "open_blocking": open_ids or "none"}))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="council_prompt")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("build")
    s.add_argument("--run", required=True); s.add_argument("--seat", required=True, type=int)
    s.add_argument("--label", required=True); s.add_argument("--family", required=True)
    s.add_argument("--role", default=None); s.add_argument("--total", type=int, default=None)
    s.add_argument("--shared", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    s.add_argument("--mode", choices=["auto", "inline", "file"], default="auto")
    s.add_argument("--max-inline", type=int, default=DEFAULT_MAX_INLINE)
    a = p.parse_args(argv)
    try:
        return build(a)
    except (cp.UsageError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
