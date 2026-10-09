"""Citation editor opened by CitationManager.auf; no EXO export required."""
from __future__ import annotations
import argparse
import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
import json
from pathlib import Path
import struct
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from citations import CitationError, resolve, pack, unpack, has_commands, load_entries, validate_entries
from gui import create_root, cleanup_snapshot
from citation_state import state_path, load_state, recover, update_state, save_state

@dataclass
class Row:
    index: int
    filter: int
    start: int
    end: int
    layer: int
    midpoint: int
    text: str

@dataclass
class Snapshot:
    token: tuple[int, ...]
    scene: int
    rows: list[Row]

def read_snapshot(path):
    data = memoryview(Path(path).read_bytes())
    if len(data) > 8 * 1024 * 1024 or len(data) < 32:
        raise CitationError('引用データのサイズが不正です。')
    magic, version, *values = struct.unpack_from('<8I', data)
    if magic != 0x45504349 or version != 1 or values[5] > 100000:
        raise CitationError('引用データの形式が不正です。')
    rows, offset = [], 32
    try:
        for _ in range(values[5]):
            index, filter_index, start, end, layer, midpoint, length = struct.unpack_from('<6iI', data, offset)
            offset += 28
            if length > 1023 or offset + length * 2 > len(data):
                raise ValueError('bad text length')
            text = bytes(data[offset:offset + length * 2]).decode('utf-16-le')
            offset += length * 2
            if '\x00' in text or index < 0 or not 0 <= filter_index < 12:
                raise ValueError('bad row')
            rows.append(Row(index, filter_index, start, end, layer, midpoint, text))
        if offset != len(data) or len({(row.index, row.filter) for row in rows}) != len(rows):
            raise ValueError('duplicate or extra rows')
    except (ValueError, struct.error, UnicodeError) as exc:
        raise CitationError('引用データを読み取れません。') from exc
    if len({row.index for row in rows}) != len(rows):
        raise CitationError("同じオブジェクトに複数のテキストフィルタがある構成には対応していません。")
    return Snapshot(tuple(values[:4]), values[4], rows)

def make_updates(snapshot, templates, entries, restore=False):
    rows = [(row.index, row.start, row.layer, templates[row.index]) for row in snapshot.rows]
    if restore:
        rendered, numbers, catalog = {row[0]: row[3] for row in rows}, {}, {}
    else:
        rendered, numbers, catalog, _ = resolve(rows, entries)
    updates = []
    for row in snapshot.rows:
        template = templates[row.index]
        if has_commands(template) and any(tag in template for tag in ('<?', '?>', '\x00')):
            raise CitationError('引用テンプレート内のLuaスクリプトには対応していません。')
        if restore:
            replacement = template
        else:
            replacement = rendered[row.index]
        if replacement != row.text:
            if row.midpoint >= 0:
                raise CitationError(f'中間点のあるテキストは更新できません: オブジェクト {row.index}')
            if len(replacement.encode('utf-16-le')) // 2 > 1023 or '\x00' in replacement:
                raise CitationError(f'テキストが1023文字を超えています: オブジェクト {row.index}')
            updates.append((row.index, row.filter, replacement))
    return updates, rendered, numbers

def encode_updates(snapshot, updates):
    result = bytearray(struct.pack('<6I', 1, *snapshot.token, len(updates)))
    for index, filter_index, text in updates:
        encoded = text.encode('utf-16-le')
        if len(encoded) // 2 > 1023 or '\x00' in text:
            raise CitationError('AviUtlの文字数制限を超えています。')
        result += struct.pack('<3I', index, filter_index, len(encoded) // 2) + encoded
    return bytes(result)

def apply_updates(target, snapshot, updates):
    class CopyData(c.Structure):
        _fields_ = [('kind', c.c_size_t), ('size', w.DWORD), ('data', c.c_void_p)]
    user = c.WinDLL('user32', use_last_error=True)
    user.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, c.POINTER(c.c_size_t)]
    user.SendMessageTimeoutW.restype = w.LPARAM
    raw = encode_updates(snapshot, updates)
    buffer = c.create_string_buffer(raw)
    packet = CopyData(0x45504355, len(raw), c.cast(buffer, c.c_void_p))
    result = c.c_size_t()
    sent = user.SendMessageTimeoutW(target, 0x4A, 0, c.addressof(packet), 0x2, 15000, c.byref(result))
    if not sent:
        raise CitationError('AviUtlに接続できません。引用管理画面を閉じ、AviUtlのメニューから開き直してください。')
    if result.value == 2:
        raise CitationError('取得後にタイムラインが変わりました。画面を閉じて取得し直してください。')
    if result.value != 1:
        raise CitationError('AviUtlが更新を受け付けませんでした。テキストは更新されていません。')
    by_id = {(index, filter_index): text for index, filter_index, text in updates}
    for row in snapshot.rows:
        row.text = by_id.get((row.index, row.filter), row.text)

