import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import council_gemini_api as cga

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


class CursorListing(Env):
    """`cursor-agent --list-models` prints `slug - Label` lines under a header (seen 2026-10-23)."""
    LISTING = ("Loading models…\r\nAvailable models\r\n\r\n"
               "composer-2.5 - Composer 2.5\r\n"
               "claude-opus-5-thinking-high - Claude Opus 5 1M Thinking\r\n"
               "claude-opus-5-5-medium - Claude Opus 5.5 1M\r\n"
               "claude-opus-5-5-medium-fast - Claude Opus 5.5 1M Fast\r\n"
               "claude-opus-5-5-high - Claude Opus 5.5 1M High\r\n"
               "gpt-5.6-sol-high - GPT-5.6 Sol 1M High\r\n"
               "gpt-5.6-sol-none - GPT-5.6 Sol 1M None\r\n"
               "gpt-5.6-sol-none-fast - GPT-5.6 Sol 1M None Fast\r\n"
               "gpt-5.6-sol-medium - GPT-5.6 Sol 1M\r\n"
               "gpt-5.6-sol-medium-fast - GPT-5.6 Sol 1M Fast\r\n"
               "gemini-3.6-flash-minimal - Gemini 3.6 Flash Minimal\r\n"
               "gemini-4-argon-medium - Gemini 4 Argon Medium\r\n"
               "gemini-3.8-flash-medium - Gemini 3.8 Flash Medium\r\n"
               "gemini-3.7-flash-high - Gemini 3.7 Flash\r\n"
               "cursor-grok-4.6-high-fast - Grok 4.6 Fast\r\n\r\nTip: use /model to switch\r\n")

    def src(self):
        p = os.path.join(self.d, "task-models.txt")
        with open(p, "wb") as f:
            f.write(self.LISTING.encode("utf-8"))
        return "file:" + p

    def test_listing_format_is_parsed(self):
        slugs = cr.catalog(self.src())
        self.assertIn("gemini-4-argon-medium", slugs)
        self.assertNotIn("Available", " ".join(slugs))
        self.assertTrue(all(" " not in s for s in slugs))

    def test_default_lineup_resolves_from_the_cursor_listing(self):
        for fam, line, eff, want in [("grok", "grok", "high", "cursor-grok-4.6-high-fast"),
                                     ("claude", "opus", "medium", "claude-opus-5-5-medium"),   # 5.5 beats 5; plain beats -fast
                                     ("gpt", "sol", "medium", "gpt-5.6-sol-medium")]:
            code, r = self.run_cli("resolve", "--family", fam, "--line", line, "--effort", eff, "--source", self.src(), "--state", self.state)
            self.assertEqual(r["slug"], want, (fam, line))


class CursorSource(Env):
    def test_gemini_cursor_skip_even_with_pin_or_last_good(self):
        self.run_cli('mark-good', '--key', 'gemini/flash/', '--slug', 'gemini-3.8-flash', '--state', self.state)
        for flags in ([], ['--pin', 'gemini-4-flash']):
            with patch.object(cr, 'catalog', side_effect=AssertionError('must not query')):
                code, result = self.run_cli('resolve', '--family', 'gemini', '--line', 'flash',
                                            '--source', 'cursor', '--state', self.state, *flags)
            self.assertEqual((code, result['status'], result['reason']), (0, 'skip', 'cursor-invalid-for-gemini'))

    def test_retired_source_is_usage_error(self):
        for flags in ([], ['--pin', 'grok-4.6']):
            self.assertEqual(self.run_cli('resolve', '--family', 'grok', '--line', 'grok',
                                          '--source', 'grok', *flags)[0], 2)

    def test_cursor_source_parses_the_cli_listing(self):
        listing = ("Available models\r\n\r\ngemini-4-argon-medium - Gemini 4 Argon Medium\r\n"
                   "gemini-3.8-flash-low - Gemini 3.8 Flash Low\r\ngemini-3.8-flash-medium - Gemini 3.8 Flash Medium\r\n"
                   "claude-sonnet-5-5-high - Claude Sonnet 5.5 1M High\r\n\r\nTip: use --model\r\n")
        old = cr.run_cli
        cr.run_cli = lambda names, args: listing if (names, args) == (["cursor-agent"], ["--list-models"]) else ""
        try:
            self.assertEqual(cr.catalog("cursor"), ["gemini-4-argon-medium", "gemini-3.8-flash-low", "gemini-3.8-flash-medium", "claude-sonnet-5-5-high"])
            code, r = self.run_cli("resolve", "--family", "gemini", "--line", "flash", "--effort", "low", "--source", "cursor", "--state", self.state)
            self.assertEqual((r["status"], r["reason"]), ("skip", "cursor-invalid-for-gemini"))
        finally:
            cr.run_cli = old


