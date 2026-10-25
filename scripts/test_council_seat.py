"""Offline seat-runner contracts: fake Python CLIs only, never vendor binaries."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    import council_seat as seat
except ImportError:
    seat = None


class SeatTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(seat, 'council_seat runner must exist')
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run = self.root / 'runs' / 'run'
        self.run.mkdir(parents=True)
        self.prompt = self.run / 'prompt.md'
        self.prompt.write_text('UNIQUE_PROMPT_MARKER', encoding='utf-8')
        self.reply = self.run / 'reply.txt'
        self.stub = self.root / 'stub.py'
        # Block accidental real CLI/network access, including inherited credentials.
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'COUNCIL_GEMINI_API_KEY': ''}).start()
        patch.object(seat, 'resolve_launcher', return_value=([sys.executable, str(self.stub)], 'fake')).start()
        patch.object(seat.api, 'read_config', return_value={'gemini_api': {'timeout_sec': 300}}).start()

    def invoke(self, body, transport='agy', extra=(), command='run'):
        self.stub.write_text(body, encoding='utf-8')
        args = [command, '--transport', transport, '--run', str(self.run), '--root', str(self.root), '--model', 'm']
        if command == 'run':
            args += ['--seat', '1', '--label', 'L', '--prompt-file', str(self.prompt), '--reply', str(self.reply)]
        args += list(extra)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = seat.main(args)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        return code, json.loads(lines[0])

    def failures(self):
        return (self.run / 'failures.md').read_text(encoding='utf-8')

    def test_success_usage_and_atomic_reply(self):
        response = json.dumps({'response': 'hello\nworld', 'usage': {'total': 7}})
        code, data = self.invoke(f'print({response!r})')
        self.assertEqual((code, data['status'], data['usage']), (0, 'ok', {'total': 7}))
        self.assertEqual(self.reply.read_bytes(), b'hello\nworld')
        self.assertFalse((self.run / 'failures.md').exists())
        meta = json.loads((self.run / 'seat-1-argv-meta.txt').read_text())
        self.assertEqual(meta['usage'], {'total': 7})
        self.assertNotIn('UNIQUE_PROMPT_MARKER', str(meta))

    def test_failure_classes_and_single_log(self):
        for error, want in [('429 quota', 'quota'), ('model x not found', 'model-rejected'),
                            ('not authenticated', 'auth'), ('permission check failed', 'permission-gate'),
                            ('user denied permission', 'permission-gate'), ('Hook blocked', 'exit-nonzero'),
                            ('$OutputEncoding', 'exit-nonzero'), ('unexpected failure', 'exit-nonzero')]:
            with self.subTest(error=error):
                self.reply.write_text('existing', encoding='utf-8')
                before = self.failures().count('\n') if (self.run / 'failures.md').exists() else 0
                code, data = self.invoke(f'import sys; print({error!r}, file=sys.stderr); sys.exit(1)')
                self.assertEqual((code, data['class']), (1, want))
                self.assertEqual(self.failures().count('\n'), before + 1)
                self.assertIn(f'seat 1 (L): agy-{want} [agy m] (', self.failures())
                self.assertEqual(self.reply.read_text(), 'existing')

    def test_empty_and_unusable_json(self):
        for body, want in [('print(\'{"response":""}\')', 'empty'), ('print("not json")', 'unusable-stdout')]:
            code, data = self.invoke(body)
            self.assertEqual((code, data['class']), (1, want))

    def test_last_json_line_and_cursor_error(self):
        code, data = self.invoke('print("noise"); print(\'{"result":"yes"}\')', 'cursor')
        self.assertEqual(code, 0)
        self.assertEqual(self.reply.read_text(), 'yes')
        code, data = self.invoke('print(\'{"result":"429 quota","is_error":true}\')', 'cursor')
        self.assertEqual((code, data['class']), (1, 'quota'))

    def test_stdin_closed_and_empty_cwd(self):
        body = 'import json, os, sys; print(json.dumps({"response":json.dumps([len(sys.stdin.read()),os.getcwd(),os.listdir(".")])}))'
        code, _ = self.invoke(body)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.reply.read_text()), [0, str(self.run.parent / '_cwd'), []])
        (self.run.parent / '_cwd' / 'keep').write_text('keep')
        self.invoke(body)
        meta = json.loads((self.run / 'seat-1-argv-meta.txt').read_text())
        self.assertTrue(meta['cwd_not_empty'])

    def test_key_and_prompt_redaction_everywhere(self):
        key = 'SYNTHETIC_' + 'x' * 29
        fixture = self.root / 'fixture.txt'
        fixture.write_text(key)
        os.environ['COUNCIL_GEMINI_API_KEY'] = key
        body = ('import os, sys; from pathlib import Path; '
                f'print(Path({str(fixture)!r}).read_text(), sys.argv[-1], file=sys.stderr); '
                'print("LEAK" if "COUNCIL_GEMINI_API_KEY" in os.environ else "stripped", file=sys.stderr); sys.exit(1)')
        code, data = self.invoke(body)
        self.assertEqual(code, 1)
        self.assertNotIn(key, json.dumps(data))
        for name in ('seat-1-argv-meta.txt', 'seat-1-stderr.txt', 'failures.md'):
            text = (self.run / name).read_text()
            self.assertNotIn(key, text)
            self.assertNotIn('UNIQUE_PROMPT_MARKER', text)
            self.assertIn('***', text)
        self.assertIn('stripped', (self.run / 'seat-1-stderr.txt').read_text())

    def test_codex_fresh_last_message_and_zero_exit_rejection(self):
        body = 'import sys; from pathlib import Path; Path(sys.argv[sys.argv.index("-o")+1]).write_text("fresh")'
        code, _ = self.invoke(body, 'codex')
        self.assertEqual(code, 0)
        self.assertEqual(self.reply.read_text(), 'fresh')
        code, data = self.invoke('pass', 'codex')
        self.assertEqual((code, data['class']), (1, 'empty'))
        self.assertFalse((self.run / 'seat-1-last.txt').exists())
        code, data = self.invoke(body + '; print("model x not found", file=sys.stderr)', 'codex')
        self.assertEqual((code, data['status']), (0, 'ok'))
        code, data = self.invoke('import sys; print("model x not found", file=sys.stderr)', 'codex')
        self.assertEqual((code, data['class']), (1, 'model-rejected'))

    def test_successful_replies_may_discuss_failure_patterns(self):
        text = 'Hook blocked; $OutputEncoding; OutputEncoding; permission check failed; user denied permission'
        for transport, field in [('agy', 'response'), ('cursor', 'result'), ('grok', 'text')]:
            with self.subTest(transport=transport):
                code, data = self.invoke(f'print({json.dumps({field: text})!r})', transport)
                self.assertEqual((code, data['status']), (0, 'ok'))
                self.assertEqual(self.reply.read_text(), text)
                self.assertFalse((self.run / 'failures.md').exists())

    def test_cursor_hook_errors_only_in_diagnostics(self):
        for marker in ('Hook blocked', '$OutputEncoding'):
            for body in (f'import sys; print({marker!r}, file=sys.stderr)',
                         f'print({marker!r})',
                         f'print({json.dumps({"result": marker, "is_error": True})!r})'):
                with self.subTest(marker=marker, body=body):
                    code, data = self.invoke(body, 'cursor')
                    self.assertEqual((code, data['class']), (1, 'cursor-hook-bug'))

    def test_codex_rejection_requires_no_fresh_nonempty_reply(self):
        for last_text in ('', 'stale'):
            body = ('import os,sys; from pathlib import Path; '
                    'p=Path(sys.argv[sys.argv.index("-o")+1]); '
                    f'p.write_text({last_text!r}); '
                    + ('os.utime(p, (1, 1)); ' if last_text else '')
                    + 'print("model x not found", file=sys.stderr)')
            self.assertEqual(self.invoke(body, 'codex')[1]['class'], 'model-rejected')

    def test_codex_nonzero_prefers_recent_stderr(self):
        body = ('import sys; print("model x not found", file=sys.stderr); '
                'print("session context\\n" * 25, file=sys.stderr); '
                'print("429 quota", file=sys.stderr); sys.exit(1)')
        code, data = self.invoke(body, 'codex')
        self.assertEqual((code, data['class']), (1, 'quota'))
        self.assertIn('429 quota', data['detail'])
        self.assertNotIn('model x not found', self.failures())

    def test_short_prompts_do_not_redact_json_keys_or_values(self):
        for prompt in ('e', '"', 'short'):
            self.prompt.write_text(prompt)
            payload = {'response': 'hello', 'usage': {'echo': prompt, prompt: 'value'}}
            code, data = self.invoke(f'print({json.dumps(payload)!r})')
            self.assertEqual((code, data['status'], data['exit']), (0, 'ok', 0))
            self.assertEqual(data['usage'], payload['usage'])
            meta = json.loads((self.run / 'seat-1-argv-meta.txt').read_text())
            self.assertEqual(meta['exit'], 0)

    def test_long_prompt_redaction_preserves_json_keys(self):
        prompt = 'synthetic prompt content longer than sixteen'
        self.prompt.write_text(prompt)
        payload = {'response': 'ok', 'usage': {prompt: prompt}}
        code, data = self.invoke(f'print({json.dumps(payload)!r})')
        self.assertEqual(code, 0)
        self.assertEqual(data['usage'], {prompt: '<prompt>'})

    def test_prompt_redaction_starts_at_sixteen_characters(self):
        for prompt, expected in [('x' * 15, 'x' * 15), ('x' * 16, '<prompt>')]:
            self.prompt.write_text(prompt)
            payload = {'response': 'ok', 'usage': {'echo': prompt}}
            code, data = self.invoke(f'print({json.dumps(payload)!r})')
            self.assertEqual(code, 0)
            self.assertEqual(data['usage']['echo'], expected)

    def test_usage_refusals_do_not_log_or_launch(self):
        for transport, flags in [('agy', ['--timeout', '5']), ('codex', ['--delivery', 'inline'])]:
            with self.subTest(transport=transport), patch.object(seat, 'execute', side_effect=AssertionError):
                code, data = self.invoke('', transport, flags)
                self.assertEqual((code, data['status'], data['class']), (2, 'refused', 'usage'))
                self.assertTrue(data['detail'])
                self.assertFalse((self.run / 'failures.md').exists())

    def test_reply_must_resolve_inside_run(self):
        outside = self.root / 'outside.txt'
        outside.write_text('preserve')
        for target in (outside, self.run / '..' / '..' / 'outside.txt', self.run):
            with self.subTest(target=target), patch.object(seat, 'execute', side_effect=AssertionError):
                code, data = self.invoke('', extra=['--reply', str(target)])
                self.assertEqual((code, data['status'], data['class']), (2, 'refused', 'usage'))
                self.assertFalse((self.run / 'failures.md').exists())
                self.assertEqual(outside.read_text(), 'preserve')
        nested = self.run / 'nested' / 'reply.txt'
        self.assertEqual(self.invoke('print(\'{"response":"ok"}\')', extra=['--reply', str(nested)])[0], 0)
        self.assertEqual(nested.read_text(), 'ok')

    def test_cursor_ps51_warning_is_summary_only(self):
        for runtime, expected in [('powershell.exe', True), ('pwsh.exe', False)]:
            prefix = [runtime, '-NoProfile', '-NonInteractive', '-File', 'synthetic.ps1']
            def fake_execute(argv, cwd, env, timeout, out, err):
                Path(out).write_text('{"result":"PREFLIGHT-OK"}')
                Path(err).write_text('')
                return 0, False, 0
            for command in ('run', 'preflight'):
                with patch.object(seat, 'resolve_launcher', return_value=(prefix, 'powershell')), \
                        patch.object(seat, 'execute', fake_execute), patch.object(seat.sys, 'platform', 'win32'):
                    code, data = self.invoke('', 'cursor', command=command)
                self.assertEqual(code, 0)
                self.assertEqual('cursor-ps51' in json.dumps(data), expected)
                self.assertFalse((self.run / 'failures.md').exists())

    def test_concurrent_failure_appends_keep_every_line(self):
        worker = self.root / 'append_worker.py'
        # Release both independent processes together to exercise the append race.
        worker.write_text(
            'import sys,time; from pathlib import Path\n'
            f'sys.path.insert(0, {str(Path(seat.__file__).parent)!r})\n'
            'import council_parse as cp\n'
            'run,tag=sys.argv[1:]\n'
            'Path(run, "ready-"+tag).touch()\n'
            'while not Path(run, "go").exists(): time.sleep(0.005)\n'
            'for i in range(60): cp.log_failure(run, f"seat {tag} (synthetic): failure {i} café")\n',
            encoding='utf-8')
        with contextlib.ExitStack() as stack:
            processes = [stack.enter_context(subprocess.Popen(
                [sys.executable, '-B', '-I', str(worker), str(self.run), tag],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
                for tag in ('1', '2')]
            stack.callback(lambda: [p.kill() for p in processes if p.poll() is None])
            deadline = time.monotonic() + 10
            while not all((self.run / ('ready-' + tag)).exists() for tag in ('1', '2')):
                if time.monotonic() > deadline:
                    self.fail('append workers did not become ready')
                time.sleep(0.01)
            (self.run / 'go').touch()
            for p in processes:
                _, err = p.communicate(timeout=15)
                self.assertEqual(p.returncode, 0, err.decode(errors='replace'))
        raw = (self.run / 'failures.md').read_bytes()
        self.assertNotIn(b'\r', raw)
        lines = raw.decode('utf-8').splitlines()
        expected = {f'seat {tag} (synthetic): failure {i} café' for tag in ('1', '2') for i in range(60)}
        self.assertEqual(len(lines), 120)
        self.assertEqual(set(lines), expected)

    def test_skip_validation_and_utf8_lf(self):
        for reason, want in [('bad reason', 2), ('same-model', 0)]:
            with contextlib.redirect_stdout(io.StringIO()):
                code = seat.main(['skip', '--run', str(self.run), '--seat', '2', '--label', 'L',
                                  '--reason', reason, '--detail', 'synthetic é\nnext'])
            self.assertEqual(code, want)
        raw = (self.run / 'failures.md').read_bytes()
        self.assertEqual(raw.decode(), 'seat 2 (L): same-model (synthetic é next)\n')

    def test_timeout_defaults_and_validation(self):
        for local, shared, want in [(None, None, 600), (None, 120, 120), (90, 120, 90)]:
            for name, value in [('config.local.yaml', local), ('config.yaml', shared)]:
                if value is not None:
                    (self.root / name).write_text(f'cli_timeout_sec: {value} # comment\n')
            self.assertEqual(seat.cli_timeout(self.root), want)
        code, _ = self.invoke('raise AssertionError("must not launch")', extra=['--timeout', '29'])
        self.assertEqual(code, 2)

    def test_run_and_preflight_not_found(self):
        with patch.object(seat, 'resolve_launcher', side_effect=seat.SeatError('not-found', 'missing')):
            code, data = self.invoke('', command='preflight')
        self.assertEqual((code, data['class']), (1, 'not-found'))
        self.assertEqual(self.failures(), 'preflight (agy): cli-preflight-agy (not-found: missing)\n')

    def test_preflight_success_version_and_grok_token(self):
        body = 'import sys; print("v1" if "--version" in sys.argv else \'{"response":"PREFLIGHT-OK"}\')'
        code, data = self.invoke(body, command='preflight')
        self.assertEqual((code, data['version']), (0, 'v1'))
        self.assertEqual((self.run / 'cli-preflight.txt').read_text(), 'PREFLIGHT-OK')
        self.assertFalse((self.run / 'failures.md').exists())
        code, data = self.invoke('print(\'{"text":"wrong"}\')', 'grok', command='preflight')
        self.assertEqual(code, 1)
        self.assertIn('preflight (grok): cli-preflight-agent (', self.failures())

    def test_codex_preflight_probe_deleted(self):
        body = ('import sys; from pathlib import Path; '
                'Path(sys.argv[sys.argv.index("-o")+1]).write_text("PREFLIGHT-OK WRITE-CREATED"); '
                f'Path({str(self.root / ".council-write-probe")!r}).write_text("probe")')
        code, data = self.invoke(body, 'codex', command='preflight')
        self.assertEqual((code, data['class']), (1, 'codex-write-deny'))
        self.assertFalse((self.root / '.council-write-probe').exists())

    def test_api_delegate_preserves_failed_reply_and_warning(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        stub = scripts / 'council_gemini_api.py'
        self.reply.write_text('existing')
        for status, category, want in [('failed', 'quota', 1), ('refused', 'config', 2), ('ok', 'ok', 0)]:
            stub.write_text('import sys; from pathlib import Path; '
                            'Path(sys.argv[sys.argv.index("--out")+1]).write_text("api reply"); '
                            f'print({seat.api.FREE_WARNING!r},file=sys.stderr); '
                            f'print({json.dumps({"status": status, "class": category, "usage": {"total": 9}})!r}); '
                            f'sys.exit({want})')
            code, data = self.invoke('', 'api')
            self.assertEqual(code, want)
            self.assertTrue(data['free_tier_warning'])
            self.assertEqual(self.reply.read_text(), 'api reply' if want == 0 else 'existing')
        self.assertIn('api-quota', self.failures())
        self.assertIn('api-refused', self.failures())

    def test_rewrite_tail_and_api_prompt(self):
        code, _ = self.invoke('import json,sys; print(json.dumps({"response":sys.argv[-1]}))',
                              extra=['--kind', 'rewrite', '--stem', 'rewrite/chunk-3'])
        self.assertEqual(code, 0)
        self.assertEqual(self.reply.read_text(), 'UNIQUE_PROMPT_MARKER\n\nDo not use any tools or read any files. Output only the rewritten chunk followed by the final REWRITE_END line.')

    def test_inline_refused_for_pointer_transports(self):
        for transport in ('codex', 'grok'):
            code, _ = self.invoke('raise AssertionError', transport, ['--delivery', 'inline'])
            self.assertEqual(code, 2)

    def test_single_quote_prompt_keeps_summary_and_meta_valid_json(self):
        self.prompt.write_text('"')
        code, data = self.invoke('print(\'{"response":"hello", "usage":{"echo":"\\\\\\\""}}\')')
        self.assertEqual((code, data['status']), (0, 'ok'))
        json.loads((self.run / 'seat-1-argv-meta.txt').read_text())

    def test_empty_stdout_classified_empty(self):
        code, data = self.invoke('pass')
        self.assertEqual((code, data['class']), (1, 'empty'))

    def test_failed_version_is_ignored(self):
        body = ('import sys; '
                'print("not a version" if "--version" in sys.argv else \'{"response":"PREFLIGHT-OK"}\'); '
                'sys.exit(1 if "--version" in sys.argv else 0)')
        code, data = self.invoke(body, command='preflight')
        self.assertEqual((code, data['version']), (0, None))

    def test_api_rewrite_tail_timeout_and_metadata(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        stub = scripts / 'council_gemini_api.py'
        stub.write_text('import json,sys; from pathlib import Path; '
                        'prompt=Path(sys.argv[sys.argv.index("--prompt-file")+1]).read_text(); '
                        'Path(sys.argv[sys.argv.index("--out")+1]).write_text(prompt); '
                        'print(\'{"status":"ok","class":"ok","usage":{"total":4}}\')')
        original = seat.execute
        limits = []
        def recording(*args):
            limits.append(args[3])
            return original(*args)
        with patch.object(seat, 'execute', recording):
            code, data = self.invoke('', 'api', ['--kind', 'rewrite', '--stem', 'rewrite/chunk-3', '--timeout', '90', '--effort', 'low'])
        self.assertEqual((code, limits), (0, [210]))
        expected = 'UNIQUE_PROMPT_MARKER\n\nDo not use any tools or read any files. Output only the rewritten chunk followed by the final REWRITE_END line.'
        self.assertEqual(self.reply.read_text(), expected)
        self.assertEqual((self.run / 'rewrite/chunk-3-api-prompt.md').read_text(), expected)
        meta = json.loads((self.run / 'rewrite/chunk-3-argv-meta.txt').read_text())
        self.assertEqual(meta['api_summary']['usage'], {'total': 4})
        self.assertIn('--effort', meta['argv'])
        self.assertFalse(list((self.run / 'rewrite').glob('.seat-api-*')))

    def test_api_preflight_delegates_without_generation(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        (scripts / 'council_gemini_api.py').write_text(
            'import sys; assert sys.argv[1] == "preflight"; '
            'assert "--out" not in sys.argv; print(\'{"status":"ok","class":"ok"}\')')
        self.assertEqual(self.invoke('', 'api', command='preflight')[0], 0)
        self.assertFalse((self.run / 'failures.md').exists())

    def test_isolated_cli_smoke(self):
        proc = subprocess.run([sys.executable, '-I', str(Path(seat.__file__)), 'skip', '--run', str(self.run),
                               '--seat', '1', '--label', 'L', '--reason', 'no-transport'],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {'status': 'skipped'})

    def test_argv_snapshots(self):
        from argparse import Namespace
        a = Namespace(root=self.root, run=self.run, model='m', effort='medium', add_dir=['review-root'])
        prefix, prompt, cwd, last = ['fake'], 'PROMPT', 'empty', self.run / 'last'
        cases = [
            ('agy', 'inline', ['fake', '--model', 'm', '--output-format', 'json', '-p', 'PROMPT']),
            ('agy', 'pointer', ['fake', '--add-dir', str(self.root), '--add-dir', str(self.run),
                                '--add-dir', 'review-root', '--model', 'm', '--output-format', 'json', '-p', 'PROMPT']),
            ('cursor', 'inline', ['fake', '-p', 'PROMPT', '--model', 'm', '--output-format', 'json',
                                  '--mode', 'ask', '--trust', '--workspace', 'empty']),
            ('grok', 'pointer', ['fake', '-p', 'PROMPT', '--model', 'm', '--permission-mode', 'plan',
                                 '--output-format', 'json', '--cwd', str(self.run), '--max-turns', '12']),
        ]
        for transport, delivery, want in cases:
            a.transport, a.delivery = transport, delivery
            self.assertEqual(seat.build_argv(a, prefix, prompt, cwd, last), want)
        a.transport = 'codex'
        for platform in ('linux', 'win32'):
            with patch.object(seat.sys, 'platform', platform):
                argv = seat.build_argv(a, prefix, prompt, cwd, last)
            want = ['fake', 'exec', '-s', 'read-only', '--ephemeral', '--ignore-user-config']
            if platform == 'win32':
                want += ['-c', 'windows.sandbox="unelevated"']
            want += ['--skip-git-repo-check', '-C', str(self.root), '--add-dir', str(self.root),
                     '--add-dir', str(self.run), '--add-dir', 'review-root', '-m', 'm', '-c',
                     'model_reasoning_effort="medium"', '-o', str(last), 'PROMPT']
            self.assertEqual(argv, want)

    def test_msystem_only_removed_for_windows_cursor(self):
        seen = []
        def fake_execute(argv, cwd, env, timeout, out, err):
            seen.append(env.copy())
            Path(out).write_text('{"result":"ok"}')
            Path(err).write_text('')
            return 0, False, 0
        with patch.dict(os.environ, {'MSYSTEM': 'MINGW64'}), patch.object(seat, 'execute', fake_execute):
            for platform in ('win32', 'linux'):
                with patch.object(seat.sys, 'platform', platform):
                    self.assertEqual(self.invoke('', 'cursor')[0], 0)
        self.assertNotIn('MSYSTEM', seen[0])
        self.assertEqual(seen[1]['MSYSTEM'], 'MINGW64')

    def test_windows_argv_limit_refuses_before_launch(self):
        self.prompt.write_text('x' * 31000)
        with patch.object(seat.sys, 'platform', 'win32'), patch.object(seat, 'execute', side_effect=AssertionError):
            code, data = self.invoke('')
        self.assertEqual((code, data['class']), (2, 'argv-too-long'))
        self.assertIn('agy-argv-too-long [agy m]', self.failures())

    def test_popen_contract_no_shell_and_closed_stdin(self):
        real_popen = subprocess.Popen
        calls = []
        def record(argv, **kwargs):
            calls.append((argv, kwargs))
            return real_popen(argv, **kwargs)
        with patch.object(seat.subprocess, 'Popen', record):
            self.assertEqual(self.invoke('print(\'{"response":"ok"}\')')[0], 0)
        argv, kwargs = calls[0]
        self.assertIsInstance(argv, list)
        self.assertFalse(kwargs.get('shell', False))
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertNotEqual(kwargs['stdout'], subprocess.PIPE)
        source = Path(seat.__file__).read_text()
        self.assertNotIn('shell=True', source)
        self.assertNotIn('os.system', source)

    def test_timeout_kills_parent_and_grandchild(self):
        pidfile = self.root / 'pids.json'
        child_code = 'import time; time.sleep(60)'
        self.stub.write_text('import json,os,subprocess,sys,time; from pathlib import Path; '
                             f'p=subprocess.Popen([sys.executable,"-c",{child_code!r}]); '
                             f'Path({str(pidfile)!r}).write_text(json.dumps([os.getpid(),p.pid])); time.sleep(60)')
        code, timed_out, seconds = seat.execute([sys.executable, str(self.stub)], self.root, os.environ.copy(),
                                               2, self.root / 'out', self.root / 'err')
        self.assertTrue(timed_out)
        self.assertLess(seconds, 10)
        pids = json.loads(pidfile.read_text())
        def alive(pid):
            if sys.platform == 'win32':
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.OpenProcess.restype = wintypes.HANDLE
                handle = kernel.OpenProcess(0x1000, False, pid)
                if not handle:
                    return False
                try:
                    code = wintypes.DWORD()
                    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                    kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                    return code.value == 259
                finally:
                    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                    kernel.CloseHandle(handle)
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
        deadline = time.monotonic() + 8
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(any(alive(pid) for pid in pids))
        with patch.object(seat, 'execute', return_value=(code, True, seconds)):
            _, data = self.invoke('')
        self.assertEqual(data['class'], 'timeout')


class LauncherTests(unittest.TestCase):
    def resolve(self, transport, mapping, platform='win32', env=None):
        # Call the original, not SeatTests' per-test fake resolver.
        return seat.resolve_launcher(transport, which=mapping.get, platform=platform, env=env or {})

    def test_cursor_powershell_order_and_missing_runtime(self):
        mapping = {'cursor-agent.ps1': 'cursor.ps1', 'pwsh': 'pwsh.exe', 'powershell': 'powershell.exe'}
        self.assertEqual(self.resolve('cursor', mapping),
                         (['pwsh.exe', '-NoProfile', '-NonInteractive', '-File', 'cursor.ps1'], 'powershell'))
        del mapping['pwsh']
        self.assertEqual(self.resolve('cursor', mapping)[0][0], 'powershell.exe')
        del mapping['powershell']
        with self.assertRaises(seat.SeatError) as raised:
            self.resolve('cursor', mapping)
        self.assertEqual(raised.exception.category, 'ps-runtime')

    def test_batch_files_refused_and_posix_plain_paths(self):
        for transport, name in [('agy', 'agy'), ('cursor', 'cursor-agent'), ('codex', 'codex'), ('grok', 'agent')]:
            self.assertEqual(self.resolve(transport, {name: '/fake/' + name}, 'linux')[0], ['/fake/' + name])
            for suffix in ('.cmd', '.BAT'):
                with self.assertRaises(seat.SeatError):
                    self.resolve(transport, {name: name + suffix}, 'linux')

    def test_codex_exe_node_powershell_order(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / 'npm/node_modules/@openai/codex/bin/codex.js'
            script.parent.mkdir(parents=True)
            script.write_text('// synthetic')
            mapping = {'codex.exe': 'native.exe', 'node': 'node.exe', 'codex.ps1': 'codex.ps1', 'pwsh': 'pwsh.exe'}
            env = {'APPDATA': temp}
            self.assertEqual(self.resolve('codex', mapping, env=env)[0], ['native.exe'])
            del mapping['codex.exe']
            self.assertEqual(self.resolve('codex', mapping, env=env), (['node.exe', str(script)], 'node'))
            script.unlink()
            self.assertEqual(self.resolve('codex', mapping, env=env)[0][-1], 'codex.ps1')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell argv round trip')
    def test_powershell_prompt_roundtrip(self):
        import shutil
        runtime = shutil.which('pwsh')
        if not runtime:
            self.skipTest('pwsh unavailable')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / 'fake.ps1'
            script.write_text('[Console]::Out.Write($args[0])', encoding='utf-8')
            prompt = 'quotes "double" and \'single\'\n$cash `tick %percent%'
            argv = seat.resolve_launcher('cursor', which={'cursor-agent.ps1': str(script), 'pwsh': runtime}.get,
                                          platform='win32', env={})[0] + [prompt]
            code, timeout, _ = seat.execute(argv, root, os.environ.copy(), 10, root / 'out', root / 'err')
            self.assertEqual((code, timeout), (0, False))
            self.assertEqual((root / 'out').read_text(), prompt)


class LauncherDiscovery(unittest.TestCase):
    def test_cursor_ps1_found_by_exact_name_when_which_ignores_ps1(self):
        # shutil.which on Windows with Python < 3.12 never matches '.ps1' (not in PATHEXT).
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'cursor-agent.ps1'
            script.write_text('', encoding='utf-8')
            shell = Path(folder) / 'pwsh.exe'
            shell.write_text('', encoding='utf-8')
            fake_which = lambda name: str(shell) if name == 'pwsh' else None
            prefix, kind = seat.resolve_launcher('cursor', which=fake_which, platform='win32', env={'PATH': folder})
        self.assertEqual(kind, 'powershell')
        self.assertEqual(prefix[-1], str(script))
        self.assertNotIn('-ExecutionPolicy', prefix)


if __name__ == '__main__':
    unittest.main()
