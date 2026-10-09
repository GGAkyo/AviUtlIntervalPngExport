"""Desktop front end for the standalone EXO converter."""
from pathlib import Path
import argparse
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from exo_to_pptx import convert


def create_root():
    try:
        return tk.Tk()
    except tk.TclError:
        # Optional local copy for hosts where Tcl cannot access the Python install.
        # Keep Tcl/Tk outside the Python installation; never modify system files.
        import os
        runtime = Path(__file__).resolve().parent / ".runtime" / "tcl"
        if not (runtime / "tcl8.6" / "init.tcl").is_file():
            raise
        os.environ["TCL_LIBRARY"] = str(runtime / "tcl8.6")
        os.environ["TK_LIBRARY"] = str(runtime / "tk8.6")
        return tk.Tk()

def available_output(path):
    path = Path(path)
    candidate = path
    index = 2
    while candidate.exists() or candidate.with_suffix(".report.json").exists():
        candidate = path.with_name(path.stem + f"_{index}" + path.suffix)
        index += 1
    return candidate


def cleanup_snapshot(source):
    """Remove only the two files in a snapshot owned by this installation."""
    source = Path(source).resolve()
    snapshots = Path(__file__).resolve().parent / ".runtime" / "snapshots"
    folder = source.parent
    marker = folder / "snapshot.owner"
    if (source.name != "scene.exo" or folder.parent != snapshots.resolve()
            or not folder.name.startswith("epe") or not marker.is_file()
            or marker.read_bytes() != b"EditablePptxExport snapshot v1\n"):
        raise ValueError("このツールが取得した一時データではありません。")
    (folder / "citation.input").unlink(missing_ok=True)
    source.unlink(missing_ok=True)
    marker.unlink()
    # Never remove other files, even if someone has put them in this directory.
    try:
        folder.rmdir()
    except OSError:
        pass


def parse_options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--citation-state", type=Path)
    parser.add_argument("--citation-scene", type=int, default=0)
    parser.add_argument("--default-references", type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-base", type=Path)
    parser.add_argument("--temporary-source", action="store_true")
    args = parser.parse_args(argv)
    if args.temporary_source and not args.source:
        parser.error("--temporary-source requires --source")
    return args


def main(options=None):
    options = options or parse_options()
    root = create_root()
    root.title("AviUtl 1 → 編集可能PPTX（試作版）")
    root.geometry("740x510")
    root.minsize(640, 490)
    box = ttk.Frame(root, padding=18)
    box.pack(fill="both", expand=True)
    box.columnconfigure(1, weight=1)
    source, output, assets = (tk.StringVar() for _ in range(3))
    if options.source:
        source.set(str(options.source))
        suggested = options.output or options.source.with_name(options.source.stem + "_editable.pptx")
        output.set(str(available_output(suggested)))
    source_base = options.source_base
    interval, start, end = tk.StringVar(value="30"), tk.StringVar(value="1"), tk.StringVar()
    bg = tk.StringVar(value="000000")
    approx = tk.BooleanVar(value=False)
    status = tk.StringVar(value="EXOを選択してください。元のEXO・画像は変更しません。")
    if options.temporary_source:
        status.set("AviUtlの現在のシーンを取得しました。保存先と間隔を指定して変換してください。")
    events = queue.Queue()

    def choose_source():
        nonlocal source_base
        path = filedialog.askopenfilename(filetypes=[("AviUtl EXO", "*.exo")])
        if path:
            source.set(path)
            source_base = None
            output.set(str(available_output(Path(path).with_name(Path(path).stem + "_editable.pptx"))))

    def choose_output():
        path = filedialog.asksaveasfilename(defaultextension=".pptx", filetypes=[("PowerPoint", "*.pptx")])
        if path:
            output.set(path)

    def choose_assets():
        path = filedialog.askdirectory()
        if path:
            assets.set(path)

    ttk.Label(box, text="文字を編集・コピー・検索できるPowerPointへ", font=("", 14, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))
    for row, label, value, command in [
        (1, "入力シーン / EXO", source, choose_source), (2, "保存先PPTX", output, choose_output),
        (3, "画像移動先（任意）", assets, choose_assets)]:
        ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", padx=(0,10), pady=6)
        ttk.Entry(box, textvariable=value).grid(row=row, column=1, sticky="ew")
        ttk.Button(box, text="選択", command=command).grid(row=row, column=2, padx=(8,0))
    for row, label, value in [(4, "間隔（フレーム）", interval), (5, "開始（1始まり）", start),
                              (6, "終了（空欄＝最後）", end), (7, "背景色（RGB）", bg)]:
        ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(box, textvariable=value, width=16).grid(row=row, column=1, sticky="w")
    ttk.Checkbutton(box, text="近似変換を許可する（未対応効果の省略・オブジェクトの除外）",
                    variable=approx).grid(row=8, column=0, columnspan=3, sticky="w", pady=(14,8))
    ttk.Label(box, text="通常文字・静止画像・背景・塗りつぶし四角形が対象です。字幅や行間の完全一致は保証しません。\n"
                       "移動・中間点・スクリプト等は未対応です。警告は変換レポートに記録します。",
              wraplength=670).grid(row=9, column=0, columnspan=3, sticky="w", pady=6)

    def run():
        try:
            if not source.get() or not output.get():
                raise ValueError("入力EXOと保存先を指定してください。")
            params = dict(source=source.get(), output=output.get(),
                          interval=int(interval.get()), start=int(start.get()),
                          end=int(end.get()) if end.get().strip() else None,
                          asset_dir=assets.get() or None, allow_approx=approx.get(),
                          background=bg.get().strip(), source_base=source_base, references=options.references,
                          default_references=options.default_references,
                          citation_state=options.citation_state, citation_scene=options.citation_scene)
        except ValueError as exc:
            messagebox.showerror("入力を確認してください", str(exc))
            return
        button.configure(state="disabled")
        status.set("変換中です…")
        def worker():
            try:
                report = convert(**params)
                events.put(("ok", report))
            except Exception as exc:
                events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            kind, result = events.get_nowait()
        except queue.Empty:
            root.after(150, poll)
            return
        button.configure(state="normal")
        if kind == "ok":
            status.set(f"完了: {len(result.frames)}枚 / 警告 {len(result.warnings)}件")
            messagebox.showinfo("変換完了", f"{result.output}\n\n{len(result.frames)}枚 / 警告 {len(result.warnings)}件\n"
                                "同名の .report.json に変換結果を保存しました。")
        else:
            status.set("変換できませんでした。詳細を確認してください。")
            messagebox.showerror("変換エラー", result)
        root.after(150, poll)

    button = ttk.Button(box, text="PPTXに変換", command=run)
    button.grid(row=10, column=0, columnspan=3, pady=12)
    ttk.Label(box, textvariable=status, wraplength=670).grid(row=11, column=0, columnspan=3, sticky="w")
    def close():
        if str(button["state"]) == "disabled":
            messagebox.showinfo("変換中", "変換が終わってから閉じてください。")
            return
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)
    poll()
    root.mainloop()

if __name__ == "__main__":
    options = parse_options()
    try:
        main(options)
    finally:
        if options.temporary_source:
            cleanup_snapshot(options.source)
