import io
import json
import os
import contextlib
import tempfile
import unittest

import council_parse as cp


def env(status="- none", gaps="- none", unresolved="- none", revised="UNCHANGED", end=True, pre=""):
    s = f"{pre}<<<COUNCIL_STATUS>>>\n{status}\n<<<COUNCIL_GAPS>>>\n{gaps}\n<<<COUNCIL_UNRESOLVED>>>\n{unresolved}\n<<<COUNCIL_REVISED>>>\n{revised}\n"
    return s + ("<<<COUNCIL_END>>>\n" if end else "")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = self.tmp.name
        with contextlib.redirect_stdout(io.StringIO()):
            cp.main(["init", "--run", self.run])
        with open(os.path.join(self.run, "artifact.md"), "w", encoding="utf-8") as f:
            f.write("# Plan\nbody\n")

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self, seat, text, label=None, *extra):
        reply = os.path.join(self.run, f"reply-{seat}.txt")
        with open(reply, "wb") as f:
            f.write(text.encode("utf-8"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cp.main(["parse", "--run", self.run, "--seat", str(seat),
                            "--label", label or f"S{seat}", "--reply", reply, *extra])
        line = out.getvalue().strip().splitlines()[-1]
        return code, json.loads(line)

    def open_ids(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cp.main(["open-ids", "--run", self.run])
        return out.getvalue().strip()

    def read(self, name):
        p = os.path.join(self.run, name)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return f.read()


class Mint(Base):
    def test_mint_ids_tiers_and_open_ids(self):
        code, s = self.parse(1, env(gaps="- blocking: first\n  continued line\n- optional: second\n- third has no prefix"))
        self.assertEqual(code, 0)
        self.assertEqual(s["gaps"]["ids"], ["G1-1", "G1-2", "G1-3"])
        self.assertEqual((s["gaps"]["blocking"], s["gaps"]["optional"]), (2, 1))
        self.assertEqual(self.open_ids(), "G1-1,G1-3")
        self.assertIn("first continued line", self.read("gaps.md"))
        self.assertIn("G1-2 [optional]", self.read("open-gaps.md"))
        self.assertIn("G1-1: open (blocking)", self.read("verdicts.md"))
        self.assertEqual(self.read("artifact.md"), "# Plan\nbody\n")  # identity: untouched

    def test_none_gaps_and_placeholder_replaced(self):
        code, s = self.parse(1, env())
        self.assertEqual(code, 0)
        self.assertEqual(s["gaps"]["ids"], [])
        self.assertNotIn("\nnone\n", "\n" + self.read("gaps.md"))
        self.assertIsNone(self.read("open-gaps.md"))
        self.assertEqual(self.open_ids(), "none")

    def test_preamble_on_same_line_is_stripped(self):
        code, s = self.parse(1, env(pre="I'll read the files then answer."))
        self.assertEqual(code, 0)

    def test_crlf_input(self):
        text = env(gaps="- blocking: x").replace("\n", "\r\n")
        code, s = self.parse(1, text)
        self.assertEqual(code, 0)
        self.assertEqual(s["gaps"]["ids"], ["G1-1"])

    def test_json_wrapper_text_field(self):
        code, s = self.parse(1, json.dumps({"text": env(gaps="- blocking: x"), "stopReason": "end_turn"}))
        self.assertEqual(code, 0)
        self.assertEqual(s["gaps"]["blocking"], 1)


class Status(Base):
    def seed(self):
        self.parse(1, env(gaps="- blocking: a\n- blocking: b\n- optional: c"))

    def test_statuses_apply_and_rejected_stays_open(self):
        self.seed()
        code, s = self.parse(2, env(status="G1-1: unresolved\nG1-2: rejected - not real\n", unresolved="- G1-2: disagree"))
        self.assertEqual(code, 0)
        self.assertEqual(s["statuses"], {"unresolved": 1, "rejected": 1})
        self.assertEqual(self.open_ids(), "G1-1,G1-2")
        self.assertIn("not real", self.read("open-gaps.md"))

    def test_em_dash_reason(self):
        self.seed()
        code, s = self.parse(2, env(status="G1-1: addressed (pre-existing)\nG1-2: rejected — because\n"))
        self.assertEqual(code, 0)
        self.assertEqual(self.open_ids(), "G1-2")

    def test_dissent_restored_when_blocking_id_omitted(self):
        self.seed()
        code, s = self.parse(2, env(status="G1-1: unresolved"))
        self.assertEqual(code, 0)
        self.assertEqual(s["restored"], ["G1-2"])
        self.assertIn("Orchestrator — restored", self.read("unresolved.md"))
        self.assertEqual(self.open_ids(), "G1-1,G1-2")

    def test_optional_omitted_is_not_restored(self):
        self.seed()
        code, s = self.parse(2, env(status="G1-1: unresolved\nG1-2: unresolved"))
        self.assertEqual(s["restored"], [])

    def test_non_identity_revised_replaces_artifact_and_addressed_counts(self):
        self.seed()
        code, s = self.parse(2, env(status="G1-1: addressed\nG1-2: unresolved", revised="# Plan v2\nnew\n"))
        self.assertEqual(code, 0)
        self.assertEqual(self.read("artifact.md"), "# Plan v2\nnew\n")
        self.assertEqual(self.open_ids(), "G1-2")

    def test_reopen_closed_id(self):
        self.seed()
        self.parse(2, env(status="G1-1: addressed\nG1-2: addressed", revised="# P2\n"))
        self.assertEqual(self.open_ids(), "none")
        code, s = self.parse(3, env(status="- none\nG1-1: unresolved"))
        self.assertEqual(self.open_ids(), "G1-1")

    def test_hold_artifact_then_apply(self):
        self.seed()
        code, s = self.parse(2, env(status="G1-1: addressed\nG1-2: addressed", revised="# Held\n"), None, "--hold-artifact")
        self.assertEqual(s["revised"], "held")
        self.assertEqual(self.read("artifact.md"), "# Plan\nbody\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cp.main(["apply", "--run", self.run, "--seat", "2"])
        self.assertEqual(self.read("artifact.md"), "# Held\n")

    def test_unknown_id_ignored(self):
        self.seed()
        code, s = self.parse(2, env(status="G9-9: addressed\nG1-1: unresolved\nG1-2: unresolved", revised="# P\n"))
        self.assertIn("G9-9", s["ignored_status_ids"])


class Bad(Base):
    def test_malformed_variants_exit_3_and_change_nothing(self):
        self.parse(1, env(gaps="- blocking: a"))
        before = self.read("gaps.json")
        for text, reason in [
            ("no sentinels here at all", "no_sentinel"),
            (env(end=False), "missing_end_marker"),
            (env(revised=""), "empty_revised"),
            (env(revised="--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"), "patch_like_revised"),
            ("<<<COUNCIL_GAPS>>>\n- x\n<<<COUNCIL_END>>>\n", "missing_revised_marker"),
        ]:
            code, s = self.parse(2, text)
            self.assertEqual((code, s["reason"]), (3, reason), text[:30])
        self.assertEqual(self.read("gaps.json"), before)

    def test_illegal_identity_exit_4(self):
        self.parse(1, env(gaps="- blocking: a"))
        code, s = self.parse(2, env(status="G1-1: addressed"))
        self.assertEqual(code, 4)
        self.assertEqual(self.open_ids(), "G1-1")

    def test_illegal_identity_final_attempt_keeps_identity_drops_status(self):
        self.parse(1, env(gaps="- blocking: a"))
        code, s = self.parse(2, env(status="G1-1: addressed", gaps="- blocking: new"), None, "--final-attempt")
        self.assertEqual(code, 0)
        self.assertEqual(s["status"], "ok_illegal_identity_dropped")
        self.assertEqual(self.open_ids(), "G1-1,G2-1")
        self.assertIn("illegal-identity", self.read("failures.md"))

    def test_malformed_final_attempt_logs_failure(self):
        code, s = self.parse(1, "garbage", None, "--final-attempt")
        self.assertEqual(code, 3)
        self.assertIn("malformed", self.read("failures.md"))

    def test_artifact_containing_sentinel_text_uses_last_end(self):
        rev = "# Doc\nmentions <<<COUNCIL_END>>> inside text\nmore\n"
        code, s = self.parse(1, env(revised=rev))
        self.assertEqual(code, 0)
        self.assertIn("more", self.read("artifact.md"))


if __name__ == "__main__":
    unittest.main()
