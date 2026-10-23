import contextlib
import io
import json
import os
import tempfile
import unittest

import council_rewrite as rw

DOC = """# Title

Intro with `inline code`, a link https://example.com/a?b=1 and a path D:\\Documents\\x\\y.py and scripts/foo.py:12.
Gap G1-2 was fixed in commit 4ca9526e and run 2026-10-23T08-34-48. See "a quoted phrase" too.

## First section

The retry limit is 3 and the timeout is 45 minutes. The seat must never write files.

| Col | Val |
|-----|-----|
| a | 1 |

```python
def f():
    return "x" # `not masked twice`
```

## Second section

Do not skip the check unless the user asks. Only the integrator may change the artifact.

### Detail

More detail here, 2 items.
"""


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = self.tmp.name
        self.write("artifact.md", DOC)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        with open(os.path.join(self.run, name), "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def read(self, name):
        with open(os.path.join(self.run, name), encoding="utf-8") as f:
            return f.read()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = rw.main(list(args))
            except SystemExit as e:
                code = e.code
        o = out.getvalue().strip()
        try:
            return code, json.loads(o)
        except ValueError:
            return code, o

    def prepare(self, max_chars=24000):
        return self.cli("prepare", "--run", self.run, "--max-chars", str(max_chars))

    def chunk_in(self, i):
        return self.read(f"rewrite/chunk-{i}-in.txt")

    def reply(self, i, text):
        p = os.path.join(self.run, f"reply-{i}-{os.urandom(2).hex()}.txt")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return self.cli("check", "--run", self.run, "--chunk", str(i), "--reply", p)

    def identity_reply(self, i):
        return self.chunk_in(i) + f"\n<<<REWRITE_END {i}>>>\n"


class Masking(unittest.TestCase):
    def test_round_trip_is_exact(self):
        masked, table, prefix = rw.mask(DOC)
        self.assertEqual(rw.unmask(masked, table), DOC)
        self.assertGreater(len(table), 8)

    def test_protected_spans_are_hidden(self):
        masked, _, _ = rw.mask(DOC)
        for secret in ("https://example.com", "D:\\Documents", "scripts/foo.py", "G1-2", "4ca9526e", "def f()", "| a | 1 |", "inline code", '"a quoted phrase"'):
            self.assertNotIn(secret, masked, secret)
        self.assertIn("The retry limit is 3", masked)              # prose and numbers stay visible
        self.assertIn("## First section", masked)                   # headings stay visible

    def test_trailing_sentence_punctuation_stays_outside_the_span(self):
        masked, table, prefix = rw.mask("Compare counters/errors. See https://example.com/x, then scripts/a.py; done (see core.md).")
        self.assertEqual(sorted(table.values()), sorted(["counters/errors", "https://example.com/x", "scripts/a.py", "core.md"]))
        self.assertRegex(masked, r"@@P\d+@@\. See @@P\d+@@, then @@P\d+@@; done \(see @@P\d+@@\)\.")
        self.assertEqual(rw.unmask(masked, table), "Compare counters/errors. See https://example.com/x, then scripts/a.py; done (see core.md).")

    def test_nested_placeholders_restore(self):
        text = 'He said "run `make all` now" twice.'
        masked, table, prefix = rw.mask(text)
        self.assertEqual(rw.unmask(masked, table), text)

    def test_real_files_round_trip_and_chunk_losslessly(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("core.md", "SKILL.md", "claude/SKILL.md", "partitioning.md"):
            p = os.path.join(root, name)
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                text = f.read().replace("\r\n", "\n")
            masked, table, prefix = rw.mask(text)
            self.assertEqual(rw.unmask(masked, table, prefix), text, name)
            self.assertEqual("".join(rw.make_chunks(masked, 6000, prefix)), masked, name)


class Chunking(unittest.TestCase):
    def test_h2_split_and_preamble(self):
        masked, _, _ = rw.mask(DOC)
        chunks = rw.make_chunks(masked, 120)
        self.assertGreater(len(chunks), 2)
        self.assertEqual("".join(chunks), masked)

    def test_oversize_section_splits_at_h3_then_paragraphs(self):
        doc = "# T\n\n## Big\n\n" + "\n\n".join(f"Paragraph number {i} " + "word " * 40 for i in range(30)) + "\n\n### Sub\n\n" + "tail " * 50
        chunks = rw.make_chunks(doc, 1500)
        self.assertEqual("".join(chunks), doc)
        self.assertTrue(all(len(c) <= 1500 for c in chunks), [len(c) for c in chunks])

    def test_no_h2_document(self):
        doc = "Just text.\n\nMore text.\n"
        self.assertEqual(rw.make_chunks(doc, 1000), [doc])

    def test_single_enormous_paragraph_is_hard_split_losslessly(self):
        doc = ("x" * 5000) + "\n" + ("y" * 5000) + "\n"
        chunks = rw.make_chunks(doc, 1000)
        self.assertEqual("".join(chunks), doc)


class Checks(Env):
    def setUp(self):
        super().setUp()
        self.prepare(max_chars=100000)
        self.src = self.chunk_in(1)

    def c(self, body, i=1):
        return rw.check_chunk(self.src, body + f"\n<<<REWRITE_END {i}>>>", i)

    def test_identity_passes(self):
        self.assertEqual(self.c(self.src), [])

    def test_empty_output_is_labelled_empty_not_truncated(self):
        self.assertTrue(rw.check_chunk(self.src, "", 1)[0].startswith("empty_output"))
        self.assertTrue(rw.check_chunk(self.src, "  \n\n", 1)[0].startswith("empty_output"))

    def test_truncated_or_wrong_marker(self):
        self.assertEqual(rw.check_chunk(self.src, self.src, 1), ["truncated_or_no_end_marker"])
        self.assertEqual(rw.check_chunk(self.src, self.src + "<<<REWRITE_END 2>>>", 1), ["truncated_or_no_end_marker"])

    def test_missing_extra_and_duplicate_placeholders(self):
        import re
        first = rw.ph_re("@@P").search(self.src).group(0)
        self.assertTrue(any(r.startswith("placeholders") for r in self.c(self.src.replace(first, "", 1))))
        self.assertTrue(any(r.startswith("placeholders") for r in self.c(self.src + " " + first)))
        self.assertTrue(any(r.startswith("placeholders") for r in self.c(self.src + " @@P9999@@")))

    def test_placeholder_order_may_change(self):
        a, b = list(rw.ph_re("@@P").finditer(self.src))[:2]
        swapped = self.src.replace(a.group(0), "@@TMP@@").replace(b.group(0), a.group(0)).replace("@@TMP@@", b.group(0))
        self.assertEqual(self.c(swapped), [])

    def test_numbers_lost_or_added(self):
        self.assertTrue(any(r.startswith("numbers") for r in self.c(self.src.replace("45 minutes", "a while"))))
        self.assertTrue(any(r.startswith("numbers") for r in self.c(self.src.replace("45 minutes", "45 minutes and 99 seconds"))))

    def test_digits_inside_an_addition_fail_and_clean_additions_pass(self):
        ok = self.src.replace("The retry limit", "{{+ A retry is a second attempt. +}} The retry limit")
        self.assertEqual(self.c(ok), [])
        bad = self.src.replace("The retry limit", "{{+ A retry is attempt 2. +}} The retry limit")
        self.assertTrue(any(r.startswith("digit_in_addition") for r in self.c(bad)))
        self.assertTrue(any("unbalanced" in r for r in self.c(self.src + " {{+ oops")))

    def test_overlong_addition_fails(self):
        long_add = "{{+ " + "a very long winded explanation " * 8 + "+}}"
        out = self.src.replace("The retry limit", long_add + " The retry limit")
        self.assertTrue(any(r.startswith("addition_too_long") for r in self.c(out)))
        short = self.src.replace("The retry limit", "{{+ (a retry is a second attempt) +}} The retry limit")
        self.assertEqual(self.c(short), [])

    def test_near_miss_markers_are_rejected_not_passed_through(self):
        for bad in ("{+ A retry is a second attempt. +}", "{{+ A retry is a second attempt. +}", "{+ x +}} "):
            out = self.src.replace("The retry limit", bad + " The retry limit")
            self.assertTrue(any(r.startswith(("stray_markers", "unbalanced")) for r in self.c(out)), bad)

    def test_prompt_file_keeps_the_literal_double_brace_markers(self):
        self.assertIn("{{+ and +}}", self.read("rewrite/chunk-1-prompt.md"))
        self.assertIn("<<<REWRITE_END 1>>>", self.read("rewrite/chunk-1-prompt.md"))
        self.assertNotIn("{n}", self.read("rewrite/chunk-1-prompt.md"))

    def test_heading_changes_fail_including_additions_in_headings(self):
        self.assertIn("headings_changed", self.c(self.src.replace("## First section", "## The first part")))
        self.assertIn("headings_changed", self.c(self.src.replace("## First section", "## First section {{+ hello +}}")))
        self.assertIn("headings_changed", self.c(self.src.replace("## First section", "### First section")))

    def test_negation_loss_fails(self):
        out = self.src.replace("must never write", "writes").replace("Do not skip", "Skip").replace("unless", "if")
        self.assertTrue(any(r.startswith(("negations_lost", "qualifiers_lost")) for r in self.c(out)))

    def test_length_bounds(self):
        self.assertTrue(any(r.startswith("length_ratio") for r in self.c("## First section\n\nshort")))
        self.assertTrue(any(r.startswith("length_ratio") for r in self.c(self.src + "\n" + self.src + "\n" + self.src)))


class Flow(Env):
    def test_identity_rewrite_reproduces_the_original_exactly(self):
        code, s = self.prepare(max_chars=300)
        self.assertGreater(s["chunks"], 1)
        for i in range(1, s["chunks"] + 1):
            self.assertEqual(self.reply(i, self.identity_reply(i))[1]["status"], "ok")
        code, s = self.cli("assemble", "--run", self.run)
        self.assertEqual(s["status"], "rewritten")
        self.assertEqual(self.read("artifact.md"), DOC)
        self.assertEqual(self.read("artifact-pre-rewrite.md"), DOC)

    def test_retry_then_fallback_keeps_the_original_chunk(self):
        self.prepare(max_chars=300)
        code, s = self.reply(1, "garbage with no end marker")
        self.assertEqual((code, s["status"]), (3, "retry"))
        code, s = self.reply(1, "still garbage")
        self.assertEqual((code, s["status"]), (4, "fallback"))
        n = self.cli("status", "--run", self.run)[1]["chunks"]
        for i in range(2, n + 1):
            self.reply(i, self.identity_reply(i))
        code, s = self.cli("assemble", "--run", self.run)
        self.assertEqual(s["status"], "rewritten")
        self.assertEqual(s["original_chunks"], [1])
        self.assertEqual(self.read("artifact.md"), DOC)           # original chunk kept verbatim

    def test_more_than_half_original_keeps_the_pre_rewrite_artifact(self):
        self.prepare(max_chars=150)
        n = self.cli("status", "--run", self.run)[1]["chunks"]
        self.assertGreaterEqual(n, 3)          # 1 accepted, the rest original: more than half
        self.reply(1, self.identity_reply(1))
        # leave every other chunk pending -> counted as original
        code, s = self.cli("assemble", "--run", self.run)
        self.assertEqual(s["status"], "fallback_all")
        self.assertEqual(self.read("artifact.md"), DOC)
        self.assertEqual(json.loads(self.read("rewrite-report.json"))["outcome"], "kept_pre_rewrite_artifact")

    def test_additions_are_reported_and_markers_stripped(self):
        self.prepare(max_chars=100000)
        body = self.chunk_in(1).replace("The retry limit", "{{+ A retry is a second attempt. +}} The retry limit")
        self.assertEqual(self.reply(1, body + "\n<<<REWRITE_END 1>>>\n")[1]["status"], "ok")
        code, s = self.cli("assemble", "--run", self.run)
        self.assertEqual((s["status"], s["additions"]), ("rewritten", 1))
        text = self.read("artifact.md")
        self.assertIn("A retry is a second attempt. The retry limit", text)
        self.assertNotIn("{{+", text)
        rep = json.loads(self.read("rewrite-report.json"))
        self.assertEqual(rep["additions"][0]["text"], "A retry is a second attempt.")

    def test_protected_text_survives_a_real_rewrite(self):
        self.prepare(max_chars=100000)
        body = self.chunk_in(1).replace("The retry limit is 3 and the timeout is 45 minutes.", "The timeout is 45 minutes, and the retry limit is 3.")
        self.assertEqual(self.reply(1, body + "\n<<<REWRITE_END 1>>>\n")[1]["status"], "ok")
        self.cli("assemble", "--run", self.run)
        text = self.read("artifact.md")
        self.assertIn("The timeout is 45 minutes, and the retry limit is 3.", text)
        for keep in ("https://example.com/a?b=1", "D:\\Documents\\x\\y.py", "G1-2", "def f():", "| a | 1 |", '"a quoted phrase"'):
            self.assertIn(keep, text)

    def test_crlf_artifact_keeps_its_line_endings(self):
        self.write("artifact.md", DOC.replace("\n", "\r\n"))
        self.prepare(max_chars=100000)
        self.assertEqual(self.reply(1, self.identity_reply(1))[1]["status"], "ok")
        self.cli("assemble", "--run", self.run)
        with open(os.path.join(self.run, "artifact.md"), "rb") as f:
            self.assertEqual(f.read(), DOC.replace("\n", "\r\n").encode("utf-8"))   # original line endings kept

    def test_errors_exit_2(self):
        self.assertEqual(self.cli("assemble", "--run", self.run)[0], 2)                      # no plan
        self.assertEqual(self.cli("prepare", "--run", os.path.join(self.run, "nope"))[0], 2)  # no artifact
        self.prepare()
        self.assertEqual(self.cli("check", "--run", self.run, "--chunk", "99", "--reply", os.path.join(self.run, "artifact.md"))[0], 2)
        self.assertEqual(self.cli("check", "--run", self.run, "--chunk", "1", "--reply", os.path.join(self.run, "missing.txt"))[0], 2)
        self.assertEqual(self.reply(1, self.identity_reply(1))[1]["status"], "ok")
        self.assertEqual(self.cli("check", "--run", self.run, "--chunk", "1", "--reply", os.path.join(self.run, "artifact.md"))[0], 2)   # already final


if __name__ == "__main__":
    unittest.main()
