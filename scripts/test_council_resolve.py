import contextlib
import io
import json
import os
import tempfile
import unittest

import council_resolve as cr

AGY = """Fetching available models...
gemini-4-argon-medium\tGemini 4 Argon (Medium)
gemini-4-argon-high\tGemini 4 Argon (High)
gemini-4-argon-low\tGemini 4 Argon (Low)
gemini-3.8-flash-high\tGemini 3.8 Flash (High)
gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)
gemini-3.8-flash-low\tGemini 3.8 Flash (Low)
claude-opus-5-5-low\tClaude Opus 5.5 (Low)
claude-opus-5-5-medium\tClaude Opus 5.5 (Medium)
claude-opus-5-5-high\tClaude Opus 5.5 (High)
claude-sonnet-5-5-medium\tClaude Sonnet 5.5 (Medium)
gpt-oss-120b-medium\tGPT-OSS 120B (Medium)
"""


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.state = os.path.join(self.d, "_resolved.json")

    def tearDown(self):
        self.tmp.cleanup()

    def catalog_file(self, slugs):
        p = os.path.join(self.d, "cat.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(slugs) + "\n")
        return "file:" + p

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cr.main(list(args))
            except SystemExit as e:
                code = e.code
        o = out.getvalue().strip()
        try:
            return code, json.loads(o)
        except ValueError:
            return code, o

    def resolve(self, family, line, effort, slugs, *extra):
        return self.run_cli("resolve", "--family", family, "--line", line, *(["--effort", effort] if effort else []),
                            "--source", self.catalog_file(slugs), "--state", self.state, "--today", "2026-10-23", *extra)[1]


class Parse(unittest.TestCase):
    def test_slug_shapes(self):
        cases = {
            "gemini-3.8-flash-high": ("gemini", "flash", (3, 8), "high"),
            "gemini-4-argon-medium": ("gemini", "argon", (4,), "medium"),
            "claude-opus-5-5-medium": ("claude", "opus", (5, 5), "medium"),
            "claude-sonnet-5-thinking-medium": ("claude", "sonnet", (5,), "medium"),
            "gpt-6.1-sol": ("gpt", "sol", (6, 1), None),
            "gpt-5.6-terra": ("gpt", "terra", (5, 6), None),
            "cursor-grok-4.6-high-fast": ("grok", "grok", (4, 6), "high"),
            "grok-4.7": ("grok", "grok", (4, 7), None),
            "composer-2.5-fast": ("composer", "composer", (2, 5), None),
        }
        for slug, (fam, line, ver, eff) in cases.items():
            p = cr.parse_slug(slug)
            self.assertEqual((p["family"], p["line"], p["version"], p["effort"]), (fam, line, ver, eff), slug)

    def test_unknown_family_is_none(self):
        self.assertIsNone(cr.parse_slug("llama-3-70b"))


class Resolve(Env):
    def test_picks_newest_version_of_the_same_line_numerically(self):
        r = self.resolve("gemini", "flash", "high", ["gemini-3.8-flash-high", "gemini-3.10-flash-high", "gemini-3.9-flash-high"])
        self.assertEqual(r["slug"], "gemini-3.10-flash-high")   # 3.10 > 3.9 > 3.8, not string order

    def test_never_crosses_lines(self):
        r = self.resolve("gemini", "flash", "high", AGY.split("\n")[1:])
        self.assertEqual(r["slug"], "gemini-3.8-flash-high")     # argon (4) is a different line
        r = self.resolve("gemini", "pro", "high", AGY.split("\n")[1:])
        self.assertEqual((r["status"], r["reason"]), ("skip", "no-line-match"))

    def test_effort_exact_then_nearest_at_the_newest_version(self):
        self.assertEqual(self.resolve("gemini", "argon", "high", ["gemini-4-argon-low", "gemini-4-argon-high"])["slug"], "gemini-4-argon-high")
        r = self.resolve("gemini", "argon", "xhigh", ["gemini-4-argon-low", "gemini-4-argon-medium", "gemini-4-argon-high"])
        self.assertEqual(r["slug"], "gemini-4-argon-high")
        self.assertIn("not offered", r["effort_note"])
        # a newer version with a different effort beats an older exact match
        r = self.resolve("gemini", "flash", "low", ["gemini-3.8-flash-low", "gemini-3.9-flash-medium"])
        self.assertEqual(r["slug"], "gemini-3.9-flash-medium")

    def test_effortless_models_accept_any_requested_effort(self):
        r = self.resolve("gpt", "sol", "medium", ["gpt-5.6-sol", "gpt-6.1-sol", "gpt-6-sol"])
        self.assertEqual((r["slug"], r["version"]), ("gpt-6.1-sol", "6.1"))

    def test_plain_beats_modifier_variant_at_equal_version(self):
        r = self.resolve("claude", "opus", "high", ["claude-opus-5-thinking-high", "claude-opus-5-high"])
        self.assertEqual(r["slug"], "claude-opus-5-high")

    def test_pin_is_used_verbatim_without_a_catalog(self):
        code, r = self.run_cli("resolve", "--family", "gpt", "--line", "sol", "--pin", "gpt-5.6-sol", "--source", "file:/nonexistent")
        self.assertEqual((r["slug"], r["from"]), ("gpt-5.6-sol", "pin"))

    def test_rejected_model_is_skipped_for_seven_days_then_retried(self):
        slugs = ["gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol"]
        self.run_cli("mark-rejected", "--slug", "gpt-6.1-sol", "--state", self.state, "--today", "2026-10-23")
        r = self.resolve("gpt", "sol", "medium", slugs)
        self.assertEqual((r["slug"], r["cooldown_skipped"]), ("gpt-6-sol", ["gpt-6.1-sol"]))   # next-lower
        code, later = self.run_cli("resolve", "--family", "gpt", "--line", "sol", "--source", self.catalog_file(slugs),
                                   "--state", self.state, "--today", "2026-10-30")
        self.assertEqual(later["slug"], "gpt-6.1-sol")                                          # probed again on day 7

    def test_all_cooling_down_skips(self):
        self.run_cli("mark-rejected", "--slug", "gpt-6.1-sol", "--state", self.state, "--today", "2026-10-23")
        self.assertEqual(self.resolve("gpt", "sol", None, ["gpt-6.1-sol"])["reason"], "all-cooling-down")

    def test_catalog_unreachable_falls_back_to_last_good_else_skips(self):
        src = "file:" + os.path.join(self.d, "missing.txt")
        code, r = self.run_cli("resolve", "--family", "gemini", "--line", "argon", "--effort", "medium", "--source", src, "--state", self.state)
        self.assertEqual((r["status"], r["reason"]), ("skip", "no-catalog"))
        self.run_cli("mark-good", "--key", "gemini/argon/medium", "--slug", "gemini-4-argon-medium", "--state", self.state)
        code, r = self.run_cli("resolve", "--family", "gemini", "--line", "argon", "--effort", "medium", "--source", src, "--state", self.state)
        self.assertEqual((r["slug"], r["from"]), ("gemini-4-argon-medium", "last_good"))

    def test_upgrade_is_reported_against_last_good(self):
        self.run_cli("mark-good", "--key", "gemini/flash/high", "--slug", "gemini-3.6-flash-high", "--state", self.state)
        r = self.resolve("gemini", "flash", "high", ["gemini-3.6-flash-high", "gemini-3.8-flash-high"])
        self.assertEqual((r["slug"], r["upgraded_from"]), ("gemini-3.8-flash-high", "gemini-3.6-flash-high"))

    def test_mark_good_clears_a_rejection(self):
        self.run_cli("mark-rejected", "--slug", "gpt-6.1-sol", "--state", self.state, "--today", "2026-10-23")
        self.run_cli("mark-good", "--key", "gpt/sol/medium", "--slug", "gpt-6.1-sol", "--state", self.state)
        self.assertEqual(self.resolve("gpt", "sol", "medium", ["gpt-6.1-sol"])["slug"], "gpt-6.1-sol")

    def test_corrupt_state_and_unknown_family_exit_2(self):
        with open(self.state, "w", encoding="utf-8") as f:
            f.write("{nope")
        code, _ = self.run_cli("resolve", "--family", "gpt", "--line", "sol", "--source", self.catalog_file(["gpt-6-sol"]), "--state", self.state)
        self.assertEqual(code, 2)
        code, _ = self.run_cli("resolve", "--family", "llama", "--line", "x", "--source", "agy")
        self.assertEqual(code, 2)


class Sources(Env):
    def test_codex_cache_visibility_and_utf8(self):
        home = os.path.join(self.d, "home")
        os.makedirs(os.path.join(home, ".codex"))
        data = {"models": [{"slug": "gpt-6.1-sol", "visibility": "list"}, {"slug": "gpt-reserve", "visibility": "hide"},
                           {"slug": "gpt-5.6-sol", "visibility": "list", "note": "café “quote”"}]}
        with open(os.path.join(home, ".codex", "models_cache.json"), "wb") as f:
            f.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        old = os.environ.get("USERPROFILE"), os.environ.get("HOME")
        os.environ["USERPROFILE"] = os.environ["HOME"] = home
        try:
            self.assertEqual(cr.catalog("codex"), ["gpt-6.1-sol", "gpt-5.6-sol"])
        finally:
            for k, v in zip(("USERPROFILE", "HOME"), old):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_list_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cr.main(["list", "--source", self.catalog_file(AGY.split("\n")[1:]), "--family", "gemini"])
        self.assertIn("gemini-4-argon-high\tgemini/argon\tv4\thigh", out.getvalue())
        self.assertNotIn("claude", out.getvalue())


class Classify(unittest.TestCase):
    def test_classes(self):
        cases = {
            "ERROR: The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account": "model-rejected",
            "400 unsupported model foo": "model-rejected",
            "429 Too Many Requests: RESOURCE_EXHAUSTED": "quota",
            "You have hit your usage limit": "quota",
            "You are not authenticated": "auth",
            "getaddrinfo ENOTFOUND api.example.com": "network",
            "operation timed out after 540s": "timeout",
            "something else entirely": "other",
        }
        for text, want in cases.items():
            self.assertEqual(cr.classify_text(text), want, text)

    def test_only_model_rejected_is_a_rejection(self):
        self.assertNotEqual(cr.classify_text("429 quota exceeded for model gpt-6.1-sol"), "model-rejected")


if __name__ == "__main__":
    unittest.main()