def import_entries(path, entries):
    """Replace matching keys while preserving references already used by the scene."""
    loaded = load_entries(path)
    merged = {**entries, **loaded}
    validate_entries(merged)
    entries.clear()
    entries.update(merged)
    return len(loaded)

def save_entries(path, entries):
    validate_entries(entries)
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                prefix='.references-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            json.dump(entries, file, ensure_ascii=False, indent=2)
            file.write('\n')
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)

def main(args):
    snapshot = read_snapshot(args.snapshot)
    metadata_path = state_path(args.references)
    state = load_state(metadata_path)
    rows = [(row.index, row.start, row.layer, row.text) for row in snapshot.rows]
    templates, entries, direct_edits = recover(rows, state, snapshot.scene)
    if args.references.is_file():
        entries.update(load_entries(args.references))
    root = create_root()
    root.title('AviUtl 1 — 論文の引用・脚注番号')
    root.geometry('1000x800'); root.minsize(780, 600)
    box = ttk.Frame(root, padding=14); box.pack(fill='both', expand=True)
    ttk.Label(box, text=r'本文: \cite{key}　脚注: \ref{key}　文献一覧: \bibliography',
        font=('', 12, 'bold')).pack(anchor='w', pady=(0, 6))
    ttk.Label(box, text='タイムラインの初出順に番号を付けます。並べ替え後はこの画面を開き直してください。').pack(anchor='w')
    status = tk.StringVar(value='文献を登録し、番号をプレビューしてから反映してください。')
    if direct_edits:
        status.set('直接編集を検出: ' + ', '.join(map(str, direct_edits)) + '。編集済みの文章を表示しています。必要なら引用キーを入れ直してください。')
    tabs = ttk.Notebook(box); tabs.pack(fill='both', expand=True, pady=10)
    texts = ttk.Frame(tabs, padding=8); bibliography = ttk.Frame(tabs, padding=8)
    tabs.add(texts, text='引用文章'); tabs.add(bibliography, text='文献の登録')
    texts.rowconfigure(1, weight=1); texts.columnconfigure(1, weight=1)
    tree = ttk.Treeview(texts, columns=('frame', 'layer', 'text'), show='headings', selectmode='browse', height=10)
    for name, label, width in [('frame', '開始', 55), ('layer', '層', 35), ('text', '文章', 235)]:
        tree.heading(name, text=label); tree.column(name, width=width, stretch=name == 'text')
    tree.grid(row=0, column=0, rowspan=3, sticky='nsew', padx=(0, 10))
    editor = tk.Text(texts, wrap='word', undo=True, height=10)
    ttk.Label(texts, text='引用キーを含む文章（ここで編集できます）').grid(row=0, column=1, sticky='w')
    editor.grid(row=1, column=1, sticky='nsew')
    preview = tk.Text(texts, wrap='word', height=7, state='disabled', background='#f1f1f1')
    preview.grid(row=2, column=1, sticky='ew', pady=(8,0))
    current = [None]
    def commit():
        if current[0] is not None:
            text = editor.get('1.0', 'end-1c')
            old = templates[current[0]]
            # Tk may normalize Windows line endings; preserve untouched originals.
            if text.replace('\r\n', '\n') != old.replace('\r\n', '\n'):
                templates[current[0]] = text
    def show_preview(text):
        preview.configure(state='normal'); preview.delete('1.0', 'end'); preview.insert('1.0', text); preview.configure(state='disabled')
    def select(_=None):
        selection = tree.selection()
        if not selection: return
        commit(); current[0] = int(selection[0])
        editor.delete('1.0', 'end'); editor.insert('1.0', templates[current[0]])
        show_preview('番号をプレビューすると、更新後の文章を表示します。')
    for row in sorted(snapshot.rows, key=lambda row: (row.start, row.layer, row.index)):
        tree.insert('', 'end', iid=str(row.index), values=(row.start + 1, row.layer + 1, templates[row.index].replace('\n', ' ')[:60]))
    tree.bind('<<TreeviewSelect>>', select)
    if snapshot.rows:
        tree.selection_set(str(snapshot.rows[0].index)); select()
    bibliography.columnconfigure(1, weight=1); bibliography.rowconfigure(0, weight=1)
    refs = ttk.Treeview(bibliography, columns=('key', 'info'), show='headings', height=8, selectmode='browse')
    refs.heading('key', text='論文キー'); refs.heading('info', text='脚注に表示する書誌情報')
    refs.column('key', width=180); refs.column('info', width=550)
    refs.grid(row=0, column=0, columnspan=3, sticky='nsew', pady=(0,10))
    key = tk.StringVar()
    ttk.Label(bibliography, text='論文キー').grid(row=1, column=0, sticky='w')
    ttk.Entry(bibliography, textvariable=key).grid(row=1, column=1, sticky='ew')
    ttk.Label(bibliography, text='書誌情報').grid(row=2, column=0, sticky='nw', pady=8)
    info = tk.Text(bibliography, height=5, wrap='word'); info.grid(row=2, column=1, sticky='ew', pady=8)
    def refresh_refs():
        for item in refs.get_children(): refs.delete(item)
        for ident, detail in entries.items(): refs.insert('', 'end', iid=ident, values=(ident, detail.replace('\n',' ')))
    def select_ref(_=None):
        chosen = refs.selection()
        if chosen:
            key.set(chosen[0]); info.delete('1.0','end'); info.insert('1.0', entries[chosen[0]])
    refs.bind('<<TreeviewSelect>>', select_ref)
    def add_ref():
        try:
            value = {key.get().strip(): info.get('1.0','end-1c').strip()}
            validate_entries(value); entries.update(value); refresh_refs()
            status.set('文献を登録しました。引用番号を反映すると文献ファイルも保存します。')
        except ValueError as exc: messagebox.showerror('文献の登録', str(exc))
    def remove_ref():
        for ident in refs.selection(): entries.pop(ident, None)
        refresh_refs()
    actions = ttk.Frame(bibliography); actions.grid(row=3, column=1, sticky='w')
    ttk.Button(actions, text='追加 / 変更', command=add_ref).pack(side='left', padx=(0,8))
    ttk.Button(actions, text='削除', command=remove_ref).pack(side='left')
    path = tk.StringVar(value=str(args.references))
    ttk.Label(bibliography, textvariable=path, wraplength=800).grid(row=4, column=0, columnspan=3, sticky='w', pady=10)
    def read_refs(chosen):
        try:
            count = import_entries(chosen, entries)
            path.set(str(chosen)); refresh_refs()
            key.set(''); info.delete('1.0', 'end')
            commit()
            rendered, numbers, _, _ = resolve(
                [(row.index, row.start, row.layer, templates[row.index]) for row in snapshot.rows], entries)
            if current[0] is not None: show_preview(rendered[current[0]])
            status.set(f'文献を{count}件読み込みました。同じキーの書誌情報を更新しました。「引用番号をAviUtlに反映」で本文・脚注・文献一覧へ反映します。')
        except (ValueError, OSError) as exc: messagebox.showerror('文献データ', str(exc))
    def open_refs():
        chosen = filedialog.askopenfilename(initialdir=str(Path(path.get()).parent), filetypes=[('文献データ', '*.json')])
        if chosen: read_refs(chosen)
    def reload_refs():
        read_refs(path.get())
    def save_refs():
        try: save_entries(path.get(), entries); status.set('文献データを保存しました。')
        except (ValueError, OSError) as exc: messagebox.showerror('文献データ', str(exc))
    def choose_path():
        chosen = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('文献データ', '*.json')])
        if chosen: path.set(chosen)
    files = ttk.Frame(bibliography); files.grid(row=5,column=0,columnspan=3,sticky='w')
    for label, command in [('文献ファイルを開く',open_refs),('文献ファイルを再読み込み',reload_refs),('保存先を変更',choose_path),('文献データを保存',save_refs)]:
        ttk.Button(files,text=label,command=command).pack(side='left',padx=(0,8))
    refresh_refs()
    def prepare(restore=False):
        commit()
        return make_updates(snapshot, templates, entries, restore)
    def preview_numbers():
        try:
            commit()
            rendered, numbers, _, _ = resolve(
                [(row.index, row.start, row.layer, templates[row.index]) for row in snapshot.rows], entries)
            if current[0] is not None: show_preview(rendered[current[0]])
            status.set(' / '.join(f'[{number}] {ident}' for ident, number in numbers.items()) or '引用がありません。')
        except (ValueError, OSError) as exc: messagebox.showerror('引用番号',str(exc))
    def apply(restore=False):
        nonlocal state
        try:
            updates, rendered, numbers = prepare(restore)
            if not restore: save_entries(path.get(), entries)
            next_state = update_state(state, snapshot.scene,
                [(row.index, row.start, row.layer, row.text) for row in snapshot.rows],
                templates, rendered, entries)
            # Save recovery data first; rollback it if AviUtl rejects the snapshot.
            previous_state = load_state(metadata_path)
            save_state(metadata_path, next_state)
            try:
                apply_updates(args.target, snapshot, updates)
            except Exception:
                save_state(metadata_path, previous_state)
                raise
            state = next_state
            if current[0] is not None: show_preview(templates[current[0]] if restore else rendered[current[0]])
            status.set(f'{len(updates)}個のテキストを更新しました。AUPと引用管理ファイル（.citations.json）を一緒に保管してください。')
        except (ValueError, OSError) as exc: messagebox.showerror('引用番号の更新',str(exc))
    metadata_label = tk.StringVar(value='引用キーの保存先: ' + str(metadata_path))
    metadata_controls = ttk.Frame(box); metadata_controls.pack(fill='x', pady=(4,8))
    ttk.Label(metadata_controls, textvariable=metadata_label, wraplength=730).pack(side='left', fill='x', expand=True)
    def choose_metadata():
        nonlocal metadata_path, state, entries, templates
        chosen = filedialog.askopenfilename(filetypes=[('引用管理データ', '*.citations.json')])
        if not chosen: return
        try:
            loaded = load_state(chosen)
            recovered, embedded, warnings = recover(rows, loaded, snapshot.scene)
            commit()
            templates.update(recovered); entries.update(embedded)
            state = loaded; metadata_path = Path(chosen)
            metadata_label.set('引用キーの保存先: ' + str(metadata_path))
            refresh_refs()
            current[0] = None; select()
            status.set('引用管理ファイルを読み込みました。文献情報と文章を確認して反映してください。')
        except (ValueError, OSError) as exc: messagebox.showerror('引用管理データ', str(exc))
    ttk.Button(metadata_controls, text='引用管理ファイルを開く', command=choose_metadata).pack(side='right')
    footer = ttk.Frame(box); footer.pack(fill='x')
    ttk.Button(footer,text='番号をプレビュー',command=preview_numbers).pack(side='left',padx=(0,8))
    ttk.Button(footer,text='引用番号をAviUtlに反映',command=apply).pack(side='left',padx=(0,8))
    ttk.Button(footer,text='引用キーに戻す',command=lambda:apply(True)).pack(side='left')
    ttk.Label(box,textvariable=status,wraplength=930).pack(anchor='w',pady=(10,0))
    root.mainloop()

def parse_options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--target',type=int,required=True)
    parser.add_argument('--references',type=Path,required=True)
    return parser.parse_args(argv)

if __name__ == '__main__':
    options = parse_options()
    try:
        main(options)
    except Exception as exc:
        root = create_root(); root.withdraw()
        messagebox.showerror('引用管理を開けません',str(exc)); root.destroy()
    finally:
        cleanup_snapshot(options.source)
