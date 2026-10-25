import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import council_install as ci

SOURCE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.dest = os.path.join(self.repo, ".cursor", "skills", "council")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = ci.main(list(args))
            except SystemExit as e:
                code = e.code
        try:
            return code, json.loads(out.getvalue().strip())
        except ValueError:
            return code, err.getvalue().strip()

    def test_install_copies_only_what_cursor_needs(self):
        code, s = self.run_cli("install", "--target", self.repo)
        self.assertEqual((code, s["status"]), (0, "installed"))
        for rel in ('scripts/council_roster.py', 'config.migrations.yaml'):
            self.assertTrue(os.path.isfile(os.path.join(self.dest, rel)), rel)
        for rel in ci.FILES:
            self.assertTrue(os.path.isfile(os.path.join(self.dest, rel)), rel)
        for absent in ("claude", "recipes", "README.md", "CHANGELOG.md", "models.md", "runs", "config.local.yaml"):
            self.assertFalse(os.path.exists(os.path.join(self.dest, absent)), absent)
        tests = [f for f in os.listdir(os.path.join(self.dest, "scripts")) if f.startswith("test_")]
        self.assertEqual(tests, [])
        self.assertTrue(os.path.isfile(os.path.join(self.dest, ci.MANIFEST)))

    def test_front_matter_name_matches_the_folder(self):
        self.run_cli("install", "--target", self.repo)
        with open(os.path.join(self.dest, "SKILL.md"), encoding="utf-8") as f:
            self.assertRegex(f.read(600), r"(?m)^name:\s*council\s*$")
        self.assertEqual(os.path.basename(self.dest), "council")

    def test_secrets_and_local_configs_never_copied(self):
        src = os.path.join(self.repo, 'synthetic-source')
        for rel in ci.FILES:
            target = os.path.join(src, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(os.path.join(SOURCE, rel), target)
        secrets = ('gemini.key', 'scripts/gemini.key', 'config.local.yaml', 'config.local.example.yaml')
        for rel in secrets:
            with open(os.path.join(src, rel), 'w', encoding='utf-8') as stream:
                stream.write('synthetic private data')
        code, _ = self.run_cli('install', '--target', self.repo, '--source', src)
        self.assertEqual(code, 0)
        with open(os.path.join(self.dest, ci.MANIFEST), encoding='utf-8') as stream:
            manifest = json.load(stream)
        for rel in secrets:
            self.assertFalse(os.path.exists(os.path.join(self.dest, rel)))
            self.assertNotIn(rel, manifest['files'])
        self.assertTrue(os.path.isfile(os.path.join(self.dest, 'scripts/council_gemini_api.py')))
        self.assertIn('scripts/council_gemini_api.py', manifest['files'])

    def test_reinstall_is_idempotent_and_check_is_in_sync(self):
        self.run_cli("install", "--target", self.repo)
        code, s = self.run_cli("install", "--target", self.repo)
        self.assertEqual((code, s["written"], s["skipped_local_changes"]), (0, [], []))
        code, s = self.run_cli("check", "--target", self.repo)
        self.assertEqual((code, s["status"]), (0, "in-sync"))

    def test_local_edits_are_protected_until_forced(self):
        self.run_cli("install", "--target", self.repo)
        cfg = os.path.join(self.dest, "config.yaml")
        with open(cfg, "a", encoding="utf-8") as f:
            f.write("\n# repo-specific tweak\n")
        code, s = self.run_cli("install", "--target", self.repo)
        self.assertEqual((code, s["skipped_local_changes"]), (1, ["config.yaml"]))
        with open(cfg, encoding="utf-8") as f:
            self.assertIn("repo-specific tweak", f.read())
        code, s = self.run_cli("check", "--target", self.repo)
        self.assertEqual((code, s["changed_locally"]), (1, ["config.yaml"]))
        code, s = self.run_cli("install", "--target", self.repo, "--force")
        self.assertEqual(code, 0)
        with open(cfg, encoding="utf-8") as f:
            self.assertNotIn("repo-specific tweak", f.read())

    def test_source_updates_flow_into_an_untouched_copy(self):
        src = os.path.join(self.repo, "src")
        os.makedirs(os.path.join(src, "scripts"))
        for rel in ci.FILES:
            os.makedirs(os.path.dirname(os.path.join(src, rel)), exist_ok=True)
            with open(os.path.join(SOURCE, rel), "rb") as a, open(os.path.join(src, rel), "wb") as b:
                b.write(a.read())
        target = os.path.join(self.repo, "proj")
        os.makedirs(target)
        self.run_cli("install", "--target", target, "--source", src)
        with open(os.path.join(src, "core.md"), "a", encoding="utf-8") as f:
            f.write("\nnew rule\n")
        code, s = self.run_cli("check", "--target", target, "--source", src)
        self.assertEqual((code, s["outdated_vs_source"]), (1, ["core.md"]))
        code, s = self.run_cli("install", "--target", target, "--source", src)
        self.assertEqual((code, s["written"]), (0, ["core.md"]))

    def test_dry_run_writes_nothing(self):
        code, s = self.run_cli("install", "--target", self.repo, "--dry-run")
        self.assertEqual(s["status"], "dry-run")
        self.assertFalse(os.path.exists(self.dest))

    def test_errors_exit_2(self):
        self.assertEqual(self.run_cli("install", "--target", os.path.join(self.repo, "nope"))[0], 2)
        bad = os.path.join(self.repo, "badsrc")
        os.makedirs(bad)
        self.assertEqual(self.run_cli("install", "--target", self.repo, "--source", bad)[0], 2)

    def test_check_before_install(self):
        code, s = self.run_cli("check", "--target", self.repo)
        self.assertEqual((code, s["status"]), (1, "not-installed"))


if __name__ == "__main__":
    unittest.main()
