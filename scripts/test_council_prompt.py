import contextlib
import io
import json
import os
import tempfile
import unittest

import council_parse as cp
import council_prompt as pr

SHARED = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = self.tmp.name
        with contextlib.redirect_stdout(io.StringIO()):
            cp.main(["init", "--run", self.run])
        self.put("request.md", "# Request\nReview this.\n")
        self.put("artifact.md", "# Doc\n\nBody with `code`.\n")

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, text):
        with open(os.path.join(self.run, name), "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def build(self, *extra, role="reviewer", seat=1):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = pr.main(["build", "--run", self.run, "--seat", str(seat), "--label", "Argon", "--family", "gemini",
                            "--role", role, "--total", "3", "--shared", SHARED, *extra])
        return code, json.loads(out.getvalue().strip().splitlines()[-1])

    def prompt(self, seat=1):
        with open(os.path.join(self.run, f"seat-{seat}-prompt.md"), encoding="utf-8") as f:
            return f.read()

    def test_inline_contains_everything_and_no_pointer(self):
        code, s = self.build()
        self.assertEqual((code, s["mode"], s["pointer"]), (0, "inline", None))
        text = self.prompt()
        for needle in ("seat: 1 of 3", "GAPS ONLY", "<<<COUNCIL_STATUS>>>", "=== request.md ===", "Review this.",
                       "=== artifact.md ===", "Body with `code`.", "Do not use any tools"):
            self.assertIn(needle, text)
        self.assertNotIn("BREADTH-AUDITOR ADDENDUM", text)

    def test_breadth_auditor_gets_the_addendum(self):
        code, s = self.build(role="breadth-auditor")
        self.assertIn("BREADTH-AUDITOR ADDENDUM", self.prompt())

    def test_placeholder_files_are_omitted_real_gaps_included(self):
        self.build()
        text = self.prompt()
        self.assertNotIn("=== open-gaps.md ===", text)
        self.assertNotIn("=== unresolved.md ===", text)
        reply = os.path.join(self.run, "r.txt")
        with open(reply, "w", encoding="utf-8") as f:
            f.write("<<<COUNCIL_STATUS>>>\n- none\n<<<COUNCIL_GAPS>>>\n- blocking: first\n<<<COUNCIL_UNRESOLVED>>>\n- earlier claim\n<<<COUNCIL_REVISED>>>\nUNCHANGED\n<<<COUNCIL_END>>>\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cp.main(["parse", "--run", self.run, "--seat", "1", "--label", "S1", "--reply", reply])
        code, s = self.build(seat=2)
        text = self.prompt(2)
        self.assertEqual(s["open_blocking"], "G1-1")
        self.assertIn("=== open-gaps.md ===", text)
        self.assertIn("first", text)
        self.assertIn("=== unresolved.md ===", text)
        self.assertIn("G1-1", text.split("open blocking IDs requiring STATUS:")[1].splitlines()[0])

    def test_auto_falls_back_to_file_mode_when_the_packet_is_too_big(self):
        self.put("artifact.md", "# Big\n\n" + ("word " * 6000))
        code, s = self.build("--max-inline", "20000")
        self.assertEqual(s["mode"], "file")
        self.assertIn("seat-1-prompt.md", s["pointer"])
        text = self.prompt()
        self.assertIn(os.path.join(self.run, "artifact.md"), text)
        self.assertNotIn("word word word word", text)             # the artifact body is not pasted in file mode
        self.assertGreater(s["inline_chars"], 20000)

    def test_forced_modes(self):
        self.assertEqual(self.build("--mode", "file")[1]["mode"], "file")
        self.assertEqual(self.build("--mode", "inline")[1]["mode"], "inline")

    def test_crlf_packet_is_normalized(self):
        self.put("artifact.md", "# Doc\r\n\r\nBody\r\n")
        self.build()
        self.assertNotIn("\r", self.prompt())

    def test_missing_run_dir_exits_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = pr.main(["build", "--run", os.path.join(self.run, "nope"), "--seat", "1", "--label", "x", "--family", "gemini", "--shared", SHARED])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
