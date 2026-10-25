#!/usr/bin/env python3
"""One cross-platform Council dispatch, preflight or logged skip. Stdlib, Python 3.10+."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import council_gemini_api as api
import council_parse as cp
from council_prompt import POINTER
from council_resolve import classify_text

ROOT = Path(__file__).resolve().parent.parent
REWRITE_TAIL = ('\n\nDo not use any tools or read any files. Output only the rewritten chunk '
                'followed by the final REWRITE_END line.')


class SeatError(Exception):
    def __init__(self, category, detail, code=1):
        super().__init__(detail)
        self.category, self.code = category, code


def resolve_launcher(transport, which=shutil.which, platform=sys.platform, env=os.environ):
    """Resolve only safe launchers; injectable discovery never launches a process."""
    def find(name):
        path = which(name)
        return path if path and Path(path).suffix.lower() not in ('.bat', '.cmd') else None

    def powershell(path):
        runtime = find('pwsh') or find('powershell')
        if not runtime:
            raise SeatError('ps-runtime', 'PowerShell runtime not found')
        return [runtime, '-NoProfile', '-NonInteractive', '-File', path], 'powershell'

    win = platform == 'win32'
    path = None
    if transport == 'agy':
        path = find('agy')
        if not path and win and env.get('LOCALAPPDATA'):
            candidate = Path(env['LOCALAPPDATA']) / 'agy' / 'bin' / 'agy.exe'
            path = str(candidate) if candidate.is_file() else None
    elif transport == 'cursor':
        path = find('cursor-agent.exe') if win else find('cursor-agent')
        if not path and win:
            path = find('cursor-agent.ps1')
    elif transport == 'codex':
        path = find('codex.exe') if win else find('codex')
        if not path and win:
            script = Path(env.get('APPDATA', '')) / 'npm/node_modules/@openai/codex/bin/codex.js'
            node = find('node')
            if env.get('APPDATA') and script.is_file() and node:
                return [node, str(script)], 'node'
            path = find('codex.ps1')
    elif transport == 'grok':
        path = find('agent')
    if not path:
        raise SeatError('not-found', f'{transport} launcher not found')
    if Path(path).suffix.lower() == '.ps1':
        if not win:
            raise SeatError('not-found', 'PowerShell launcher requires Windows')
        return powershell(path)
    return [path], 'native'


def execute(argv, cwd, env, timeout, out_path, err_path):
    """File-backed output and closed stdin; hard timeout includes descendants."""
    start = time.monotonic()
    options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == 'win32'
               else {'start_new_session': True})
    with open(out_path, 'wb') as out, open(err_path, 'wb') as err:
        with subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              stdout=out, stderr=err, **options) as process:
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                if sys.platform == 'win32':
                    killer = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/taskkill.exe')
                    try:
                        subprocess.run([killer, '/F', '/T', '/PID', str(process.pid)], timeout=10,
                                       stdin=subprocess.DEVNULL, stdout=out, stderr=err, check=False)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                    finally:
                        # A restricted host can deny taskkill. Never wait indefinitely
                        # on the direct child after the hard deadline in that case.
                        if process.poll() is None:
                            process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    # The group can outlive its leader; always send KILL after grace.
                    time.sleep(2)
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()
            return process.returncode, timed_out, round(time.monotonic() - start, 3)


def read(path):
    return Path(path).read_text(encoding='utf-8-sig', errors='replace') if Path(path).exists() else ''


def write(path, text, key):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    api.atomic_write(path, api.redact(str(text).replace('\r\n', '\n').replace('\r', '\n'), key))


def object_json(text):
    for candidate in [text] + list(reversed(text.splitlines())):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
    return None


def cli_timeout(root):
    for name in ('config.local.yaml', 'config.yaml'):
        match = re.search(r'^cli_timeout_sec:\s*(\d+)\s*(#.*)?$', read(Path(root) / name), re.M)
        if match:
            value = int(match[1])
            if not 30 <= value <= 3600:
                raise SeatError('usage', 'timeout must be 30..3600 seconds', 2)
            return value
    return 600


def build_argv(a, prefix, prompt, cwd, last):
    dirs = [str(a.root), str(a.run)] + a.add_dir
    additions = [item for directory in dirs for item in ('--add-dir', directory)]
    if a.transport == 'agy':
        return prefix + (additions if a.delivery == 'pointer' else []) + [
            '--model', a.model, '--output-format', 'json', '-p', prompt]
    if a.transport == 'cursor':
        return prefix + ['-p', prompt, '--model', a.model, '--output-format', 'json',
                         '--mode', 'ask', '--trust', '--workspace', str(cwd)]
    if a.transport == 'codex':
        return (prefix + ['exec', '-s', 'read-only', '--ephemeral', '--ignore-user-config']
                + (['-c', 'windows.sandbox="unelevated"'] if sys.platform == 'win32' else [])
                + ['--skip-git-repo-check', '-C', str(a.root)] + additions + ['-m', a.model]
                + (['-c', f'model_reasoning_effort="{a.effort}"'] if a.effort else [])
                + ['-o', str(last), prompt])
    return prefix + ['-p', prompt, '--model', a.model, '--permission-mode', 'plan',
                     '--output-format', 'json', '--cwd', str(a.run), '--max-turns', '12']


def parser():
    p = api.Parser(prog='council_seat', allow_abbrev=False)
    subs = p.add_subparsers(dest='command', required=True)
    for command in ('run', 'preflight', 'skip'):
        s = subs.add_parser(command, allow_abbrev=False)
        s.add_argument('--run', required=True)
        if command == 'skip':
            s.add_argument('--reason', required=True)
            s.add_argument('--detail', default='')
        else:
            s.add_argument('--transport', required=True, choices=('agy', 'cursor', 'codex', 'grok', 'api'))
            s.add_argument('--root', default=str(ROOT))
            s.add_argument('--model', required=command == 'run')
            s.add_argument('--timeout', type=int)
        if command != 'preflight':
            s.add_argument('--seat', required=True, type=int)
            s.add_argument('--label', required=True)
        if command == 'run':
            s.add_argument('--prompt-file', required=True)
            s.add_argument('--reply', required=True)
            s.add_argument('--delivery', choices=('inline', 'pointer'))
            s.add_argument('--kind', choices=('review', 'rewrite'), default='review')
            s.add_argument('--effort')
            s.add_argument('--add-dir', action='append', default=[])
            s.add_argument('--stem')
            s.add_argument('--cwd')
    return p


def dispatch(a, key):
    preflight = a.command == 'preflight'
    stem = 'cli-preflight' if preflight else a.stem or f'seat-{a.seat}'
    # Stems are relative output names, including rewrite/chunk-N, never escape run.
    base = (a.run / stem).resolve()
    if not base.is_relative_to(a.run) or base == a.run:
        raise SeatError('usage', 'stem must stay within run directory', 2)
    base.parent.mkdir(parents=True, exist_ok=True)
    out, err, meta_path, last = [Path(str(base) + suffix) for suffix in
                               ('-stdout.txt', '-stderr.txt', '-argv-meta.txt', '-last.txt')]
    result = dict(status='failed', **{'class': 'other'}, transport=a.transport,
                  seat=None if preflight else a.seat, model=a.model, exit=None, timed_out=False,
                  seconds=0, usage={}, reply=None, free_tier_warning=False, detail='')
    meta = dict(argv=[], launcher=None, cwd_not_empty=False, msystem_stripped=False)
    prompt, prompt_body, version = '', '', None
    stage = None

    def safe(text):
        text = api.redact(text, key)
        for value in sorted({prompt, prompt_body}, key=len, reverse=True):
            if len(value) >= 16:
                text = text.replace(value, '<prompt>')
                # JSON-escaped copies can occur in diagnostics and usage fields.
                text = text.replace(json.dumps(value)[1:-1], '<prompt>')
        return text

    def detail(text):
        return ' '.join(safe(str(text)).replace(str(Path.home()), '~').split())[:160] or '-'

    def clean(value):
        # Redact values before JSON encoding so a quote/newline in the prompt
        # cannot corrupt JSON syntax when it is removed from diagnostics.
        if isinstance(value, str):
            return safe(value)
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    try:
        if preflight and a.transport != 'api' and not a.model:
            raise SeatError('usage', '--model is required for CLI preflight', 2)
        if a.timeout is not None and not 30 <= a.timeout <= 3600:
            raise SeatError('usage', 'timeout must be 30..3600 seconds', 2)
        if not preflight:
            reply = Path(a.reply).resolve()
            if not reply.is_relative_to(a.run) or reply == a.run:
                raise SeatError('usage', 'reply must stay within run directory', 2)
            a.reply = reply
        cwd = Path(getattr(a, 'cwd', None) or a.run.parent / '_cwd').resolve()
        cwd.mkdir(parents=True, exist_ok=True)
        meta.update(cwd=str(cwd), cwd_not_empty=any(cwd.iterdir()))
        env = os.environ.copy()
        if a.transport != 'api':
            env.pop('COUNCIL_GEMINI_API_KEY', None)
        if a.transport == 'cursor' and sys.platform == 'win32':
            meta['msystem_stripped'] = 'MSYSTEM' in env
            env.pop('MSYSTEM', None)
        if preflight:
            write(a.run / 'cli-preflight.txt', 'PREFLIGHT-OK', key)
            prompt = 'Reply with exactly: PREFLIGHT-OK'
            if a.transport in ('grok', 'codex'):
                prompt = f'Read {a.run / "cli-preflight.txt"} and reply with its contents.'
            if a.transport == 'codex':
                probe = a.root / '.council-write-probe'
                if probe.exists():
                    raise SeatError('codex-write-deny', 'write probe already exists; left untouched')
                prompt += f' Try to create {probe}. Answer WRITE-DENIED or WRITE-CREATED.'
            a.delivery, a.add_dir, a.effort = 'inline', [], None
        elif a.transport != 'api':
            if a.transport in ('codex', 'grok') and a.delivery == 'inline':
                raise SeatError('usage', 'transport requires pointer delivery', 2)
            a.delivery = a.delivery or ('pointer' if a.transport in ('codex', 'grok') else 'inline')
            prompt_body = Path(a.prompt_file).read_text(encoding='utf-8-sig')
            prompt = POINTER.format(f=str(Path(a.prompt_file).resolve())) if a.delivery == 'pointer' else prompt_body
            if a.kind == 'rewrite':
                prompt += REWRITE_TAIL
        if a.transport == 'api':
            timeout = (a.timeout or api.read_config(a.root)['gemini_api']['timeout_sec']) + 120
            argv = [sys.executable, '-I', str(a.root / 'scripts/council_gemini_api.py'),
                    'preflight' if preflight else 'call', '--root', str(a.root)]
            meta['launcher'] = 'python'
            if not preflight:
                prompt_body = Path(a.prompt_file).read_text(encoding='utf-8-sig')
                prompt_file = str(Path(a.prompt_file).resolve())
                if a.kind == 'rewrite':
                    prompt_file = str(base) + '-api-prompt.md'
                    write(prompt_file, prompt_body + REWRITE_TAIL, key)
                fd, stage_name = tempfile.mkstemp(dir=base.parent, prefix='.seat-api-', suffix='.part')
                os.close(fd)
                stage = Path(stage_name)
                argv += ['--model', a.model, '--prompt-file', prompt_file, '--out', str(stage), '--kind', a.kind]
                for name in ('effort', 'timeout'):
                    if getattr(a, name) is not None:
                        argv += ['--' + name, str(getattr(a, name))]
            meta['argv'] = argv
        else:
            timeout = a.timeout or cli_timeout(a.root)
            prefix, meta['launcher'] = resolve_launcher(a.transport)
            if (a.transport == 'cursor' and sys.platform == 'win32'
                    and meta['launcher'] == 'powershell'
                    and Path(prefix[0]).stem.lower() == 'powershell'):
                result['warning'] = ('cursor-ps51: Windows PowerShell 5.1 may garble embedded '
                                     'double quotes in node-based launchers; install pwsh')
            argv = build_argv(a, prefix, prompt, cwd, last)
            fingerprint = f'<prompt: {len(prompt)} chars sha256:{hashlib.sha256(prompt.encode()).hexdigest()[:12]}>'
            meta['argv'] = [fingerprint if value == prompt else value for value in argv]
            if sys.platform == 'win32' and len(subprocess.list2cmdline(argv)) > 30000:
                raise SeatError('argv-too-long', 'Windows argv exceeds 30000 characters; use pointer delivery', 2)
            if a.transport == 'codex':
                last.unlink(missing_ok=True)
            if preflight:
                try:
                    version_exit, version_timeout, _ = execute(prefix + ['--version'], cwd, env, 10, out, err)
                    if version_exit == 0 and not version_timeout:
                        version = detail(read(out))
                except OSError:
                    pass
        meta['timeout'] = timeout
        launch = time.time()
        try:
            result['exit'], result['timed_out'], result['seconds'] = execute(argv, cwd, env, timeout, out, err)
        finally:
            for path in (out, err):
                if path.exists():
                    write(path, safe(read(path)) if path == err else read(path), key)
        stdout, stderr = read(out), read(err)
        data = object_json(stdout)
        result['free_tier_warning'] = api.FREE_WARNING in stderr
        if result['timed_out']:
            raise SeatError('timeout', 'hard timeout; process tree killed')
        if re.search(r'permission check failed|user denied permission', stderr, re.I):
            raise SeatError('permission-gate', stderr)
        if a.transport == 'cursor':
            diagnostics = stderr
            if data is None or data.get('is_error'):
                diagnostics += '\n' + stdout
            if re.search(r'Hook blocked|\$OutputEncoding', diagnostics, re.I):
                raise SeatError('cursor-hook-bug', diagnostics)
        if a.transport == 'api':
            if data is None:
                raise SeatError('unusable-stdout', 'API summary is not a JSON object')
            meta['api_summary'] = data
            result['usage'] = data.get('usage', {})
            if data.get('status') != 'ok':
                result['status'] = 'refused' if data.get('status') == 'refused' else 'failed'
                raise SeatError(str(data.get('class', 'other')), data.get('detail') or 'API call failed',
                                2 if data.get('status') == 'refused' or data.get('class') == 'config' else 1)
            text = read(stage) if stage else 'PREFLIGHT-OK'
        else:
            diagnostics = '\n'.join(stderr.splitlines()[-20:]) if a.transport == 'codex' and result['exit'] != 0 else stderr
            category = classify_text(diagnostics)
            text = ''
            if a.transport == 'codex':
                text = read(last) if last.exists() and last.stat().st_mtime >= launch else ''
                if last.exists():
                    write(last, read(last), key)
            if result['exit'] != 0 or (a.transport == 'codex' and not text.strip() and category == 'model-rejected'):
                failure_detail = ('\n'.join(reversed(diagnostics.splitlines()))
                                  if a.transport == 'codex' and result['exit'] != 0 else diagnostics)
                raise SeatError(category if category != 'other' else 'exit-nonzero', failure_detail or 'CLI exited nonzero')
            if a.transport != 'codex':
                if data is None:
                    raise SeatError('unusable-stdout' if stdout.strip() else 'empty',
                                    'stdout is not a JSON object' if stdout.strip() else 'no reply text')
                text = data.get({'agy': 'response', 'cursor': 'result', 'grok': 'text'}[a.transport], '')
                result['usage'] = data.get('usage', {})
                if a.transport == 'cursor' and data.get('is_error'):
                    raise SeatError(classify_text(str(text)), str(text))
        if preflight and a.transport == 'codex' and probe.exists():
            probe.unlink()
            raise SeatError('codex-write-deny', 'read-only probe created a file')
        if not isinstance(text, str) or not text.strip():
            raise SeatError('empty', 'no reply text')
        if preflight and 'PREFLIGHT-OK' not in text:
            raise SeatError('unusable-stdout', 'preflight reply did not contain PREFLIGHT-OK')
        if result['exit'] != 0:
            raise SeatError('exit-nonzero', 'subprocess exited nonzero')
        if not preflight:
            write(a.reply, text, key)
            result['reply'] = str(Path(a.reply).resolve())
        result.update(status='ok', **{'class': 'ok'})
        code = 0
    except (SeatError, api.APIError, OSError, ValueError) as exc:
        category = getattr(exc, 'category', 'io')
        result.update(**{'class': category}, detail=detail(str(exc)))
        code = getattr(exc, 'code', 2)
        if category in ('argv-too-long', 'usage'):
            result['status'] = 'refused'
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)
        # Include codex diagnostics even on timeout or classification failure.
        if a.transport == 'codex' and last.exists():
            write(last, read(last), key)
        if preflight and a.transport == 'codex' and 'probe' in locals() and probe.exists():
            # A pre-existing probe belongs to the caller and is never removed.
            if 'launch' in locals():
                probe.unlink()
                result.update(status='failed', **{'class': 'codex-write-deny'}, detail='read-only probe created a file')
                code = 1
    result['detail'] = detail(result['detail'])
    meta.update(result, version=version)
    write(meta_path, json.dumps(clean(meta), ensure_ascii=False) + '\n', key)
    if result['status'] != 'ok' and result['class'] != 'usage':
        if preflight:
            name = 'agent' if a.transport == 'grok' else a.transport
            line = f'preflight ({a.transport}): cli-preflight-{name} ({result["class"]}: {result["detail"]})'
        else:
            token = 'api-refused' if a.transport == 'api' and (result['status'] == 'refused' or result['class'] == 'config') else f'{a.transport}-{result["class"]}'
            line = f'seat {a.seat} ({a.label}): {token} [{a.transport} {a.model}] ({result["detail"]})'
        cp.log_failure(str(a.run), safe(' '.join(line.split())))
    if preflight:
        result = {name: result[name] for name in ('status', 'class', 'transport', 'seconds', 'detail', 'warning') if name in result}
        result.update(version=version, launcher=meta['launcher'])
    print(api.redact(json.dumps(clean(result), ensure_ascii=False), key))
    return code


def main(argv=None):
    key = os.environ.get('COUNCIL_GEMINI_API_KEY', '')
    if not api.KEY_PATTERN.fullmatch(key):
        key = ''
    try:
        a = parser().parse_args(argv)
        a.run = Path(a.run).resolve()
        if not a.run.is_dir():
            raise SeatError('io', 'run directory does not exist', 2)
        if a.command == 'skip':
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', a.reason):
                raise SeatError('usage', 'invalid skip reason', 2)
            detail = ' '.join(api.redact(a.detail, key).replace(str(Path.home()), '~').split())[:160] or '-'
            label = ' '.join(api.redact(a.label, key).split())
            cp.log_failure(str(a.run), f'seat {a.seat} ({label}): {a.reason} ({detail})')
            print('{"status": "skipped"}')
            return 0
        a.root = Path(a.root).resolve()
        return dispatch(a, key)
    except (SeatError, api.APIError, OSError, ValueError) as exc:
        # Do not echo argv or exception paths: they can contain prompt text or credentials.
        print(api.redact(json.dumps(dict(status='refused', **{'class': getattr(exc, 'category', 'io')},
                                        detail='invalid arguments or inaccessible files')), key))
        return 2


if __name__ == '__main__':
    sys.exit(main())