class EffortWords(Env):
    def test_none_and_minimal_are_efforts_not_lines(self):
        self.assertEqual(cr.parse_slug("gpt-5.6-sol-none")["effort"], "none")
        self.assertEqual(cr.parse_slug("gemini-3.6-flash-minimal")["effort"], "minimal")
        self.assertEqual(cr.parse_slug("gpt-5.6-sol-none")["line"], "sol")

    def test_exact_effort_beats_an_effortless_or_none_slug(self):
        r = self.resolve("gpt", "sol", "medium", ["gpt-5.6-sol-none", "gpt-5.6-sol", "gpt-5.6-sol-medium"])
        self.assertEqual(r["slug"], "gpt-5.6-sol-medium")
        # but an effortless slug still beats a far-away explicit effort
        r = self.resolve("gpt", "sol", "medium", ["gpt-5.6-sol-max", "gpt-5.6-sol"])
        self.assertEqual(r["slug"], "gpt-5.6-sol")


class APISource(Env):
    SLUGS = ['gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-3.5-flash', 'gemini-4-argon',
             'gemini-9-flash-lite', 'gemini-9-flash-image', 'gemini-9-flash-tts', 'gemini-9-flash-customtools']

    def api_resolve(self, *extra):
        return self.run_cli('resolve', '--family', 'gemini', '--line', 'flash', '--source', 'api',
                            '--state', self.state, *extra)

    def test_api_filters_variants_and_preserves_line(self):
        with patch.object(cga, 'list_models', return_value=self.SLUGS):
            self.assertEqual(self.api_resolve()[1]['slug'], 'gemini-3.8-flash')
        self.assertEqual(cr.parse_slug('gemini-3.8-flash-lite')['extras'], ['lite'])

    def test_exclusions_are_ephemeral_and_reported(self):
        with patch.object(cga, 'list_models', return_value=self.SLUGS):
            code, result = self.api_resolve('--exclude', 'gemini-3.8-flash')
            self.assertEqual((code, result['slug']), (0, 'gemini-3.6-flash'))
            self.assertEqual(result['cooldown_skipped'], ['gemini-3.8-flash'])
            args = [v for slug in self.SLUGS[:3] for v in ('--exclude', slug)]
            self.assertEqual(self.api_resolve(*args)[1]['reason'], 'all-cooling-down')
            self.assertEqual(self.api_resolve()[1]['slug'], 'gemini-3.8-flash')
        self.assertFalse(os.path.exists(self.state))

    def test_explicit_variant_line(self):
        with patch.object(cga, 'list_models', return_value=self.SLUGS):
            code, result = self.run_cli('resolve', '--family', 'gemini', '--line', 'lite', '--source', 'api')
        self.assertEqual((code, result.get('slug')), (0, 'gemini-9-flash-lite'))

    def test_per_entry_days_and_old_default(self):
        code, result = self.run_cli('mark-rejected', '--slug', 'gemini-3.8-flash', '--state', self.state,
                                   '--days', '1', '--reason', 'unavailable', '--today', '2026-10-24')
        self.assertEqual((code, result['until_days']), (0, 1))
        state = cr.load_state(self.state)
        self.assertEqual(state['rejected']['gemini-3.8-flash']['days'], 1)
        self.assertTrue(cr.cooling(state, 'gemini-3.8-flash', date(2026, 10, 24)))
        self.assertFalse(cr.cooling(state, 'gemini-3.8-flash', date(2026, 10, 25)))
        del state['rejected']['gemini-3.8-flash']['days']
        self.assertTrue(cr.cooling(state, 'gemini-3.8-flash', date(2026, 10, 30)))
        self.assertFalse(cr.cooling(state, 'gemini-3.8-flash', date(2026, 10, 31)))

    def test_disabled_lane_never_serves_last_good(self):
        self.run_cli('mark-good', '--key', 'gemini/flash/', '--slug', 'gemini-3.8-flash', '--state', self.state)
        with patch.object(cga, 'list_models', side_effect=cga.APIError('gemini_api.enabled is false', 'config')):
            self.assertEqual(self.api_resolve()[1]['reason'], 'no-catalog')

    def test_unreachable_last_good_and_excluded_last_good(self):
        with patch.object(cga, 'list_models', side_effect=cga.APIError('synthetic unavailable', 'network')):
            self.assertEqual(self.api_resolve()[1]['reason'], 'no-catalog')
            self.run_cli('mark-good', '--key', 'gemini/flash/', '--slug', 'gemini-3.8-flash', '--state', self.state)
            self.assertEqual(self.api_resolve()[1]['from'], 'last_good')
            self.assertEqual(self.api_resolve('--exclude', 'gemini-3.8-flash')[1]['reason'], 'no-catalog')


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


