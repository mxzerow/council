"""Regression tests for the Codex review of council_rewrite.py (2026-10-23)."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest

import council_rewrite as rw


def identity_roundtrip(text, max_chars=24000):
    masked, table, prefix = rw.mask(text)
    chunks = rw.make_chunks(masked, max_chars, prefix)
    outs = []
    for i, c in enumerate(chunks, 1):
        reasons = rw.check_chunk(c, c + f"\n<<<REWRITE_END {i}>>>", i, prefix)
        assert reasons == [], reasons
        outs.append(c)
    return rw.unmask("".join(outs), table, prefix)


class Collisions(unittest.TestCase):
    def test_literal_placeholder_text_in_the_document_survives(self):
        text = "Literal @@P1@@ and `secret` and @@PP2@@.\n"
        self.assertEqual(identity_roundtrip(text), text)

    def test_prefix_is_never_present_in_the_source(self):
        for text in ("plain", "has @@P1@@", "has @@P1@@ and @@PP2@@ and @@PPP3@@"):
            masked, table, prefix = rw.mask(text)
            self.assertNotIn(prefix, text)

    def test_chunk_boundary_never_splits_a_placeholder(self):
        text = "a" * 23998 + "`secret`" + "z" * 50
        masked, table, prefix = rw.mask(text)
        chunks = rw.make_chunks(masked, 24000, prefix)
        self.assertEqual("".join(chunks), masked)
        ph = rw.ph_re(prefix)
        for c in chunks:
            self.assertEqual(c.count(prefix), len(ph.findall(c)), "a chunk holds a broken placeholder")
        self.assertEqual(identity_roundtrip(text), text)

    def test_hard_split_inside_long_runs_keeps_every_placeholder_whole(self):
        text = ("word `code` " * 400) + "\n"
        masked, table, prefix = rw.mask(text)
        chunks = rw.make_chunks(masked, 500, prefix)
        ph = rw.ph_re(prefix)
        for c in chunks:
            self.assertEqual(c.count(prefix), len(ph.findall(c)))
        self.assertEqual(rw.unmask("".join(chunks), table), text)


class CodeProtection(unittest.TestCase):
    def assert_hidden(self, text, secret):
        masked, table, prefix = rw.mask(text)
        self.assertNotIn(secret, masked, text)
        self.assertEqual(rw.unmask(masked, table), text)

    def test_indented_and_longer_fences(self):
        self.assert_hidden("Intro\n\n  ```py\n  print(1)\n  ```\n\nafter\n", "print(1)")
        self.assert_hidden("Intro\n\n````\nprint(1)\n```\nstill code\n````\n\nafter\n", "still code")
        self.assert_hidden("Intro\n\n~~~~\nprint(1)\n~~~~~\n\nafter\n", "print(1)")

    def test_indented_code_block(self):
        self.assert_hidden("Intro\n\n    print(1)\n    print(2)\n\nafter\n", "print(2)")
        self.assert_hidden("Intro\n\n\tprint(1)\n\nafter\n", "print(1)")

    def test_multi_backtick_spans(self):
        self.assert_hidden("Use ``a ` b`` safely.", "a ` b")
        self.assert_hidden("Use ```x `` y``` safely.", "x `` y")

    def test_unclosed_fence_runs_to_the_end_and_is_hidden(self):
        self.assert_hidden("Intro\n\n```py\nprint(1)\nno closing fence\n", "no closing fence")

    def test_changing_protected_code_is_caught(self):
        text = "Intro\n\n  ```py\n  print(1)\n  ```\n"
        masked, table, prefix = rw.mask(text)
        # the model never sees the code, so it cannot change it; a changed placeholder is rejected
        self.assertTrue(rw.check_chunk(masked, masked.replace(prefix + "1@@", "erase(1)") + "\n<<<REWRITE_END 1>>>", 1, prefix))


class Numbers(unittest.TestCase):
    def check(self, a, b):
        return rw.check_chunk(a, b + "\n<<<REWRITE_END 1>>>", 1)

    def test_version_changes_are_caught(self):
        self.assertTrue(any(r.startswith("numbers") for r in self.check("Use version 3.8.1 for this release.", "Use version 3.8.2 for this release.")))
        self.assertEqual(self.check("Use version 3.8.1 for this release.", "Please use version 3.8.1 for this release."), [])

    def test_sign_changes_are_caught_but_ranges_are_not_signed(self):
        self.assertTrue(any(r.startswith("numbers") for r in self.check("The error was -5 after the change.", "The error was 5 after the change.")))
        self.assertEqual(self.check("Allow 3-5 retries here.", "Allow 3-5 retries here."), [])

    def test_percent_and_thousands(self):
        self.assertTrue(any(r.startswith("numbers") for r in self.check("Up 12% to 1,000 items now.", "Up 13% to 1,000 items now.")))


class Negations(unittest.TestCase):
    def test_losing_one_of_three_nots_is_rejected(self):
        src = "It must not fail. It must not crash. It must not hang."
        out = "It must not fail. It must crash. It must not hang."
        self.assertTrue(any(r.startswith("negations_lost") for r in rw.check_chunk(src, out + "\n<<<REWRITE_END 1>>>", 1)))

    def test_reworded_negation_forms_are_accepted(self):
        src = "It must not fail and must never crash."
        out = "It cannot fail, and it must never crash."
        self.assertEqual([r for r in rw.check_chunk(src, out + "\n<<<REWRITE_END 1>>>", 1) if r.startswith("negations")], [])


class Headings(unittest.TestCase):
    def test_setext_headings_are_protected(self):
        src = "Release\n=======\n\nBody text here.\n\nSub\n---\n\nMore.\n"
        self.assertEqual(rw.headings(src), [(1, "Release"), (2, "Sub")])
        out = src.replace("Release", "Summary")
        self.assertIn("headings_changed", rw.check_chunk(src, out + "\n<<<REWRITE_END 1>>>", 1))


class Performance(unittest.TestCase):
    def test_unclosed_fences_scale_linearly(self):
        text = ("~~~lang\n" + "a" * 100 + "\n") * 9000          # ~ 1 MB, never closed
        t0 = time.time()
        masked, table, prefix = rw.mask(text)
        rw.make_chunks(masked, 24000, prefix)
        self.assertLess(time.time() - t0, 3.0)

    def test_large_plain_and_backtick_heavy_text(self):
        text = ("Some prose with `code` and \"a quote\" and a/b.py here. " * 20000)
        t0 = time.time()
        masked, table, prefix = rw.mask(text)
        self.assertEqual(rw.unmask(masked, table), text)
        self.assertLess(time.time() - t0, 5.0)


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, data):
        with open(os.path.join(self.run, name), "wb") as f:
            f.write(data)

    def get(self, name):
        with open(os.path.join(self.run, name), "rb") as f:
            return f.read()

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = rw.main(list(args))
            except SystemExit as e:
                code = e.code
        try:
            return code, json.loads(out.getvalue().strip())
        except ValueError:
            return code, out.getvalue().strip()

    def accept_identity(self, n):
        for i in range(1, n + 1):
            with open(os.path.join(self.run, "rewrite", f"chunk-{i}-in.txt"), encoding="utf-8") as f:
                src = f.read()
            self.put("reply.txt", (src + f"\n<<<REWRITE_END {i}>>>\n").encode("utf-8"))
            self.cli("check", "--run", self.run, "--chunk", str(i), "--reply", os.path.join(self.run, "reply.txt"))

    def test_crlf_identity_is_byte_identical(self):
        original = b"# T\r\n\r\nHello world.\r\n\r\nKeep this text.\r\n"
        self.put("artifact.md", original)
        code, s = self.cli("prepare", "--run", self.run)
        self.accept_identity(s["chunks"])
        self.cli("assemble", "--run", self.run)
        self.assertEqual(self.get("artifact.md"), original)
        self.assertEqual(self.get("artifact-pre-rewrite.md"), original)

    def test_repeated_prepare_and_fallback_restores_the_saved_original(self):
        self.put("artifact.md", b"# T\n\nThe initial complete report.\n")
        code, s = self.cli("prepare", "--run", self.run)
        with open(os.path.join(self.run, "rewrite", "chunk-1-in.txt"), encoding="utf-8") as f:
            src = f.read()
        self.put("reply.txt", (src.replace("initial", "original") + "\n<<<REWRITE_END 1>>>\n").encode("utf-8"))
        self.assertEqual(self.cli("check", "--run", self.run, "--chunk", "1", "--reply", os.path.join(self.run, "reply.txt"))[1]["status"], "ok")
        self.cli("assemble", "--run", self.run)
        self.assertIn(b"original complete", self.get("artifact.md"))
        self.cli("prepare", "--run", self.run)                       # again: nothing accepted yet
        code, s = self.cli("assemble", "--run", self.run)
        self.assertEqual(s["status"], "fallback_all")
        self.assertEqual(self.get("artifact.md"), b"# T\n\nThe initial complete report.\n")


if __name__ == "__main__":
    unittest.main()
