import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    import council_roster as cr
except ImportError:
    cr = None

ROOT = Path(__file__).resolve().parent.parent
SHARED = 'config_version: 2\nseats:\n  - family: gemini\n    line: argon\n    label: Primary\n'


class Roster(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cr, 'stage-1 roster module is missing')

    def build(self, local=None, origin='default', shared=SHARED):
        return cr.build_roster(shared, local, origin)

    def bad(self, local, key, shared=SHARED):
        with self.assertRaisesRegex(cr.APIError, key):
            self.build(local, shared=shared)

    def test_shipped_roster_both_origins(self):
        shared = (ROOT / 'config.yaml').read_text(encoding='utf-8-sig')
        for origin, sol, sonnet in [('default', 'codex', 'agent'), ('cursor', 'task', 'task')]:
            result = self.build(origin=origin, shared=shared)
            seats = result['seats']
            self.assertEqual([s['seat'] for s in seats], [1, 1, 2, None, None])
            self.assertEqual([s['transports'] for s in seats], [['api', 'agy'], ['api', 'agy'], [sol], ['api', 'agy'], [sonnet]])
            self.assertEqual(seats[1]['fallback_for'], 'Gemini Argon (breadth)')
            self.assertEqual(seats[1]['role'], 'breadth-auditor')
            self.assertTrue(seats[1]['fallback'])
            self.assertEqual(seats[2]['dispatch'], [{'transport': sol, 'runner': 'codex' if origin == 'default' else None, 'source': sol}])
            self.assertEqual(seats[4]['dispatch'][0]['source'], None if origin == 'default' else 'task')

    def test_routes_merge_and_empty(self):
        shared = SHARED + 'routes:\n  default:\n    gpt: []\n    gemini: [agy]\n'
        result = self.build('routes:\n  default:\n    gemini: []\n', shared=shared)
        self.assertEqual(result['routes']['gpt'], [])
        self.assertEqual(result['seats'][0]['skip'], 'no-transport')
        self.assertEqual(self.build(origin='cursor', shared=shared)['routes']['gemini'], ['api', 'agy'])

    def test_transport_matrix_and_invalid_routes(self):
        allowed = {'default': {'gemini': ['api', 'agy'], 'gpt': ['codex'], 'grok': ['cursor_cli'], 'claude': ['agent'], 'composer': []},
                   'cursor': {'gemini': ['api', 'agy'], 'gpt': ['task'], 'grok': ['cursor_cli'], 'claude': ['task'], 'composer': ['task']}}
        for origin, families in allowed.items():
            for family, valid in families.items():
                for transport in ['api', 'agy', 'codex', 'cursor_cli', 'task', 'agent', 'cursor', 'grok']:
                    local = f'routes:\n  {origin}:\n    {family}: [{transport}]\n'
                    with self.subTest(origin=origin, family=family, transport=transport):
                        if transport in valid:
                            self.build(local)
                        else:
                            self.bad(local, f'routes.{origin}.{family}')
        self.bad('routes:\n  other:\n    gpt: []\n', 'routes.other')
        self.bad('routes:\n  default:\n    other: []\n', 'routes.default.other')

    def test_gemini_cursor_rejected_everywhere(self):
        for value in ['cursor', 'cursor_cli', 'task']:
            for local in [f'gemini_transport: [{value}]\n', f'routes:\n  cursor:\n    gemini: [{value}]\n',
                          f'seats:\n  - family: gemini\n    line: flash\n    transports: [{value}]\n']:
                self.bad(local, 'config.migrations.yaml version 2')
        self.bad('routes:\n  default:\n    grok: [cursor]\n', 'cursor_cli')

    def test_alias_warning_conflict_and_cross_file_override(self):
        result = self.build('gemini_transport: [agy, api]\n')
        self.assertEqual(result['routes']['gemini'], ['agy', 'api'])
        self.assertTrue(result['warnings'])
        self.bad('gemini_transport: [api]\nroutes:\n  cursor:\n    gemini: [agy]\n', 'gemini_transport')
        self.assertEqual(self.build('routes:\n  default:\n    gemini: [agy]\n', shared=SHARED + 'gemini_transport: [api]\n')['routes']['gemini'], ['agy'])

    def test_seat_transport_filter_and_dispatch(self):
        local = 'seats:\n- family: gpt\n  line: sol\n  transports:\n    - task\n'
        result = self.build(local)['seats'][0]
        self.assertEqual((result['skip'], result['transports_source']), ('no-transport', 'seat'))
        self.assertIn('task', result['skip_detail'])
        self.assertEqual(self.build(local, 'cursor')['seats'][0]['transports'], ['task'])
        grok = self.build('seats:\n- family: grok\n  line: grok\n')['seats'][0]
        self.assertEqual(grok['dispatch'], [{'transport': 'cursor_cli', 'runner': 'cursor', 'source': 'cursor'}])

    def test_fallback_numbering_and_truncation(self):
        local = '''max_reviewers: 1
seats:
  - family: gemini
    line: argon
    role: breadth-auditor
    label: Primary
  - family: gemini
    line: flash
    label: Backup
    fallback_for: Primary
  - family: gpt
    line: sol
    label: Second
  - family: gemini
    line: flash
    label: Second backup
    fallback_for: Second
  - family: claude
    line: sonnet
    role: rewriter
'''
        result = self.build(local)
        self.assertEqual([s['seat'] for s in result['seats']], [1, 1, None])
        self.assertEqual(result['seats'][1]['role'], 'breadth-auditor')
        self.assertEqual(result['truncated'], ['Second', 'Second backup'])
        self.assertEqual(result['source'], 'config.local.yaml')

    def test_fallback_errors(self):
        primary = 'seats:\n- family: gemini\n  line: argon\n  label: Primary\n'
        backup = '- family: gemini\n  line: flash\n  label: Backup\n  fallback_for: Primary\n'
        cases = [backup.replace('Primary', 'missing'), backup.replace('Primary', 'primary'),
                 backup.replace('  label: Backup\n', ''), backup + backup.replace('Backup', 'Another'),
                 '- family: gpt\n  line: sol\n' + backup, backup.replace('fallback_for: Primary', 'fallback_for: Backup'),
                 backup.replace('  fallback_for:', '  role: rewriter\n  fallback_for:')]
        for suffix in cases:
            with self.subTest(suffix=suffix):
                self.bad(None, 'fallback_for|label', shared=primary + suffix)
        self.bad(None, 'fallback_for', shared=primary.replace('  label:', '  role: rewriter\n  label:') + backup)
        self.bad(None, 'fallback_for', shared=primary + backup + backup.replace('fallback_for: Primary', 'fallback_for: Backup').replace('label: Backup', 'label: Third'))
        self.bad(None, 'fallback_for', shared=primary + backup.replace('label: Backup', 'label: Primary'))

    def test_validation_duplicates_and_legacy(self):
        for text, key in [('max_reviewers: 0', 'max_reviewers'), ('max_reviewers: true', 'max_reviewers'),
                          ('max_reviewers: "2"', 'max_reviewers'), ('reviewers:\n- family: gpt', 'migration'),
                          ('cursor_reviewers: []', 'migration'), ('seats: nope', 'seats'),
                          ('routes:\n  default:\n    gpt: [codex, codex]', 'routes.default.gpt'),
                          ('max_reviewers: 1\nmax_reviewers: 2', 'max_reviewers')]:
            with self.subTest(text=text):
                self.bad(text, key)
        for entry, key in [('other: value', 'other'), ('role: bad', 'role'), ('family: bad', 'family'),
                           ('effort: bad', 'effort'), ('line: true', 'line'), ('line: ""', 'line'),
                           ('family: gpt\n  family: gemini', 'family')]:
            self.bad('seats:\n- family: gemini\n  line: flash\n  ' + entry + '\n', key)
        self.bad('seats:\n- family: gpt\n', 'line')

    def test_replace_keep_and_text_syntax(self):
        self.assertEqual(self.build('max_reviewers: 2\n')['seats'][0]['line'], 'argon')
        self.assertEqual(self.build('seats: []\n')['seats'], [])
        text = '\ufeffseats:\r\n- family: gpt # comment\r\n  line: sol\r\n  label: "A (quoted) # label"\r\n  pin: \'test-slug\'\r\n'
        seat = self.build(text)['seats'][0]
        self.assertEqual((seat['label'], seat['pin'], seat['effort'], seat['role']), ('A (quoted) # label', 'test-slug', 'medium', 'reviewer'))

    def test_tabs_in_indentation_are_rejected(self):
        for text, line in [('seats:\n\t- family: gpt\n  line: sol\n', 2),
                           ('\tmax_reviewers: 1\n', 1),
                           (SHARED + '  \tfallback_for: Primary\n', 6),
                           ('unrelated:\n \tvalue: synthetic\n' + SHARED, 2)]:
            with self.subTest(line=line, text=text), self.assertRaises(cr.APIError) as caught:
                self.build(text)
            self.assertEqual(str(caught.exception), f'config: line {line}: tabs are not allowed for indentation')

    def test_malformed_line_does_not_echo_contents(self):
        with self.assertRaises(cr.APIError) as caught:
            self.build('unrelated: ignored\n\n' + SHARED + '    SYNTHETIC_SECRET_abcdef123456\n')
        self.assertEqual(str(caught.exception), 'config: line 8: malformed line')

    def test_top_level_spaced_keys_and_stray_lists_are_rejected(self):
        for text, line in [('seats : []\n', 1), ('routes : {}\n', 1),
                           ('unrelated: ignored\n- family: gpt\n  line: sol\n', 2),
                           ('- family: gpt\n  line: sol\n', 1),
                           ('max_reviewers: 2\n- family: gpt\n', 2)]:
            with self.subTest(text=text), self.assertRaises(cr.APIError) as caught:
                self.build(text)
            self.assertEqual(str(caught.exception), f'config: line {line}: malformed line')

    def test_structured_values_are_config_errors(self):
        for text, key in [('seats:\n- family: []\n  line: sol\n', 'family'),
                          ('seats:\n- family: {}\n  line: sol\n', 'family'),
                          ('seats:\n- family: gpt\n  line: sol\n  effort: []\n', 'effort'),
                          ('seats:\n- family: gemini\n  line: flash\n  transports: {}\n', 'transports')]:
            with self.subTest(text=text):
                self.bad(text, key)

    def test_roster_parser_rejects_bad_indentation_and_duplicate_keys(self):
        for text in ['seats:\n    - family: gpt\n      line: sol\n',
                     'routes:\n    default:\n      gemini: [api]\n',
                     'routes:\n  default:\n    gemini: [api]\n    gemini: [agy]\n',
                     'routes:\n  default:\n    gemini: [api]\n  default:\n    gemini: [agy]\n',
                     'seats:\n- family: gemini\n  line: flash\n  role: reviewer\n  role: rewriter\n']:
            with self.subTest(text=text), self.assertRaises(cr.APIError):
                self.build(text)


