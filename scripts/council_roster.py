#!/usr/bin/env python3
"""Validate and load Council rosters. Python 3.10+, standard library only.

Run with python -I scripts/council_roster.py load|validate|check|migrations|stamp.
The pure core accepts text only; it never reads credentials or invokes transports.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile

# Isolated Python does not add this script's directory to sys.path.
try:
    from council_gemini_api import APIError, scalar, uncomment
except ModuleNotFoundError:
    _spec = importlib.util.spec_from_file_location('council_gemini_api', Path(__file__).with_name('council_gemini_api.py'))
    _api = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_api)
    APIError, scalar, uncomment = _api.APIError, _api.scalar, _api.uncomment

ROOT = Path(__file__).resolve().parent.parent
ROUTES = {
    'default': {'gemini': ['api', 'agy'], 'gpt': ['codex'], 'grok': ['cursor_cli'], 'claude': ['agent'], 'composer': []},
    'cursor': {'gemini': ['api', 'agy'], 'gpt': ['task'], 'grok': ['cursor_cli'], 'claude': ['task'], 'composer': ['task']},
}
KEYS = {'config_version', 'based_on_config_version', 'max_reviewers', 'gemini_transport', 'routes', 'seats', 'reviewers', 'cursor_reviewers'}
SEAT_KEYS = {'family', 'line', 'effort', 'role', 'label', 'pin', 'transports', 'fallback_for'}
MIGRATION_HINT = '; Gemini is never routed through Cursor (see config.migrations.yaml version 2)'


def flow(value, key):
    """Parse a flow list, splitting only commas outside quoted scalars."""
    if not (value.startswith('[') and value.endswith(']')):
        raise APIError(f'{key}: expected a list')
    body = value[1:-1]
    if not body.strip():
        return []
    entries, start, quote, escaped = [], 0, None, False
    for i, char in enumerate(body):
        if escaped:
            escaped = False
        elif char == '\\' and quote == '"':
            escaped = True
        elif char in ('"', "'"):
            quote = None if quote == char else char if quote is None else quote
        elif char == ',' and quote is None:
            entries.append(scalar(body[start:i], key))
            start = i + 1
    entries.append(scalar(body[start:], key))
    return entries


def parse_subset(text, keys, path='config'):
    """Restricted block YAML: mappings, sequences, scalars, flow lists, literals.

    Ignore unrelated top-level sections before parsing, so API settings and key
    paths are neither interpreted nor included in diagnostics.
    """
    lines, active, section = [], False, None
    for number, raw in enumerate((text or '').lstrip('\ufeff').splitlines(), 1):
        leading = raw[:len(raw) - len(raw.lstrip())]
        if '\t' in leading:
            raise APIError(f'{path}: line {number}: tabs are not allowed for indentation')
        clean = uncomment(raw)
        if not clean.strip():
            if active:
                lines.append((raw, number))
            continue
        indent = len(clean) - len(clean.lstrip(' '))
        if indent == 0 and not clean.startswith('- '):
            key, sep, _ = clean.partition(':')
            if key.strip() in keys and (not sep or key != key.strip()):
                raise APIError(f'{path}: line {number}: malformed line')
            section = key
            active = key in keys
        elif indent == 0 and clean.startswith('- ') and section not in {'seats', 'reviewers', 'cursor_reviewers', 'gemini_transport', 'migrations'}:
            raise APIError(f'{path}: line {number}: malformed line')
        if active:
            lines.append((raw, number))

    tokens = []
    i = 0
    while i < len(lines):
        raw, number = lines[i]
        clean = uncomment(raw)
        i += 1
        if not clean.strip():
            continue
        indent = len(clean) - len(clean.lstrip(' '))
        content = clean[indent:]
        literal = None
        if content.endswith(': |'):
            chunks = []
            while i < len(lines):
                line, _ = lines[i]
                if line.strip() and len(line) - len(line.lstrip(' ')) <= indent:
                    break
                if line.strip() and not line.startswith(' ' * (indent + 2)):
                    raise APIError('migrations.example: invalid literal indentation')
                chunks.append(line[indent + 2:])
                i += 1
            literal = '\n'.join(chunks) + ('\n' if chunks else '')
        tokens.append((indent, content, literal, number))

    def atom(value, key):
        return flow(value, key) if value.startswith('[') else {} if value == '{}' else scalar(value, key)

    def mapping(pos, indent, path):
        result = {}
        while pos < len(tokens) and tokens[pos][0] == indent and not tokens[pos][1].startswith('- '):
            _, content, literal, number = tokens[pos]
            key, sep, value = content.partition(':')
            if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key):
                raise APIError(f'{path_name}: line {number}: malformed line')
            full = f'{path}.{key}' if path else key
            if key in result:
                raise APIError(f'{full}: duplicate or malformed key')
            pos += 1
            value = value.strip()
            if literal is not None:
                item = literal
            elif value:
                item = atom(value, full)
            elif pos < len(tokens) and (tokens[pos][0] > indent or (tokens[pos][0] == indent and tokens[pos][1].startswith('- '))):
                item, pos = block(pos, tokens[pos][0], full)
            else:
                item = None
            result[key] = item
        return result, pos

    def block(pos, indent, path):
        if path in ('seats', 'gemini_transport') and indent not in (0, 2):
            raise APIError(f'{path}: expected list entries indented zero or two spaces')
        if path == 'routes' and indent != 2 or path.startswith('routes.') and indent != 4:
            raise APIError(f'{path}: invalid mapping indentation')
        if not tokens[pos][1].startswith('- '):
            return mapping(pos, indent, path)
        result = []
        while pos < len(tokens) and tokens[pos][0] == indent and tokens[pos][1].startswith('- '):
            _, content, literal, number = tokens[pos]
            value = content[2:]
            if re.match(r'[A-Za-z_][A-Za-z_0-9]*:', value):
                # A sequence mapping begins at the implicit dash + two indent.
                tokens[pos] = (indent + 2, value, literal, number)
                item, pos = mapping(pos, indent + 2, f'{path}[{len(result) + 1}]')
            else:
                item = atom(value, path)
                pos += 1
            result.append(item)
        return result, pos

    path_name = path
    if not tokens:
        return {}
    result, end = mapping(0, 0, '')
    if end != len(tokens):
        raise APIError('config: invalid indentation or structure')
    return result


def positive(value, key):
    if type(value) is not int or value < 1:
        raise APIError(f'{key}: expected a positive integer')
    return value


def transport_list(value, key, family, origin=None, legacy=False):
    if not isinstance(value, list) or any(type(t) is not str for t in value):
        raise APIError(f'{key}: expected a unique transport list')
    if len(set(value)) != len(value) or (legacy and not value):
        raise APIError(f'{key}: expected a unique transport list')
    allowed = ROUTES[origin][family] if origin else set(ROUTES['default'][family] + ROUTES['cursor'][family])
    for transport in value:
        if transport not in allowed:
            if family == 'gemini' and transport in ('cursor', 'cursor_cli', 'task'):
                raise APIError(f'{key}: invalid Gemini transport' + MIGRATION_HINT)
            if transport == 'cursor':
                raise APIError(f'{key}: old cursor value is invalid; use cursor_cli for Grok')
            # Do not echo arbitrary local values.
            raise APIError(f'{key}: transport is not allowed for {family}')
    return value


def parse_config(text):
    parsed = parse_subset(text, KEYS)
    for key in ('config_version', 'based_on_config_version', 'max_reviewers'):
        if key in parsed:
            positive(parsed[key], key)
    if 'seats' not in parsed and any(key in parsed for key in ('reviewers', 'cursor_reviewers')):
        raise APIError('reviewers/cursor_reviewers: migration to seats required (config.migrations.yaml version 2)')
    routes = parsed.get('routes', {})
    if not isinstance(routes, dict):
        raise APIError('routes: expected a mapping')
    for origin, families in routes.items():
        if origin not in ROUTES:
            raise APIError(f'routes.{origin}: unknown origin')
        if not isinstance(families, dict):
            raise APIError(f'routes.{origin}: expected a mapping')
        for family, transports in families.items():
            if family not in ROUTES[origin]:
                raise APIError(f'routes.{origin}.{family}: unknown family')
            transport_list(transports, f'routes.{origin}.{family}', family, origin)
    if 'gemini_transport' in parsed:
        transport_list(parsed['gemini_transport'], 'gemini_transport', 'gemini', legacy=True)
        if any('gemini' in families for families in routes.values()):
            raise APIError('gemini_transport: cannot also set routes.*.gemini in the same file')
    if 'seats' in parsed:
        validate_seats(parsed['seats'])
    return parsed


def validate_seats(seats):
    if not isinstance(seats, list):
        raise APIError('seats: expected a list')
    for i, seat in enumerate(seats, 1):
        key = f'seats[{i}]'
        if not isinstance(seat, dict):
            raise APIError(f'{key}: expected a mapping')
        for name in seat:
            if name not in SEAT_KEYS:
                raise APIError(f'{key}.{name}: unknown seat key')
        if type(seat.get('family')) is not str or seat['family'] not in ROUTES['default']:
            raise APIError(f'{key}.family: invalid family')
        for name in ('line', 'label', 'pin', 'fallback_for'):
            if (name == 'line' or name in seat) and (type(seat.get(name)) is not str or not seat[name].strip()):
                raise APIError(f'{key}.{name}: expected a non-empty string')
        if seat.get('effort', 'medium') not in ('low', 'medium', 'high', 'xhigh', 'max'):
            raise APIError(f'{key}.effort: invalid effort')
        if seat.get('role', 'reviewer') not in ('reviewer', 'breadth-auditor', 'rewriter'):
            raise APIError(f'{key}.role: invalid role')
        if 'transports' in seat:
            transport_list(seat['transports'], f'{key}.transports', seat['family'])
    targets = set()
    for i, seat in enumerate(seats):
        if 'fallback_for' not in seat:
            continue
        key = f'seats[{i + 1}].fallback_for'
        matches = [j for j, other in enumerate(seats) if other.get('label') == seat['fallback_for']]
        if len(matches) != 1 or matches[0] != i - 1 or i == 0:
            raise APIError(f'{key}: target label must be unique and immediately preceding')
        target = seats[i - 1]
        role = target.get('role', 'reviewer')
        if 'label' not in seat:
            raise APIError(f'seats[{i + 1}].label: fallback needs a label')
        if 'fallback_for' in target or role not in ('reviewer', 'breadth-auditor') or i - 1 in targets:
            raise APIError(f'{key}: invalid or repeated fallback target')
        if 'role' in seat and seat['role'] != role:
            raise APIError(f'{key}: fallback role must match target role')
        targets.add(i - 1)


def build_roster(shared_text, local_text, origin):
    """Return the effective roster without touching the filesystem."""
    if origin not in ROUTES:
        raise APIError('origin: expected cursor or default')
    shared, local = parse_config(shared_text), parse_config(local_text)
    routes = {family: list(values) for family, values in ROUTES[origin].items()}
    warnings = []
    for parsed in (shared, local):
        if 'gemini_transport' in parsed:
            routes['gemini'] = list(parsed['gemini_transport'])
            warnings.append('gemini_transport is deprecated; use routes.<origin>.gemini (config.migrations.yaml version 2)')
        routes.update(parsed.get('routes', {}).get(origin, {}))
    raw_seats = local.get('seats', shared.get('seats', []))
    maximum = local.get('max_reviewers', shared.get('max_reviewers', 7))
    seats, truncated, review_number, previous = [], [], 0, None
    for i, raw in enumerate(raw_seats, 1):
        fallback = 'fallback_for' in raw
        role = previous['role'] if fallback else raw.get('role', 'reviewer')
        if not fallback and role != 'rewriter':
            review_number += 1
        number = previous['seat'] if fallback else None if role == 'rewriter' else review_number
        requested = raw.get('transports', routes[raw['family']])
        transports = [t for t in requested if t in ROUTES[origin][raw['family']]]
        dropped = [t for t in requested if t not in transports]
        dispatch = []
        for transport in transports:
            runner = {'api': 'api', 'agy': 'agy', 'codex': 'codex', 'cursor_cli': 'cursor'}.get(transport)
            source = 'task' if transport == 'task' else runner
            dispatch.append(dict(transport=transport, runner=runner, source=source))
        seat = dict(index=i, seat=number, family=raw['family'], line=raw['line'], effort=raw.get('effort', 'medium'),
                    role=role, label=raw.get('label', f"{raw['family']} {raw['line']}"), pin=raw.get('pin'),
                    transports=transports, transports_source='seat' if 'transports' in raw else 'route', dispatch=dispatch,
                    skip=None if transports else 'no-transport',
                    skip_detail=('dropped transports for this origin: ' + ', '.join(dropped)) if dropped else
                                (None if transports else 'route has no transports for this origin'),
                    fallback_for=raw.get('fallback_for'), fallback=fallback)
        previous = seat
        if number is not None and number > maximum:
            truncated.append(seat['label'])
        else:
            seats.append(seat)
    return dict(status='ok', origin=origin, config_version=shared.get('config_version', 1), based_on=None,
                migrations=[], warnings=warnings, source='config.local.yaml' if 'seats' in local else 'config.yaml',
                max_reviewers=maximum, truncated=truncated, routes=routes, seats=seats)


def parse_migrations(text, version):
    parsed = parse_subset(text, {'migrations'})
    entries = parsed.get('migrations')
    if not isinstance(entries, list):
        raise APIError('migrations: expected a list')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'version', 'date', 'title', 'changes'}:
            raise APIError('migrations: invalid entry keys')
        v = positive(entry['version'], 'migrations.version')
        if v > version:
            continue
        if v < 2 or v in seen:
            raise APIError('migrations.version: duplicate or out of range')
        seen.add(v)
        for name in ('date', 'title'):
            if type(entry[name]) is not str or not entry[name].strip():
                raise APIError(f'migrations.{name}: expected a string')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['date']):
            raise APIError('migrations.date: expected YYYY-MM-DD')
        if not isinstance(entry['changes'], list) or not entry['changes']:
            raise APIError('migrations.changes: expected a non-empty list')
        for change in entry['changes']:
            if not isinstance(change, dict) or set(change) != {'key', 'kind', 'meaning', 'action', 'example'}:
                raise APIError('migrations.changes: invalid change keys')
            if any(type(value) is not str or not value.strip() for value in change.values()):
                raise APIError('migrations.changes: expected non-empty strings')
            if change['kind'] not in ('changed', 'removed', 'renamed', 'added'):
                raise APIError('migrations.changes.kind: invalid kind')
    if seen != set(range(2, version + 1)):
        raise APIError('migrations.version: exactly one entry required for every config version from 2')
    return sorted((entry for entry in entries if entry['version'] <= version), key=lambda entry: entry['version'])


def version_info(shared_text, local_text, repair_stamp=False):
    # Version checks must work even when a legacy roster is no longer valid.
    shared = parse_subset(shared_text, {'config_version'})
    if repair_stamp and local_text is not None:
        def repair(match):
            try:
                value = scalar(uncomment(match.group(1)), 'based_on_config_version')
            except APIError:
                value = None
            return match.group(0) if type(value) is int else 'based_on_config_version: 1'
        local_text = re.sub(r'(?m)^based_on_config_version:([^\r\n]*)', repair, local_text)
    local = parse_subset(local_text, {'based_on_config_version'})
    version = positive(shared.get('config_version', 1), 'config_version')
    based = None if local_text is None else positive(local.get('based_on_config_version', 1), 'based_on_config_version')
    if based is not None and based > version:
        raise APIError('based_on_config_version: newer than config_version')
    return version, based


def check_version(shared_text, local_text, migrations_text, repair_stamp=False):
    version, based = version_info(shared_text, local_text, repair_stamp=repair_stamp)
    entries = parse_migrations(migrations_text, version)
    return dict(config_version=version, based_on=based,
                migrations=[] if based is None else [entry for entry in entries if entry['version'] > based])


def stamp_bytes(original, version):
    text = original.decode('utf-8-sig')
    newline = '\r\n' if '\r\n' in text else '\n'
    pattern = r'(?m)^(based_on_config_version:[ \t]*)[^\r\n#]*(.*)$'
    if re.search(pattern, text):
        def replace(match):
            old = match.group(0)
            comment = old[old.index('#'):] if '#' in old else ''
            ending = '\r' if old.endswith('\r') else ''
            comment = comment.rstrip('\r')
            return match.group(1) + str(version) + (' ' + comment if comment else '') + ending
        text = re.sub(pattern, replace, text)
    else:
        text += (newline if text and not text.endswith('\n') else '') + f'based_on_config_version: {version}' + newline
    return (b'\xef\xbb\xbf' if original.startswith(b'\xef\xbb\xbf') else b'') + text.encode('utf-8')


def atomic_write(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.council-stamp-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise APIError('arguments: invalid command or option')


def main(argv=None):
    migrations = []
    try:
        parser = Parser(prog='council_roster')
        sub = parser.add_subparsers(dest='cmd', required=True)
        for command in ('load', 'validate', 'check', 'migrations', 'stamp'):
            p = sub.add_parser(command)
            p.add_argument('--root', type=Path, default=ROOT)
            if command == 'load':
                p.add_argument('--origin', required=True, choices=tuple(ROUTES))
            elif command == 'validate':
                p.add_argument('--local', type=Path)
            elif command == 'migrations':
                p.add_argument('--since', type=int)
            elif command == 'stamp':
                p.add_argument('--version', type=int)
        args = parser.parse_args(argv)
        shared = (args.root / 'config.yaml').read_text(encoding='utf-8-sig')
        local_path = args.local if args.cmd == 'validate' and args.local else args.root / 'config.local.yaml'
        local = local_path.read_bytes().decode('utf-8-sig') if local_path.exists() else None
        if args.cmd in ('load', 'validate'):
            parse_subset(shared, KEYS, path=args.root / 'config.yaml')
            parse_subset(local, KEYS, path=local_path)
        if args.cmd == 'validate':
            if args.local and local is None:
                raise APIError('local: candidate file is missing')
            result = {'status': 'ok', 'rosters': {origin: build_roster(shared, local, origin) for origin in ROUTES}}
            code = 0
        else:
            info = check_version(shared, local, (args.root / 'config.migrations.yaml').read_text(encoding='utf-8-sig'),
                                 repair_stamp=args.cmd == 'stamp')
            migrations = info['migrations']
            result = dict(status='ok', **info)
            code = 3 if migrations else 0
            if args.cmd == 'load':
                result = {**build_roster(shared, local, args.origin), **info}
            elif args.cmd == 'migrations':
                since = args.since if args.since is not None else info['based_on'] or info['config_version']
                if since < 0:
                    raise APIError('since: expected a non-negative integer')
                entries = parse_migrations((args.root / 'config.migrations.yaml').read_text(encoding='utf-8-sig'), info['config_version'])
                result['migrations'] = [entry for entry in entries if entry['version'] > since]
                code = 0
            elif args.cmd == 'stamp':
                version = info['config_version'] if args.version is None else positive(args.version, 'version')
                if version > info['config_version'] or version < (info['based_on'] or 1):
                    raise APIError('version: cannot downgrade the stamp or exceed config_version')
                original = local_path.read_bytes() if local is not None else b''
                atomic_write(local_path, stamp_bytes(original, version))
                result = dict(status='ok', config_version=info['config_version'], based_on=version)
                code = 0
        print(json.dumps(result, ensure_ascii=False))
        return code
    except APIError as exc:
        print(json.dumps(dict(status='error', detail=str(exc), migrations=migrations)))
        return 2
    except (OSError, UnicodeError):
        print(json.dumps(dict(status='error', detail='config: unable to read or write configuration', migrations=migrations)))
        return 2


if __name__ == '__main__':
    sys.exit(main())
