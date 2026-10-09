"""Citation templates shared by AviUtl and editable PPTX export."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import re
import zlib

class CitationError(ValueError):
    pass

COMMAND = re.compile(r"\\(cite|ref)\{([^{}]*)\}|\\bibliography(?:\{\})?(?![A-Za-z0-9_{])")
MARKER = re.compile(r"\A<\?--epe-cite-v1:([A-Za-z0-9_-]+)\n\?>")
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")

def has_commands(text):
    return bool(re.search(r"\\(?:cite|ref|bibliography)\b", text))

def digest(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def unpack(text, verify=True):
    match = MARKER.match(text.replace('\r\n', '\n'))
    if not match:
        if text.startswith('<?--epe-cite-'):
            raise CitationError('引用データが壊れています。引用管理画面で元の文章を入れ直してください。')
        return text, {}, text
    normalized = text.replace('\r\n', '\n')
    try:
        raw = base64.urlsafe_b64decode(match[1] + '=' * (-len(match[1]) % 4))
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(raw, 65537)
        if len(decoded) > 65536 or not decoder.eof or decoder.unused_data:
            raise ValueError('invalid compressed payload')
        data = json.loads(decoded)
        template, entries = data['template'], data['entries']
        rendered = normalized[match.end():]
        if not isinstance(template, str) or not isinstance(entries, dict):
            raise ValueError('invalid payload')
        validate_entries(entries)
        if verify and data['hash'] != digest(rendered):
            raise CitationError('更新済みの文章が直接編集されています。引用管理画面でテンプレートを編集してください。')
        return template, entries, rendered
    except CitationError:
        raise
    except (ValueError, KeyError, TypeError, zlib.error) as exc:
        raise CitationError('引用データを読み取れません。') from exc

def validate_entries(entries):
    if not isinstance(entries, dict):
        raise CitationError('文献データはキーと書誌情報のJSONオブジェクトにしてください。')
    for key, value in entries.items():
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise CitationError(f'文献キーが不正です: {key!r}')
        if not isinstance(value, str) or not value.strip():
            raise CitationError(f'書誌情報が空です: {key}')
        if '<?' in value or '?>' in value or '\x00' in value:
            raise CitationError(f'書誌情報にスクリプトやNULは使用できません: {key}')
    return entries

def load_entries(path):
    return validate_entries(json.loads(Path(path).read_text(encoding='utf-8-sig')))

def commands(template):
    matches = list(COMMAND.finditer(template))
    remainder = COMMAND.sub('', template)
    if has_commands(remainder):
        raise CitationError('引用の書式を確認してください: \\cite{key} / \\ref{key} / \\bibliography')
    for match in matches:
        if match[1] is None:
            yield match, 'bibliography', []
        else:
            keys = [key.strip() for key in match[2].split(',')]
            if not keys or any(not KEY.fullmatch(key) for key in keys):
                raise CitationError(f'引用キーが不正です: {match[0]}')
            yield match, match[1], list(dict.fromkeys(keys))

def resolve(rows, entries=None, defaults=None):
    """Rows are (identity, start, layer, text); ties use identity then text order."""
    ordered = sorted(rows, key=lambda row: (row[1], row[2], row[0]))
    catalog, templates = {}, {}
    for identity, start, layer, text in ordered:
        template, embedded, _ = unpack(text)
        for key, value in embedded.items():
            if key in catalog and catalog[key] != value and (not entries or key not in entries):
                raise CitationError(f'同じキーの書誌情報が一致しません: {key}')
            catalog[key] = value
        templates[identity] = template
    if defaults is not None:
        catalog = {**validate_entries(defaults), **catalog}
    if entries is not None:
        catalog.update(validate_entries(entries))
    used, refs = [], []
    for identity, start, layer, text in ordered:
        for match, kind, keys in commands(templates[identity]):
            for key in keys:
                if key not in catalog:
                    raise CitationError(f'未登録の論文キーです: {key}（オブジェクト {identity}）')
                target = used if kind == 'cite' else refs
                if key not in target:
                    target.append(key)
    used += [key for key in refs if key not in used]
    numbers = {key: index + 1 for index, key in enumerate(used)}
    results = {}
    for identity, start, layer, text in ordered:
        template = templates[identity]
        def replace(match):
            kind, keys = next((kind, keys) for m, kind, keys in commands(match[0]))
            if kind == 'cite':
                return '[' + ', '.join(str(numbers[key]) for key in keys) + ']'
            if kind == 'bibliography':
                keys = used
            return '\n'.join(f'[{numbers[key]}] {catalog[key]}' for key in keys)
        results[identity] = COMMAND.sub(replace, template)
    return results, numbers, catalog, templates

def pack(template, rendered, catalog):
    template = template.replace("\r\n", "\n").replace("\r", "\n")
    rendered = rendered.replace("\r\n", "\n").replace("\r", "\n")
    if '<?' in template or '?>' in template or '\x00' in template:
        raise CitationError('引用テンプレート内のLuaスクリプトには対応していません。')
    keys = {key for _, _, keys in commands(template) for key in keys}
    if any(kind == 'bibliography' for _, kind, _ in commands(template)):
        keys = set(catalog)
    data = {'template': template, 'entries': {key: catalog[key] for key in sorted(keys)},
            'hash': digest(rendered)}
    payload = base64.urlsafe_b64encode(zlib.compress(json.dumps(data, ensure_ascii=False,
        separators=(',', ':')).encode('utf-8'), 9)).decode('ascii').rstrip('=')
    result = '<?--epe-cite-v1:' + payload + '\n?>' + rendered
    if len(result.encode('utf-16-le')) // 2 > 1023:
        raise CitationError('引用データを含めるとAviUtlの1023文字制限を超えます。文章や文献一覧を複数のテキストに分けてください。')
    return result
