"""Citation templates kept outside AviUtl's text buffer."""
from pathlib import Path
import json
import os
import tempfile
from citations import CitationError, has_commands, unpack, validate_entries

def state_path(references):
    path = Path(references)
    stem = path.stem.removesuffix('.references')
    return path.with_name(stem + '.citations.json')

def load_state(path):
    path = Path(path)
    if not path.exists(): return {'version': 1, 'scenes': {}}
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('scenes'), dict):
        raise CitationError('引用管理ファイルの形式が不正です。')
    for scene in data['scenes'].values():
        if not isinstance(scene, dict) or not isinstance(scene.get('rows'), list):
            raise CitationError('引用管理ファイルの形式が不正です。')
        validate_entries(scene.get('entries', {}))
        for row in scene['rows']:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in ('template', 'rendered')):
                raise CitationError('引用管理ファイルの文章が不正です。')
    return data

def recover(rows, state, scene=0):
    """Match unchanged visible text; ambiguous templates must never be guessed."""
    saved = state['scenes'].get(str(scene), {})
    records = saved.get('rows', [])
    templates, entries, warnings = {}, dict(saved.get('entries', {})), []
    for identity, start, layer, text in rows:
        template, embedded, _ = unpack(text, verify=False)
        entries.update(embedded)
        if text.startswith('<?--epe-cite-') or has_commands(text):
            templates[identity] = template
            continue
        candidates = {r['template'] for r in records
                      if r['rendered'].replace('\r\n','\n') == text.replace('\r\n','\n')}
        if len(candidates) > 1:
            raise CitationError(f'引用文章を特定できません: オブジェクト {identity}。引用キーを入れ直してください。')
        templates[identity] = next(iter(candidates)) if candidates else text
        if not candidates and any(r.get('index') == identity and r.get('start') == start and r.get('layer') == layer for r in records):
            warnings.append(identity)
    return templates, entries, warnings

def update_state(state, scene, rows, templates, rendered, entries):
    result = json.loads(json.dumps(state))
    result['scenes'][str(scene)] = {'entries': dict(entries), 'rows': [
        {'index': identity, 'start': start, 'layer': layer,
         'template': templates[identity], 'rendered': rendered[identity]}
        for identity, start, layer, _ in rows if has_commands(templates[identity])]}
    return result

def save_state(path, data):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                prefix='.citations-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write('\n')
            file.flush(); os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary: temporary.unlink(missing_ok=True)