class CodexReview(Env):
    """Regression tests for the Codex review of 2026-10-23 (resolver)."""

    def test_auth_and_quota_with_model_context_are_not_model_rejected(self):
        self.assertEqual(cr.classify_text("401: model gpt-6.1-sol: invalid API key"), "auth")
        self.assertEqual(cr.classify_text("429: model gpt-6.1-sol not available: quota exceeded"), "quota")
        self.assertEqual(cr.classify_text("ERROR: The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account"), "model-rejected")

    def test_date_suffix_does_not_override_version(self):
        for dated in ("gpt-6-sol-20261001", "gpt-6-sol-2026-10-01", "gpt-6-sol-261001"):
            r = self.resolve("gpt", "sol", "high", [dated, "gpt-6.1-sol"])
            self.assertEqual(r["slug"], "gpt-6.1-sol", dated)

    def test_concurrent_state_updates_survive(self):
        import threading
        errors = []

        def work(i):
            try:
                if i % 2:
                    cr.main(["mark-good", "--key", f"gemini/argon/k{i}", "--slug", f"gemini-4-argon-s{i}", "--state", self.state])
                else:
                    cr.main(["mark-rejected", "--slug", f"gpt-6.{i}-sol", "--state", self.state, "--today", "2026-10-23"])
            except Exception as e:  # pragma: no cover
                errors.append(e)

        with contextlib.redirect_stdout(io.StringIO()):
            threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        self.assertEqual(errors, [])
        with open(self.state, encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(len(st["last_good"]), 8)
        self.assertEqual(len(st["rejected"]), 8)
        self.assertFalse(os.path.exists(self.state + ".lock"))

    def test_nonzero_catalog_exit_uses_last_good(self):
        old = cr.run_cli

        def failing(names, args):
            raise cr.UsageError("agy exited 1: authentication failed")

        cr.run_cli = failing
        try:
            self.run_cli("mark-good", "--key", "gemini/argon/medium", "--slug", "gemini-4-argon-medium", "--state", self.state)
            code, r = self.run_cli("resolve", "--family", "gemini", "--line", "argon", "--effort", "medium", "--source", "agy", "--state", self.state)
        finally:
            cr.run_cli = old
        self.assertEqual((r["status"], r["from"], r["slug"]), ("ok", "last_good", "gemini-4-argon-medium"))

    def test_run_cli_nonzero_exit_raises(self):
        import sys
        old = cr.shutil.which
        cr.shutil.which = lambda n: sys.executable
        try:
            with self.assertRaises(cr.UsageError):
                cr.run_cli(["agy"], ["-c", "import sys; sys.stderr.write('authentication failed'); sys.exit(1)"])
        finally:
            cr.shutil.which = old

    def test_empty_catalog_is_unreachable_not_no_line_match(self):
        code, r = self.run_cli("resolve", "--family", "gemini", "--line", "argon", "--source", self.catalog_file([]), "--state", self.state)
        self.assertEqual((r["status"], r["reason"]), ("skip", "no-catalog"))

    def test_equivalent_versions_choose_exact_effort(self):
        r = self.resolve("claude", "opus", "high", ["claude-opus-5-high", "claude-opus-5.0-low"])
        self.assertEqual(r["slug"], "claude-opus-5-high")

    def test_release_suffix_preserves_line(self):
        for suffix in ("latest", "preview", "beta"):
            p = cr.parse_slug(f"grok-4.7-{suffix}")
            self.assertEqual((p["line"], p["version"]), ("grok", (4, 7)), suffix)
        r = self.resolve("grok", "grok", None, ["grok-4.7-latest"])
        self.assertEqual(r["slug"], "grok-4.7-latest")

    def test_stable_beats_preview_at_equal_version(self):
        r = self.resolve("gemini", "flash", "high", ["gemini-3.8-flash-high-preview", "gemini-3.8-flash-high"])
        self.assertEqual(r["slug"], "gemini-3.8-flash-high")

    def test_invalid_state_and_catalog_structures(self):
        for bad in ("[]", '{"rejected": [], "last_good": {}}', '{"rejected": {}, "last_good": []}', '{"rejected": {"x": 5}}'):
            with open(self.state, "w", encoding="utf-8") as f:
                f.write(bad)
            code, _ = self.run_cli("resolve", "--family", "gpt", "--line", "sol", "--source", self.catalog_file(["gpt-6-sol"]), "--state", self.state)
            self.assertEqual(code, 2, bad)
        home = os.path.join(self.d, "home2")
        os.makedirs(os.path.join(home, ".codex"))
        with open(os.path.join(home, ".codex", "models_cache.json"), "w", encoding="utf-8") as f:
            f.write('{"models": null}')
        old = os.environ.get("USERPROFILE"), os.environ.get("HOME")
        os.environ["USERPROFILE"] = os.environ["HOME"] = home
        try:
            with self.assertRaises(cr.UsageError):
                cr.catalog("codex")
        finally:
            for k, v in zip(("USERPROFILE", "HOME"), old):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
