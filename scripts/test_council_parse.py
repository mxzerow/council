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
        # Most mechanics tests need a role that may return a changed Revised; reviewer-role
        # tests pass "--role", "reviewer" explicitly.
        role = [] if "--role" in extra else ["--role", "integrator"]
        with contextlib.redirect_stdout(out):
            code = cp.main(["parse", "--run", self.run, "--seat", str(seat),
                            "--label", label or f"S{seat}", "--reply", reply, *role, *extra])
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

    def test_gap_text_that_quotes_sentinels_does_not_confuse_the_parser(self):
        # seen live: a reviewer of a plan about this protocol quoted <<<COUNCIL_REVISED>>> inside a gap
        quoted = ("- blocking: The parser strips `<<<COUNCIL_REVISED>>>` and then reads <<<COUNCIL_END>>> too early,"
                  " and a `<<<COUNCIL_STATUS>>>` block may be missing.\n- optional: second")
        code, s = self.parse(1, env(gaps=quoted))
        self.assertEqual((code, s["gaps"]["blocking"], s["gaps"]["optional"]), (0, 1, 1))
        self.assertIn("COUNCIL_REVISED", self.read("gaps.md"))     # the quote survives in the gap text

    def test_quoted_sentinel_before_the_real_one_on_its_own_line(self):
        text = ("<<<COUNCIL_STATUS>>>\n- none\n<<<COUNCIL_GAPS>>>\n"
                "- blocking: see the sentinel <<<COUNCIL_UNRESOLVED>>> in section 4\n"
                "<<<COUNCIL_UNRESOLVED>>>\n- none\n<<<COUNCIL_REVISED>>>\nUNCHANGED\n<<<COUNCIL_END>>>\n")
        code, s = self.parse(1, text)
        self.assertEqual((code, s["gaps"]["blocking"]), (0, 1))

    def test_still_malformed_when_a_required_marker_is_really_missing(self):
        code, s = self.parse(1, "<<<COUNCIL_STATUS>>>\n- none\n<<<COUNCIL_GAPS>>>\n- blocking: mentions <<<COUNCIL_REVISED>>> only\n<<<COUNCIL_END>>>\n")
        self.assertEqual(code, 0)    # the quoted mention is accepted as the fallback REVISED marker, END closes it
        code, s = self.parse(2, "<<<COUNCIL_STATUS>>>\n- none\n<<<COUNCIL_GAPS>>>\n- none\n")
        self.assertEqual((code, s["reason"]), (3, "missing_revised_marker"))

    def test_json_wrapper_response_field_from_agy(self):
        wrapped = json.dumps({"conversation_id": "x", "status": "SUCCESS", "response": env(gaps="- blocking: x"),
                              "usage": {"input_tokens": 15434, "output_tokens": 200}})
        code, s = self.parse(1, wrapped)
        self.assertEqual((code, s["gaps"]["blocking"]), (0, 1))

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