class Versions(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cr, 'stage-1 roster module is missing')
        self.migrations = (ROOT / 'config.migrations.yaml').read_text(encoding='utf-8')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'config.yaml').write_text(SHARED, encoding='utf-8')
        (self.root / 'config.migrations.yaml').write_text(self.migrations, encoding='utf-8')

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cr.main([args[0], '--root', str(self.root), *args[1:]])
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        return code, json.loads(out.getvalue())

    def candidate(self, text):
        path = self.root / 'candidate.yaml'
        path.write_text(text, encoding='utf-8')
        return str(path)

    def test_write_local_preserves_api_bytes_and_backup(self):
        local = self.root / 'config.local.yaml'
        api = b'gemini_api:\r\n  enabled: false # synthetic\r\n  tier: free\r\n'
        original = (b'\xef\xbb\xbf# keep\r\nbased_on_config_version: 1 # reviewed\r\n' + api
                    + b'\r\nseats:\r\n- family: gemini\r\n  line: argon\r\n'
                    + b'# keep this comment\r\nruns_dir: synthetic-runs\r\n')
        local.write_bytes(original)
        candidate = self.candidate('seats:\n- family: gpt\n  line: sol\n')
        code, result = self.cli('write-local', '--candidate', candidate)
        self.assertEqual(code, 0, result)
        written = local.read_bytes()
        self.assertIn(api, written)
        self.assertTrue(written.startswith(b'\xef\xbb\xbf# keep\r\n'))
        self.assertIn(b'based_on_config_version: 2 # reviewed\r\n', written)
        self.assertIn(b'# keep this comment\r\nruns_dir: synthetic-runs\r\n', written)
        self.assertNotIn(b'\n', written.replace(b'\r\n', b''))
        self.assertEqual((self.root / 'config.prev.local.yaml').read_bytes(), original)
        for origin in cr.ROUTES:
            self.assertEqual(cr.build_roster(SHARED, written.decode('utf-8-sig'), origin)['seats'][0]['line'], 'sol')

    def test_write_local_creates_and_appends_allowed_sections(self):
        candidate = self.candidate('based_on_config_version: 2\nshow_exchange: always\nmax_reviewers: 3\n'
                                   'routes:\n  default:\n    gemini: [agy]\n')
        code, result = self.cli('write-local', '--candidate', candidate)
        self.assertEqual(code, 0, result)
        local = self.root / 'config.local.yaml'
        self.assertIn('based_on_config_version: 2', local.read_text())
        self.assertFalse((self.root / 'config.prev.local.yaml').exists())
        local.write_bytes(b'runs_dir: synthetic-runs')
        code, result = self.cli('write-local', '--candidate', candidate)
        self.assertEqual(code, 0, result)
        self.assertTrue(local.read_bytes().startswith(b'runs_dir: synthetic-runs\n'))

    def test_write_local_refuses_candidates_without_writing_or_leaking(self):
        local = self.root / 'config.local.yaml'
        original = b'based_on_config_version: 2\ngemini_api:\n  enabled: false\n'
        local.write_bytes(original)
        backup = self.root / 'config.prev.local.yaml'
        backup.write_bytes(b'previous synthetic backup')
        for text in ['gemini_api:\n  enabled: true\n', 'key_file: SYNTHETIC_SECRET_abcdef\n',
                     'gemini_transport: [api]\n', 'unknown: SYNTHETIC_SECRET_abcdef\n',
                     'seats:\n- family: gemini\n  line: flash\n  transports: [task]\n',
                     'routes:\n  cursor:\n    gpt: [codex]\n', 'show_exchange: invalid\n',
                     'show_exchange: []\n', 'max_reviewers: 0\n', 'seats : []\n',
                     '  gemini_api:\n    enabled: true\n',
                     'seats: []\nseats: []\n', 'based_on_config_version: 3\n']:
            with self.subTest(text=text):
                code, result = self.cli('write-local', '--candidate', self.candidate(text))
                self.assertEqual(code, 2, result)
                self.assertNotIn('SYNTHETIC_SECRET_abcdef', json.dumps(result))
                self.assertEqual(local.read_bytes(), original)
                self.assertEqual(backup.read_bytes(), b'previous synthetic backup')
        # A valid candidate must also be refused when the retained local routes
        # make the merged configuration invalid in the other origin.
        original = b'routes:\n  cursor:\n    gpt: [codex]\n'
        local.write_bytes(original)
        candidate = self.candidate('seats:\n- family: gpt\n  line: sol\n')
        code, result = self.cli('write-local', '--candidate', candidate)
        self.assertEqual(code, 2, result)
        self.assertEqual(local.read_bytes(), original)
        self.assertEqual(backup.read_bytes(), b'previous synthetic backup')

    def test_write_local_replaces_routes_and_scalars_and_preserves_absent_sections(self):
        local = self.root / 'config.local.yaml'
        original = ('max_reviewers: 1\nshow_exchange: never\nroutes:\n  default:\n    gemini: [agy]\n'
                    'seats:\n- family: gpt\n  line: sol\n')
        local.write_text(original, encoding='utf-8')
        candidate = self.candidate('max_reviewers: 4\nshow_exchange: ask\nroutes:\n  cursor:\n    grok: []\n')
        code, result = self.cli('write-local', '--candidate', candidate)
        self.assertEqual(code, 0, result)
        written = local.read_text()
        self.assertIn('seats:\n- family: gpt\n  line: sol\n', written)
        parsed = cr.parse_config(written)
        self.assertEqual(parsed['max_reviewers'], 4)
        self.assertEqual(parsed['routes'], {'cursor': {'grok': []}})
        self.assertEqual(cr.parse_subset(written, {'show_exchange'}), {'show_exchange': 'ask'})

    def test_write_local_atomic_failure_keeps_original(self):
        local = self.root / 'config.local.yaml'
        original = b'based_on_config_version: 2\n'
        local.write_bytes(original)
        candidate = self.candidate('max_reviewers: 3\n')
        replace = cr.os.replace
        def fail_local(source, target):
            if target == local:
                raise OSError('synthetic write failure')
            return replace(source, target)
        with patch.object(cr.os, 'replace', side_effect=fail_local):
            code, result = self.cli('write-local', '--candidate', candidate)
        self.assertEqual(code, 2, result)
        self.assertEqual(local.read_bytes(), original)
        self.assertEqual((self.root / 'config.prev.local.yaml').read_bytes(), original)
        self.assertEqual(list(self.root.glob('.council-stamp-*')), [])

    def test_write_local_isolated_entrypoint(self):
        candidate = self.candidate('max_reviewers: 3\n')
        process = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/council_roster.py'),
                                  'write-local', '--root', str(self.root), '--candidate', candidate],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        self.assertEqual(len(process.stdout.splitlines()), 1)
        self.assertEqual(json.loads(process.stdout)['based_on'], 2)

    def test_presets_validate_both_origins_and_expected_rosters(self):
        shared = (ROOT / 'config.yaml').read_text(encoding='utf-8')
        paths = sorted((ROOT / 'presets').glob('*.yaml'))
        self.assertEqual([p.name for p in paths], ['api-only.yaml', 'claude-code-full.yaml', 'cursor-originated.yaml'])
        for path in paths:
            text = path.read_text(encoding='utf-8')
            self.assertEqual(cr.parse_config(text)['based_on_config_version'], 2)
            for origin in cr.ROUTES:
                roster = cr.build_roster(shared, text, origin)
                if path.stem == 'api-only':
                    self.assertEqual(roster['routes']['gemini'], ['api'])
                    self.assertTrue(all(s['transports'] == ['api'] for s in roster['seats'] if s['family'] == 'gemini'))
                else:
                    family = 'grok' if path.stem == 'claude-code-full' else 'claude'
                    self.assertEqual([s['family'] for s in roster['seats']], ['gemini', 'gemini', 'gpt', family, 'gemini', 'claude'])
                code, result = self.cli('write-local', '--candidate', str(path))
                self.assertEqual(code, 0, result)

    def test_version_states(self):
        for local, based, count in [(None, None, 0), ('based_on_config_version: 2\n', 2, 0), ('', 1, 1)]:
            result = cr.check_version(SHARED, local, self.migrations)
            self.assertEqual((result['based_on'], len(result['migrations'])), (based, count))
        for local in ['based_on_config_version: 3', 'based_on_config_version: true', 'based_on_config_version: "2"', 'based_on_config_version: 0']:
            with self.assertRaisesRegex(cr.APIError, 'based_on_config_version'):
                cr.check_version(SHARED, local, self.migrations)

    def test_migration_schema_and_coverage(self):
        result = cr.check_version(SHARED, '', self.migrations)
        self.assertEqual([m['version'] for m in result['migrations']], [2])
        entry = result['migrations'][0]
        self.assertEqual(len(entry['changes']), 9)
        for change in entry['changes']:
            self.assertEqual(set(change), {'key', 'kind', 'meaning', 'action', 'example'})
        for text in ['migrations: []', self.migrations + self.migrations.split('migrations:\n')[1],
                     self.migrations.replace('kind: removed', 'kind: invalid', 1), self.migrations.replace('    title:', '    unknown:', 1)]:
            with self.assertRaises(cr.APIError):
                cr.check_version(SHARED, '', text)

    def test_shipped_migration_versions_do_not_exceed_shipped_config(self):
        shared = (ROOT / 'config.yaml').read_text(encoding='utf-8')
        version, _ = cr.version_info(shared, None)
        entries = cr.parse_subset(self.migrations, {'migrations'})['migrations']
        self.assertTrue(all(entry['version'] <= version for entry in entries))

    def test_future_migrations_ignored_for_project_config(self):
        for shared in [SHARED.replace('config_version: 2\n', ''), SHARED.replace('config_version: 2', 'config_version: 1')]:
            (self.root / 'config.yaml').write_text(shared, encoding='utf-8')
            for command in [('load', '--origin', 'default'), ('check',)]:
                with self.subTest(shared=shared, command=command):
                    code, result = self.cli(*command)
                    self.assertEqual(code, 0, result)
                    self.assertEqual(result['migrations'], [])
        future = self.migrations + self.migrations.split('migrations:\n')[1].replace('version: 2', 'version: 3', 1)
        self.assertEqual([entry['version'] for entry in cr.check_version(SHARED, '', future)['migrations']], [2])

    def test_version_checks_allow_zero_indented_seats(self):
        shared = SHARED.replace('  - family:', '- family:').replace('    line:', '  line:').replace('    label:', '  label:')
        (self.root / 'config.yaml').write_text(shared, encoding='utf-8')
        code, result = self.cli('load', '--origin', 'default')
        self.assertEqual(code, 0, result)
        self.assertEqual(result['seats'][0]['label'], 'Primary')

    def test_stamp_repairs_non_integer_stamp(self):
        local = self.root / 'config.local.yaml'
        for value in ["'1'", '"2"', 'invalid', 'true', '[]', 'null', '"unterminated']:
            original = f'# synthetic\r\nbased_on_config_version: {value} # keep\r\nother: untouched\r\n'.encode()
            local.write_bytes(original)
            self.assertEqual(self.cli('check')[0], 2)
            self.assertEqual(self.cli('stamp', '--version', '3')[0], 2)
            self.assertEqual(local.read_bytes(), original)
            code, result = self.cli('stamp')
            self.assertEqual(code, 0, result)
            self.assertEqual(local.read_bytes(), b'# synthetic\r\nbased_on_config_version: 2 # keep\r\nother: untouched\r\n')
        local.write_text('based_on_config_version: 3\n', encoding='utf-8')
        before = local.read_bytes()
        self.assertEqual(self.cli('stamp')[0], 2)
        self.assertEqual(local.read_bytes(), before)

    def test_cli_parser_errors_name_path_and_original_line(self):
        path = self.root / 'config.yaml'
        for text, message in [('unrelated: ignored\n\nseats:\n  \tlabel: synthetic\n', 'tabs are not allowed for indentation'),
                              ('unrelated: ignored\n\nseats:\n  SYNTHETIC_SECRET_abcdef123456\n', 'malformed line'),
                              ('unrelated: ignored\n\n# comment\nseats : []\n', 'malformed line')]:
            path.write_text(text, encoding='utf-8')
            code, result = self.cli('load', '--origin', 'default')
            self.assertEqual(code, 2)
            self.assertEqual(result['detail'], f'{path}: line 4: {message}')

    def test_cli_load_validate_check_migrations(self):
        self.assertEqual(self.cli('load', '--origin', 'default')[0], 0)
        local = self.root / 'config.local.yaml'
        local.write_text('gemini_transport: [api, agy]\n', encoding='utf-8')
        self.assertEqual(self.cli('load', '--origin', 'cursor')[0], 3)
        self.assertEqual(self.cli('check')[0], 3)
        self.assertEqual(self.cli('validate')[0], 0)
        self.assertEqual(len(self.cli('migrations')[1]['migrations']), 1)
        self.assertEqual(self.cli('migrations', '--since', '2')[1]['migrations'], [])
        local.write_text('gemini_transport: [api, cursor, agy]\n', encoding='utf-8')
        code, result = self.cli('load', '--origin', 'default')
        self.assertEqual(code, 2)
        self.assertIn('version 2', result['detail'])
        self.assertEqual(result['migrations'][0]['version'], 2)
        candidate = self.root / 'candidate.yaml'
        candidate.write_text('routes:\n  cursor:\n    claude: [agent]\n', encoding='utf-8')
        self.assertEqual(self.cli('validate', '--local', str(candidate))[0], 2)
        self.assertEqual(self.cli('load', '--origin', 'unknown')[0], 2)

    def test_stamp_preserves_bytes_and_refuses_downgrade(self):
        self.assertEqual(self.cli('stamp')[0], 0)
        local = self.root / 'config.local.yaml'
        self.assertEqual(local.read_bytes(), b'based_on_config_version: 2\n')
        for original, expected in [(b'\xef\xbb\xbf# synthetic\r\nbased_on_config_version: 1 # old\r\nother: untouched\r\n',
                                    b'\xef\xbb\xbf# synthetic\r\nbased_on_config_version: 2 # old\r\nother: untouched\r\n'),
                                   (b'# synthetic\r\nother: untouched', b'# synthetic\r\nother: untouched\r\nbased_on_config_version: 2\r\n')]:
            local.write_bytes(original)
            self.assertEqual(self.cli('stamp')[0], 0)
            self.assertEqual(local.read_bytes(), expected)
        before = local.read_bytes()
        for version in ['1', '3', 'nope']:
            self.assertEqual(self.cli('stamp', '--version', version)[0], 2)
            self.assertEqual(local.read_bytes(), before)

    def test_isolated_entrypoint_and_error_redaction(self):
        script = ROOT / 'scripts/council_roster.py'
        result = subprocess.run([sys.executable, '-I', str(script), 'load', '--root', str(self.root), '--origin', 'default'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['seats'][0]['seat'], 1)
        (self.root / 'config.local.yaml').write_text('gemini_transport: [SYNTHETIC_PRIVATE_VALUE]\n', encoding='utf-8')
        code, result = self.cli('load', '--origin', 'default')
        self.assertEqual(code, 2)
        self.assertNotIn('SYNTHETIC_PRIVATE_VALUE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
