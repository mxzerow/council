import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import council_gemini_api as cga

KEY = 'TESTKEY_aaaaaaaaaaaaaaaaaaaaaaaa'


class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.status = 200
        cls.message = 'synthetic failure'
        cls.get_status = 200
        cls.delay = 0
        cls.reply = {}
        cls.pages = {}
        cls.redirect = False

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_GET(self):
                cls.requests.append(('GET', self.path, dict(self.headers), None))
                if cls.redirect and self.path != '/redirected':
                    self.send_response(302)
                    self.send_header('Location', 'http://127.0.0.1:%s/redirected' % cls.server.server_port)
                    self.end_headers()
                elif cls.get_status != 200:
                    self.respond(cls.get_status, {'error': {'message': cls.message}})
                elif self.path.startswith('/models?'):
                    self.respond(200, cls.pages.get(self.path, {'models': []}))
                else:
                    self.respond(200, {'outputTokenLimit': 32768 if 'argon' in self.path else 65536})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                cls.requests.append(('POST', self.path, dict(self.headers), body))
                time.sleep(cls.delay)
                self.respond(cls.status, cls.reply if cls.status == 200 else {'error': {'message': cls.message}})

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'HOME': str(self.root), 'USERPROFILE': str(self.root),
                                           'COUNCIL_GEMINI_API_KEY': '', 'HTTP_PROXY': '',
                                           'HTTPS_PROXY': '', 'ALL_PROXY': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.base = patch.object(cga, 'BASE_URL', 'http://127.0.0.1:%s' % self.server.server_port)
        self.base.start()
        self.addCleanup(self.base.stop)
        type(self).requests = []
        type(self).status = type(self).get_status = 200
        type(self).message = 'synthetic failure'
        type(self).delay = 0
        type(self).pages = {}
        type(self).redirect = False
        type(self).reply = {'candidates': [{'content': {'parts': [{'text': 'synthetic reply'}]}, 'finishReason': 'STOP'}],
                            'usageMetadata': {'promptTokenCount': 10, 'thoughtsTokenCount': 2,
                                              'candidatesTokenCount': 3, 'totalTokenCount': 15}}
        self.config('enabled: true\ntier: paid')
        os.environ['COUNCIL_GEMINI_API_KEY'] = KEY
        (self.root / 'prompt.md').write_text('synthetic prompt', encoding='utf-8')

    def config(self, block, local=False):
        (self.root / ('config.local.yaml' if local else 'config.yaml')).write_text(
            'gemini_api:\n' + ''.join('  ' + s + '\n' for s in block.splitlines()), encoding='utf-8')

    def cli(self, command='call', *extra):
        argv = [command, '--root', str(self.root)]
        if command == 'call':
            argv += ['--model', 'gemini-3.8-flash', '--prompt-file', str(self.root / 'prompt.md'),
                     '--out', str(self.root / 'reply.md')]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cga.main(argv + list(extra))
        self.assertNotIn(KEY, out.getvalue() + err.getvalue())
        return code, json.loads(out.getvalue()), err.getvalue()

    def test_success_header_usage_and_body(self):
        code, result, _ = self.cli()
        self.assertEqual((code, result['status'], result['class']), (0, 'ok', 'ok'))
        self.assertEqual(result['usage'], {'prompt': 10, 'thoughts': 2, 'output': 3, 'total': 15})
        self.assertEqual((self.root / 'reply.md').read_text(), 'synthetic reply')
        method, url, headers, body = self.requests[-1]
        self.assertEqual({k.lower(): v for k, v in headers.items()}['x-goog-api-key'], KEY)
        self.assertNotIn('key=', url)
        self.assertEqual(body['generationConfig'], {'maxOutputTokens': 32768, 'thinkingConfig': {'thinkingLevel': 'high'}})
        self.assertEqual(body['contents'][0]['parts'], [{'text': 'synthetic prompt'}])
        self.assertNotIn('service_tier', body)

    def test_thoughts_stripped_and_overrides(self):
        self.reply['candidates'][0]['content']['parts'] = [{'thought': True, 'text': 'private reasoning'}, {'text': 'answer'}, {'text': '!'}]
        _, result, _ = self.cli('call', '--kind', 'rewrite', '--effort', 'low')
        self.assertEqual(result['max_output'], 65536)
        self.assertEqual((self.root / 'reply.md').read_text(), 'answer!')
        self.assertEqual(self.requests[-1][3]['generationConfig']['thinkingConfig']['thinkingLevel'], 'low')
        _, result, _ = self.cli('call', '--model', 'gemini-4-argon', '--max-output', '65536')
        self.assertEqual(result['max_output'], 32768)

    def test_http_failure_classes_redaction_and_no_overwrite(self):
        (self.root / 'reply.md').write_text('previous reply')
        for status, message, category in [(429, 'quota', 'quota'), (503, 'busy', 'unavailable'),
                (404, 'missing', 'model-rejected'), (400, 'model foo not supported', 'model-rejected'),
                (400, 'unsupported model foo', 'model-rejected'), (400, 'bad thinking level', 'other'),
                (401, 'bad key', 'auth'), (403, 'denied', 'auth'), (500, 'broken', 'network'),
                (502, 'gateway', 'network'), (504, 'deadline', 'timeout')]:
            with self.subTest(status=status, message=message):
                type(self).status = status
                type(self).message = message + ' ' + KEY + ('x' * 500)
                code, result, _ = self.cli()
                self.assertEqual((code, result['class'], result['http']), (1, category, status))
                self.assertLessEqual(len(result['detail']), 300)
                self.assertEqual((self.root / 'reply.md').read_text(), 'previous reply')

    def test_get_404_never_posts(self):
        type(self).get_status = 404
        self.assertEqual(self.cli()[1]['class'], 'model-rejected')
        self.assertEqual([r[0] for r in self.requests], ['GET'])

    def test_timeout_no_output(self):
        type(self).delay = 3
        code, result, _ = self.cli('call', '--timeout', '1')
        self.assertEqual((code, result['class']), (1, 'timeout'))
        self.assertFalse((self.root / 'reply.md').exists())

    def test_empty_and_missing_candidates(self):
        for reply in ({}, {'candidates': []}, {'candidates': [{'content': {'parts': [{'thought': True, 'text': 'hidden'}]}}]},
                      {'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'parts': []}}]}):
            type(self).reply = reply
            self.assertEqual(self.cli()[1]['class'], 'empty')
            self.assertFalse((self.root / 'reply.md').exists())

    def test_gates(self):
        for config in ('enabled: false', 'enabled: true\ntier: free\nfree_tier_ack: false'):
            self.config(config)
            code, result, _ = self.cli()
            self.assertEqual((code, result['status'], result['class']), (2, 'refused', 'config'))
        self.assertEqual(self.requests, [])
        self.config('enabled: true\ntier: free\nfree_tier_ack: true')
        self.assertEqual(self.cli()[2], cga.FREE_WARNING + '\n')
        self.assertEqual(self.cli('call', '--tier', 'paid')[2], '')

    def test_key_sources_and_encodings(self):
        path = self.root / 'synthetic-key.txt'
        self.config('key_file: "' + path.as_posix() + '"', local=True)
        path.write_text('INVALID-CONTENTS')
        self.assertEqual(self.cli()[0], 0)  # env wins without opening file
        os.environ.pop('COUNCIL_GEMINI_API_KEY')
        for encoding in ('utf-8-sig', 'utf-16', 'utf-16-be'):
            raw = (KEY + '\n').encode(encoding)
            if encoding == 'utf-16-be':
                raw = b'\xfe\xff' + raw
            path.write_bytes(raw)
            path.chmod(0o600)
            self.assertEqual(self.cli()[0], 0, encoding)
        path.write_text('synthetic.' + KEY)  # newer Google keys contain a dot
        path.chmod(0o600)
        self.assertEqual(self.cli()[0], 0, 'dotted key')
        self.config('enabled: true\ntier: paid')
        (self.root / 'config.local.yaml').unlink()
        default = self.root / '.council' / 'gemini.key'
        default.parent.mkdir()
        default.write_text(KEY)
        default.chmod(0o600)
        self.assertEqual(self.cli()[0], 0)

    def test_invalid_missing_key_and_permissions(self):
        os.environ.pop('COUNCIL_GEMINI_API_KEY')
        self.assertEqual(self.cli()[0], 2)
        path = self.root / 'synthetic-key.txt'
        self.config('key_file: ' + path.as_posix(), local=True)
        for raw in (b'INVALID-CONTENTS', (KEY + '\n' + KEY).encode(), b'\xff'):
            path.write_bytes(raw)
            path.chmod(0o600)
            code, result, err = self.cli()
            self.assertEqual(code, 2)
            self.assertIn('invalid format', result['detail'])
            self.assertNotIn('INVALID-CONTENTS', json.dumps(result) + err)
        if os.name != 'nt':
            path.write_text(KEY)
            path.chmod(0o644)
            self.assertEqual(self.cli()[0], 2)

    def test_config_validation(self):
        invalid = [('enabled', 'yes'), ('free_tier_ack', '"true"'), ('tier', 'fast'), ('thinking_level', 'minimal'),
                   ('timeout_sec', '29'), ('timeout_sec', '3601'), ('timeout_sec', 'true'), ('timeout_sec', '3.5'),
                   ('max_output_review', '1023'), ('max_output_rewrite', '65537'), ('max_model_steps', '-1'),
                   ('max_model_steps', '6'), ('key_file', 'false'), ('unknown', 'true')]
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                self.config(key + ': ' + value)
                code, result, _ = self.cli('config')
                self.assertEqual(code, 2)
                self.assertIn(key, result['detail'])
        for value in ('[]', '[agy, agy]', '[foo]', 'agy', '[agy, cursor', '[agy, true]'):
            (self.root / 'config.yaml').write_text('gemini_transport: ' + value)
            code, result, _ = self.cli('config')
            self.assertEqual(code, 2, value)
            self.assertIn('gemini_transport', result['detail'])

    def test_config_merge_lists_quotes_and_timeouts(self):
        (self.root / 'config.yaml').write_text('seats:\n  - family: gemini\nother: ignored\ngemini_transport:\n  - agy\n  - "api" # comment\ngemini_api:\n  tier: paid\n  enabled: true\n')
        self.config("thinking_level: 'medium' # comment\nkey_file: 'some#path'", local=True)
        code, result, _ = self.cli('config')
        self.assertEqual(code, 0)
        self.assertEqual(result['gemini_transport'], ['agy', 'api'])
        self.assertEqual(result['gemini_api']['timeout_sec'], 300)
        self.assertEqual(result['gemini_api']['thinking_level'], 'medium')
        self.assertEqual(result['key_source'], 'env')
        self.config('tier: free', local=True)
        self.assertEqual(self.cli('config')[1]['gemini_api']['timeout_sec'], 1200)
        self.config('tier: paid\ntimeout_sec: 60', local=True)
        self.assertEqual(self.cli('config')[1]['gemini_api']['timeout_sec'], 60)

    def test_preflight_and_paginated_models(self):
        self.assertEqual(self.cli('preflight')[1]['status'], 'ok')
        type(self).get_status = 403
        self.assertEqual(self.cli('preflight')[1]['class'], 'auth')
        type(self).get_status = 200
        slugs = ['gemini-3.8-flash', 'gemini-3.8-flash-latest', 'gemini-3.8-flash-preview',
                 'gemini-3.8-flash-exp', 'gemini-3.8-flash-experimental']
        type(self).pages = {'/models?pageSize=1000': {'models': [
            {'name': 'models/' + slug, 'supportedGenerationMethods': ['generateContent']} for slug in slugs
        ] + [{'name': 'models/embedding', 'supportedGenerationMethods': ['embedContent']}], 'nextPageToken': 'page 2'},
            '/models?pageSize=1000&pageToken=page+2': {'models': [
                {'name': 'models/gemini-3.6-flash', 'supportedGenerationMethods': ['generateContent']} ]}}
        self.assertEqual(cga.list_models(self.root), ['gemini-3.8-flash', 'gemini-3.6-flash'])

    def test_usage_errors_are_json_and_redacted(self):
        for args in (('--key', KEY), ('--timeout', '0'), ('--effort', 'minimal'), ('--model', '../escape'),
                     ('--max-output', '-1')):
            self.assertEqual(self.cli('call', *args)[0], 2)

    def test_network_and_io_failures(self):
        with patch.object(cga, 'BASE_URL', 'http://127.0.0.1:1'):
            self.assertEqual(self.cli()[1]['class'], 'network')
        (self.root / 'prompt.md').unlink()
        self.assertEqual(self.cli()[0], 2)
        self.assertEqual(self.requests, [])

    def test_file_key_redacted_in_errors_success_and_resolver_exception(self):
        os.environ.pop('COUNCIL_GEMINI_API_KEY')
        path = self.root / 'synthetic-key.txt'
        path.write_text(KEY)
        path.chmod(0o600)
        self.config('key_file: ' + path.as_posix(), local=True)
        type(self).status = 403
        type(self).message = 'server echoed ' + KEY
        self.assertEqual(self.cli()[1]['class'], 'auth')
        type(self).get_status = 403
        with self.assertRaises(cga.APIError) as caught:
            cga.list_models(self.root)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertLessEqual(len(str(caught.exception)), 300)
        type(self).get_status = type(self).status = 200
        self.reply['candidates'][0]['content']['parts'] = [{'text': 'echo ' + KEY}]
        self.reply['candidates'][0]['finishReason'] = KEY
        self.assertEqual(self.cli()[0], 0)
        self.assertEqual((self.root / 'reply.md').read_text(), 'echo ***')

    def test_redirect_never_forwards_key(self):
        type(self).redirect = True
        code, result, _ = self.cli()
        self.assertEqual((code, result['http']), (1, 302))
        self.assertEqual([r[1] for r in self.requests], ['/models/gemini-3.8-flash'])

    def test_timeout_override_tier_and_model_metadata(self):
        with patch.object(cga, 'request', wraps=cga.request) as request:
            self.cli('call', '--tier', 'free', '--timeout', '1')
            self.assertEqual(self.requests, [])  # no ack, no call
            self.config('free_tier_ack: true', local=True)
            self.cli('call', '--tier', 'free')
            self.assertEqual(request.call_args_list[-1].args[2], 1200)
            self.cli('call', '--timeout', '1')
            self.assertEqual(request.call_args_list[-1].args[2], 1)
        with patch.object(cga, 'request', return_value={}):
            self.assertEqual(self.cli()[1]['class'], 'other')

    def test_explicit_small_output_cap(self):
        _, result, _ = self.cli('call', '--max-output', '100')
        self.assertEqual(result['max_output'], 100)

    def test_shipped_config_and_isolated_entrypoint(self):
        import subprocess
        import sys
        script = Path(cga.__file__).resolve()
        result = subprocess.run([sys.executable, '-I', str(script), 'config'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config['gemini_transport'], ['agy', 'cursor'])
        self.assertFalse(config['gemini_api']['enabled'])
        self.assertEqual(config['gemini_api']['timeout_sec'], 1200)
        result = subprocess.run([sys.executable, '-I', str(script), 'call', '--model', 'gemini-3.8-flash',
                                 '--prompt-file', str(self.root / 'prompt.md'), '--out', str(self.root / 'reply.md')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'refused')
        self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
