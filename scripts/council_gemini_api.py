#!/usr/bin/env python3
"""Opt-in Gemini Developer API transport. Python 3.10+, standard library only.

Run with python -I scripts/council_gemini_api.py config|preflight|models|call.
Keys are loaded locally, sent only as a header, and never accepted in argv.
Model fallback belongs to the harness; call retries 503 requests here (see core.md).
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import random
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'
FREE_WARNING = ('council: Gemini API free tier - Google may use prompts and replies to improve its products '
                'and humans may review them.')
DEFAULTS = dict(enabled=False, tier='free', free_tier_ack=False, thinking_level='high',
                max_output_review=32768, max_output_rewrite=65536, max_model_steps=2,
                unavailable_retry_wait_sec=20, unavailable_retries=1)
SLEEP = time.sleep
JITTER = lambda: random.uniform(0, 2)
KEY_PATTERN = re.compile(r'[A-Za-z0-9_.\-]{20,}\Z')  # newer Google keys contain '.'
SLUG_PATTERN = re.compile(r'[a-z0-9][a-z0-9.\-]*\Z')
ROOT = Path(__file__).resolve().parent.parent


def redact(text, key):
    return str(text).replace(key, '***') if key else str(text)


class APIError(Exception):
    def __init__(self, detail, category='config', http=None, refused=False):
        super().__init__(detail)
        self.category, self.http, self.refused = category, http, refused


def uncomment(line):
    """Remove YAML comments outside quoted scalars (no general YAML evaluation)."""
    quote = None
    escaped = False
    for i, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == '\\' and quote == '"':
            escaped = True
        elif char in ('"', "'"):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == '#' and quote is None:
            return line[:i].rstrip()
    return line.rstrip()


def scalar(value, key):
    value = value.strip()
    if value.startswith(('"', "'")):
        if len(value) < 2 or value[-1] != value[0]:
            raise APIError(f'{key}: invalid quoted scalar')
        if value[0] == '"':
            try:
                return json.loads(value)
            except ValueError:
                raise APIError(f'{key}: invalid quoted scalar') from None
        return value[1:-1].replace("''", "'")
    if value in ('true', 'false'):
        return value == 'true'
    if re.fullmatch(r'-?\d+', value):
        return int(value)
    if not value or value.startswith(('[', '{', '|', '>')) or value in ('null', '~'):
        raise APIError(f'{key}: expected a scalar')
    return value


def parse_config(path):
    """Read only gemini_transport and gemini_api; ignore unrelated YAML sections."""
    result = {}
    if not path.exists():
        return result
    active = None
    seen = set()
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line = uncomment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(' '))
        if indent == 0:
            active = None
            name, sep, value = line.partition(':')
            if name not in ('gemini_transport', 'gemini_api'):
                continue
            if not sep or name in seen:
                raise APIError(f'{name}: duplicate or malformed key')
            seen.add(name)
            value = value.strip()
            if name == 'gemini_api':
                if value and value != '{}':
                    raise APIError('gemini_api: expected a block mapping')
                result[name] = {}
                active = name if not value else None
            elif value:
                if not (value.startswith('[') and value.endswith(']')):
                    raise APIError('gemini_transport: expected a list')
                result[name] = [scalar(v, name) for v in value[1:-1].split(',')] if value[1:-1].strip() else []
            else:
                result[name] = []
                active = name
        elif active == 'gemini_transport':
            match = re.fullmatch(r'\s+-\s+(.+)', line)
            if not match:
                raise APIError('gemini_transport: expected list entries')
            result[active].append(scalar(match[1], active))
        elif active == 'gemini_api':
            match = re.fullmatch(r'  ([a-zA-Z_][a-zA-Z_0-9]*):\s*(.*)', line)
            if not match:
                raise APIError('gemini_api: expected scalar keys indented two spaces')
            name, value = match.groups()
            if name in result[active]:
                raise APIError(f'gemini_api.{name}: duplicate key')
            result[active][name] = scalar(value, f'gemini_api.{name}')
    return result


def validate_api(api):
    for key in api:
        if key not in {*DEFAULTS, 'timeout_sec', 'key_file'}:
            raise APIError(f'gemini_api.{key}: unknown key')
    for key in ('enabled', 'free_tier_ack'):
        if type(api[key]) is not bool:
            raise APIError(f'gemini_api.{key}: expected literal true or false')
    for key, values in [('tier', ('free', 'paid')), ('thinking_level', ('low', 'medium', 'high'))]:
        if api[key] not in values:
            raise APIError(f'gemini_api.{key}: invalid value')
    for key, lo, hi in [('timeout_sec', 30, 3600), ('max_output_review', 1024, 65536),
                        ('max_output_rewrite', 1024, 65536), ('max_model_steps', 0, 5),
                        ('unavailable_retry_wait_sec', 0, 120), ('unavailable_retries', 0, 3)]:
        if key in api and (type(api[key]) is not int or not lo <= api[key] <= hi):
            raise APIError(f'gemini_api.{key}: expected integer {lo}..{hi}')
    if 'key_file' in api and (not isinstance(api['key_file'], str) or not api['key_file'].strip()):
        raise APIError('gemini_api.key_file: expected a path string')


def read_config(root=ROOT):
    root = Path(root).resolve()
    api, transports = dict(DEFAULTS), ['api', 'agy']
    for name in ('config.yaml', 'config.local.yaml'):
        parsed = parse_config(root / name)
        # Validate each file too: an override must not hide an invalid shared value.
        candidate = {**api, **parsed.get('gemini_api', {})}
        validate_api(candidate)
        api = candidate
        if 'gemini_transport' in parsed:
            transports = parsed['gemini_transport']
            if any(t in ('cursor', 'cursor_cli', 'task') for t in transports):
                raise APIError("gemini_transport: Cursor transports are no longer valid; Gemini is never routed through Cursor (see config.migrations.yaml version 2)")
            if (not transports or any(type(t) is not str or t not in ('agy', 'api') for t in transports)
                    or len(set(transports)) != len(transports)):
                raise APIError('gemini_transport: expected non-empty, unique api|agy list')
    api.setdefault('timeout_sec', 1200 if api['tier'] == 'free' else 300)
    path = Path(api.get('key_file', '~/.council/gemini.key')).expanduser()
    if not path.is_absolute():
        path = root / path
    api['key_file'] = str(path)
    source = 'env' if os.environ.get('COUNCIL_GEMINI_API_KEY') else ('file' if path.is_file() else 'missing')
    return {'gemini_transport': transports, 'gemini_api': api, 'key_source': source, 'key_file': str(path),
            'outer_timeout_sec': outer_timeout(api)}


def outer_timeout(api, timeout=None):
    retries = api['unavailable_retries']
    return ((timeout or api['timeout_sec']) * (1 + retries)
            + retries * (api['unavailable_retry_wait_sec'] + 2) + 120)


def load_key(config):
    key = os.environ.get('COUNCIL_GEMINI_API_KEY')
    if key:
        if not KEY_PATTERN.fullmatch(key):
            raise APIError('COUNCIL_GEMINI_API_KEY has an invalid format')
        return key
    path = Path(config['key_file'])
    try:
        if os.name != 'nt' and path.stat().st_mode & 0o077:
            raise APIError(f'key file permissions must be 600: {path}')
        raw = path.read_bytes()
    except OSError:
        raise APIError(f'key file is missing or unreadable: {path}') from None
    try:
        key = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig').strip()
    except UnicodeError:
        raise APIError(f'key file has an invalid format: {path}') from None
    if not KEY_PATTERN.fullmatch(key):
        raise APIError(f'key file has an invalid format: {path}')
    return key


def gate(config):
    api = config['gemini_api']
    if not api['enabled']:
        raise APIError('gemini_api.enabled is false', refused=True)
    if api['tier'] == 'free' and not api['free_tier_ack']:
        raise APIError('gemini_api.free_tier_ack must be true for the free tier', refused=True)


def ready(config, warn=True):
    gate(config)
    key = load_key(config)
    if warn and config['gemini_api']['tier'] == 'free':
        print(FREE_WARNING, file=sys.stderr)
    return key


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # Never forward a credential-bearing request to a redirect target.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_class(code, detail):
    if code in (401, 403):
        return 'auth'
    if code == 429:
        return 'quota'
    if code == 503:
        return 'unavailable'
    if code == 404 or (code == 400 and re.search(
            r'model[^\n]{0,100}(not supported|unsupported|not found|does not exist)|unsupported model|unknown model', detail, re.I)):
        return 'model-rejected'
    if code == 504:
        return 'timeout'
    return 'network' if code in (500, 502) else 'other'


def request(path, key, timeout, body=None):
    headers = {'x-goog-api-key': key, 'Content-Type': 'application/json'}
    req = urllib.request.Request(BASE_URL + path, headers=headers,
                                 data=json.dumps(body).encode('utf-8') if body is not None else None)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as response:
            data = json.load(response)
        if not isinstance(data, dict):
            raise APIError('API response is not an object', 'other', 200)
        return data
    except urllib.error.HTTPError as exc:
        with exc:
            detail = redact(exc.read().decode('utf-8', errors='replace'), key)
        raise APIError(detail[:300], http_class(exc.code, detail), exc.code) from None
    except (socket.timeout, TimeoutError):
        raise APIError('request timed out', 'timeout') from None
    except urllib.error.URLError as exc:
        category = 'timeout' if isinstance(exc.reason, (socket.timeout, TimeoutError)) else 'network'
        raise APIError(redact(str(exc.reason), key)[:300], category) from None
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise APIError(redact(str(exc), key)[:300], 'other' if isinstance(exc, ValueError) else 'network') from None


def list_models(root=ROOT):
    config = read_config(root)
    key = ready(config)
    try:
        slugs, token, seen = [], None, set()
        while True:
            query = {'pageSize': 1000}
            if token:
                query['pageToken'] = token
            data = request('/models?' + urllib.parse.urlencode(query), key, config['gemini_api']['timeout_sec'])
            for model in data.get('models', []):
                slug = model.get('name', '').removeprefix('models/')
                if ('generateContent' in model.get('supportedGenerationMethods', []) and SLUG_PATTERN.fullmatch(slug)
                        and not set(slug.split('-')) & {'latest', 'preview', 'exp', 'experimental'}):
                    slugs.append(slug)
            token = data.get('nextPageToken')
            if not token:
                return list(dict.fromkeys(slugs))
            if not isinstance(token, str) or token in seen:
                raise APIError('API returned invalid model pagination', 'other')
            seen.add(token)
    except (APIError, OSError, ValueError, TypeError, AttributeError) as exc:
        # Resolver callers receive only safe exception text, including file-sourced keys.
        raise APIError(redact(str(exc), key)[:300], getattr(exc, 'category', 'other'),
                       getattr(exc, 'http', None)) from None


def atomic_write(path, text):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.resolve().parent, prefix='.gemini-', suffix='.part')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes offending argv values, which can include a secret.
        argument = re.search(r'--[a-z][a-z-]*', message)
        raise APIError('usage: invalid or missing argument' + (': ' + argument[0] if argument else ''))


def build_parser():
    parser = Parser(prog='council_gemini_api', allow_abbrev=False)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('config', 'preflight', 'models', 'call'):
        cmd = commands.add_parser(name, allow_abbrev=False)
        cmd.add_argument('--root', default=str(ROOT))
        if name == 'call':
            cmd.add_argument('--model', required=True)
            cmd.add_argument('--prompt-file', required=True)
            cmd.add_argument('--out', required=True)
            cmd.add_argument('--kind', choices=('review', 'rewrite'), default='review')
            cmd.add_argument('--effort', choices=('low', 'medium', 'high'))
            cmd.add_argument('--max-output', type=int)
            cmd.add_argument('--timeout', type=int)
            cmd.add_argument('--tier', choices=('free', 'paid'))
    return parser


def main(argv=None):
    start, key, args = time.monotonic(), os.environ.get('COUNCIL_GEMINI_API_KEY', ''), None
    if not KEY_PATTERN.fullmatch(key):
        key = ''  # an unvalidated value must not be used as a redaction needle (would garble output)
    summary = dict(status='failed', **{'class': 'config'}, model=None, http=None,
                   usage=dict(prompt=0, thoughts=0, output=0, total=0), finish_reason=None,
                   seconds=0, tier=None, max_output=None, detail=None, unavailable_retries_used=0)
    try:
        args = build_parser().parse_args(argv)
        config = read_config(args.root)
        api = config['gemini_api']
        summary['tier'] = api['tier']
        if args.command == 'config':
            print(redact(json.dumps(config), key))
            return 0
        if args.command == 'models':
            live_key = ready(config)  # raises the same config errors list_models would
            for slug in list_models(args.root):
                print(redact(slug, live_key))
            return 0
        if args.command == 'call':
            summary['model'] = args.model
            if not SLUG_PATTERN.fullmatch(args.model):
                raise APIError('--model: invalid slug')
            if args.tier:
                api['tier'] = args.tier
                # Recompute tier default only when neither config file sets a timeout.
                explicit = any('timeout_sec' in parse_config(Path(args.root) / name).get('gemini_api', {})
                               for name in ('config.yaml', 'config.local.yaml'))
                if not explicit:
                    api['timeout_sec'] = 1200 if args.tier == 'free' else 300
            summary['tier'] = api['tier']
            if args.timeout is not None and args.timeout < 1:
                raise APIError('--timeout: expected integer >= 1')
            if args.max_output is not None and args.max_output < 1:
                raise APIError('--max-output: expected integer >= 1')
        key = ready(config)
        timeout = getattr(args, 'timeout', None) or api['timeout_sec']

        def call_request(path, body=None):
            for attempt in range(api['unavailable_retries'] + 1):
                try:
                    return request(path, key, timeout, body)
                except APIError as exc:
                    if exc.category != 'unavailable' or attempt == api['unavailable_retries']:
                        raise
                    summary['unavailable_retries_used'] += 1
                    SLEEP(api['unavailable_retry_wait_sec'] + JITTER())

        if args.command == 'preflight':
            request('/models?pageSize=1', key, timeout)
        else:
            prompt = Path(args.prompt_file).read_text(encoding='utf-8-sig')
            model = call_request('/models/' + args.model)
            limit = model.get('outputTokenLimit')
            if type(limit) is not int or limit < 1:
                raise APIError('model outputTokenLimit is missing or invalid', 'other', 200)
            maximum = min(args.max_output or api['max_output_' + args.kind], limit)
            summary['max_output'] = maximum
            data = call_request('/models/' + args.model + ':generateContent', {
                'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
                'generationConfig': {'maxOutputTokens': maximum,
                                     'thinkingConfig': {'thinkingLevel': args.effort or api['thinking_level']}}})
            summary['http'] = 200
            usage = data.get('usageMetadata') or {}
            summary['usage'] = {label: usage.get(field, 0) for label, field in [
                ('prompt', 'promptTokenCount'), ('thoughts', 'thoughtsTokenCount'),
                ('output', 'candidatesTokenCount'), ('total', 'totalTokenCount')]}
            candidates = data.get('candidates') or []
            candidate = candidates[0] if candidates else {}
            summary['finish_reason'] = candidate.get('finishReason')
            parts = (candidate.get('content') or {}).get('parts') or []
            text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
            if not text.strip():
                raise APIError('API returned no non-thought text', 'empty', 200)
            atomic_write(args.out, redact(text, key))
        summary.update(status='ok', **{'class': 'ok'}, http=200)
        code = 0
    except APIError as exc:
        summary.update(status='refused' if exc.refused else 'failed', **{'class': exc.category},
                       http=exc.http, detail=redact(str(exc), key)[:300])
        code = 2 if exc.category == 'config' else 1
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        category = 'config' if isinstance(exc, (OSError, UnicodeError)) else 'other'
        summary.update(**{'class': category}, detail=redact(str(exc), key)[:300])
        code = 2 if category == 'config' else 1
    summary['seconds'] = round(time.monotonic() - start, 3)
    print(redact(json.dumps(summary), key))
    return code


if __name__ == '__main__':
    sys.exit(main())