class ReviewFindings(Base):
    """Regression tests for the Codex review of 2026-10-23."""

    def seed(self):
        self.parse(1, env(gaps="- blocking: fix bug"))

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cp.main(list(args))
        except SystemExit as e:
            code = e.code
        return code, out.getvalue()

    def test_hold_rejection_preserves_verdicts(self):
        self.seed()
        code, s = self.parse(2, env(status="G1-1: addressed", revised="replacement"), None, "--hold-artifact")
        self.assertTrue(s["pending_until_apply"])
        self.assertEqual(self.open_ids(), "G1-1")          # nothing closed yet
        self.assertIsNotNone(self.read("open-gaps.md"))
        self.cli("reject", "--run", self.run, "--seat", "2")
        self.assertEqual(self.open_ids(), "G1-1")          # still open after rejection
        self.assertEqual(self.read("artifact.md"), "# Plan\nbody\n")
        self.assertIn("revised-rejected", self.read("failures.md"))
        self.assertIn("(revised rejected)", self.read("gaps.md"))
        self.assertFalse(os.path.exists(os.path.join(self.run, "seat-2-revised.md")))

    def test_hold_then_apply_closes_and_replaces(self):
        self.seed()
        self.parse(2, env(status="G1-1: addressed", revised="replacement"), None, "--hold-artifact")
        self.cli("apply", "--run", self.run, "--seat", "2")
        self.assertEqual(self.open_ids(), "none")
        self.assertEqual(self.read("artifact.md"), "replacement\n")

    def test_hold_restores_are_deferred_too(self):
        self.parse(1, env(gaps="- blocking: a\n- blocking: b"))
        self.parse(2, env(status="G1-1: unresolved", revised="v2"), None, "--hold-artifact")
        self.assertNotIn("restored", (self.read("unresolved.md") or ""))
        self.cli("apply", "--run", self.run, "--seat", "2")
        self.assertIn("restored", self.read("unresolved.md"))

    def test_duplicate_seat_rejected_and_state_untouched(self):
        self.parse(1, env(gaps="- blocking: original claim"))
        before = self.read("gaps.json")
        code, _ = self.cli("parse", "--run", self.run, "--seat", "1", "--label", "S1", "--reply",
                           os.path.join(self.run, "reply-1.txt"))
        self.assertEqual(code, 2)
        self.assertEqual(self.read("gaps.json"), before)

    def test_interrupted_commit_heals_on_next_command(self):
        self.seed()
        real = cp.render_all
        calls = {"n": 0}

        def boom(run, st):
            calls["n"] += 1
            if calls["n"] == 2:  # the render inside parse, after the state commit
                raise OSError("disk full")
            return real(run, st)

        cp.render_all = boom
        try:
            with self.assertRaises(OSError):
                reply = os.path.join(self.run, "r2.txt")
                with open(reply, "w", encoding="utf-8") as f:
                    f.write(env(status="G1-1: addressed", revised="replacement"))
                cp.cmd_parse(cp.build_parser().parse_args(
                    ["parse", "--run", self.run, "--seat", "2", "--label", "S2", "--reply", reply, "--role", "integrator"]))
        finally:
            cp.render_all = real
        self.assertIn("G1-1: open", self.read("verdicts.md"))     # stale derived file...
        self.assertEqual(self.open_ids(), "none")                  # ...healed by the next command
        self.assertIn("G1-1: addressed", self.read("verdicts.md"))
        self.assertIsNone(self.read("open-gaps.md"))

    def test_apply_patch_envelope_is_malformed(self):
        self.seed()
        patch = "*** Begin Patch\n*** Update File: a.txt\n@@\n-old\n+new\n*** End Patch"
        code, s = self.parse(2, env(status="G1-1: addressed", revised=patch))
        self.assertEqual((code, s["reason"]), (3, "patch_like_revised"))
        self.assertEqual(self.open_ids(), "G1-1")
        self.assertEqual(self.read("artifact.md"), "# Plan\nbody\n")

    def test_markdown_status_variants(self):
        for fmt in ["**G1-1**: addressed (pre-existing)", "`G1-1`: addressed (pre-existing)",
                    "1. G1-1: addressed (pre-existing)", "| G1-1 | addressed (pre-existing) |",
                    "g1-1: addressed (pre-existing)", "- **G1-1:** addressed (pre-existing)"]:
            with self.subTest(fmt=fmt):
                self.tearDown(); self.setUp()
                self.seed()
                code, s = self.parse(2, env(status=fmt))
                self.assertEqual(self.open_ids(), "none", fmt)
                self.assertEqual(s["restored"], [])

    def test_numbered_and_loose_gap_lists(self):
        code, s = self.parse(1, env(gaps="1. blocking: real issue\n2) optional: nit"))
        self.assertEqual((s["gaps"]["blocking"], s["gaps"]["optional"]), (1, 1))
        self.tearDown(); self.setUp()
        code, s = self.parse(1, env(gaps="The plan lacks a rollback step."))
        self.assertEqual(s["gaps"]["blocking"], 1)                  # prose is kept, not dropped

    def test_exit_2_for_usage_and_io_errors(self):
        self.assertEqual(self.cli("parse", "--run", self.run, "--seat", "x", "--label", "S", "--reply", "nope")[0], 2)
        self.assertEqual(self.cli("parse", "--run", self.run, "--seat", "1", "--label", "S", "--reply", "nope.txt")[0], 2)
        self.assertEqual(self.cli("parse", "--run", os.path.join(self.run, "missing"), "--seat", "1",
                                  "--label", "S", "--reply", "x")[0], 2)
        with open(os.path.join(self.run, "gaps.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(self.cli("open-ids", "--run", self.run)[0], 2)


class GapsOnly(Base):
    """Step 5: reviewers report gaps only; only the integrator may change the artifact."""

    def seed(self):
        self.parse(1, env(gaps="- blocking: fix bug"), None, "--role", "reviewer")

    def test_reviewer_with_changed_revised_is_malformed_first_time(self):
        self.seed()
        code, s = self.parse(2, env(status="G1-1: unresolved", revised="rewritten"), None, "--role", "reviewer")
        self.assertEqual((code, s["reason"]), (3, "revised_not_allowed"))
        self.assertIn("gaps-only", s["action"])
        self.assertEqual(self.read("artifact.md"), "# Plan\nbody\n")
        self.assertEqual(self.open_ids(), "G1-1")

    def test_breadth_auditor_is_gaps_only_too(self):
        code, s = self.parse(1, env(revised="rewritten"), None, "--role", "breadth-auditor")
        self.assertEqual((code, s["reason"]), (3, "revised_not_allowed"))

    def test_final_attempt_ignores_revised_keeps_gaps_and_logs(self):
        self.seed()
        code, s = self.parse(2, env(status="G1-1: unresolved", gaps="- blocking: new one", revised="rewritten"),
                             None, "--role", "reviewer", "--final-attempt")
        self.assertEqual(code, 0)
        self.assertEqual(s["revised"], "UNCHANGED")
        self.assertEqual(self.read("artifact.md"), "# Plan\nbody\n")
        self.assertEqual(self.open_ids(), "G1-1,G2-1")
        self.assertIn("revised-not-allowed", self.read("failures.md"))

    def test_integrator_closes_and_rejects_with_reasons(self):
        self.parse(1, env(gaps="- blocking: a\n- blocking: b\n- optional: c"), None, "--role", "reviewer")
        code, s = self.parse(2, env(status="G1-1: addressed\nG1-2: rejected - out of scope\nG1-3: addressed",
                                    unresolved="- G1-2: out of scope", revised="# Plan v2\n"), None, "--role", "integrator")
        self.assertEqual(code, 0)
        self.assertEqual(self.read("artifact.md"), "# Plan v2\n")
        self.assertEqual(self.open_ids(), "G1-2")          # rejected stays open and visible
        self.assertIn("out of scope", self.read("open-gaps.md"))
        self.assertIn("G1-2: out of scope", self.read("unresolved.md"))

    def test_reviewer_unchanged_with_gaps_is_fine(self):
        code, s = self.parse(1, env(gaps="- blocking: x"), None, "--role", "reviewer")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
