import os
import json
import shutil
import streamlit as st
from pathlib import Path
from streamlit_tree_select import tree_select

# tkinter for file dialogs
try:
    import tkinter as tk
    from tkinter import filedialog
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False

# ========= Streamlit ページ設定 =========
st.set_page_config(page_title="ファイル選択ツール", layout="wide")

# ========= デフォルト設定 =========
DEFAULT_SEARCH_PATH = "."
DEFAULT_EXCLUDE_DIRS = ["old", "temp", "work", ".git", "__pycache__", "node_modules"]
DEFAULT_INCLUDE_EXTS = [".docx", ".xlsx", ".xls", ".pptx", ".pdf", ".txt", ".md"]


# ========= ファイルダイアログ =========
def open_folder_dialog(title="フォルダを選択"):
    """フォルダ選択ダイアログを開く"""
    if not HAS_TKINTER:
        return None
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", 1)
    folder = filedialog.askdirectory(title=title)
    root.destroy()
    return folder if folder else None


def open_file_dialog(title="ファイルを選択", filetypes=None):
    """ファイル選択ダイアログを開く"""
    if not HAS_TKINTER:
        return None
    if filetypes is None:
        filetypes = [("JSON files", "*.json"), ("All files", "*.*")]
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", 1)
    filepath = filedialog.askopenfilename(title=title, filetypes=filetypes)
    root.destroy()
    return filepath if filepath else None


def save_file_dialog(title="保存先を選択", filetypes=None, defaultextension=".json"):
    """ファイル保存ダイアログを開く"""
    if not HAS_TKINTER:
        return None
    if filetypes is None:
        filetypes = [("JSON files", "*.json"), ("All files", "*.*")]
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", 1)
    filepath = filedialog.asksaveasfilename(
        title=title, filetypes=filetypes, defaultextension=defaultextension
    )
    root.destroy()
    return filepath if filepath else None


# ========= セッション初期化 =========
def init_state():
    defaults = {
        "search_path": DEFAULT_SEARCH_PATH,
        "dest_path": "",
        "exclude_dirs": DEFAULT_EXCLUDE_DIRS.copy(),
        "include_exts": DEFAULT_INCLUDE_EXTS.copy(),
        "entries": [],
        "selected_paths": set(),
        "tree_expanded": [],
        "_tree_key_version": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()


# ========= ファイル検索 =========
@st.cache_data(show_spinner=False)
def search_files(root: str, exclude_dirs: tuple, include_exts: tuple):
    """ファイルを検索してリストを返す"""
    entries = []
    exclude_dirs_norm = set(d.lower() for d in exclude_dirs)
    include_exts_norm = set(
        (e.lower() if e.startswith(".") else f".{e.lower()}") for e in include_exts
    )

    for dirpath, dirnames, filenames in os.walk(root):
        # 除外フォルダをスキップ
        dirnames[:] = [d for d in dirnames if d.lower() not in exclude_dirs_norm]

        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            # 拡張子フィルタ（空の場合は全ファイル）
            if include_exts_norm and ext not in include_exts_norm:
                continue

            abs_path = os.path.join(dirpath, fn)
            rel_path = os.path.relpath(abs_path, root)
            entries.append({
                "file_name": fn,
                "rel_path": rel_path,
                "abs_path": abs_path,
            })

    return entries


# ========= ツリー構造生成 =========
def build_tree_nodes(entries):
    """streamlit-tree-select用のノードリストを構築"""
    tree = {}

    for entry in entries:
        parts = entry["rel_path"].replace("\\", "/").split("/")
        current = tree

        # フォルダ階層を構築
        for part in parts[:-1]:
            if part not in current:
                current[part] = {"_children": {}, "_is_folder": True}
            current = current[part]["_children"]

        # ファイルノード
        filename = parts[-1]
        current[filename] = {
            "_is_file": True,
            "_abs_path": entry["abs_path"],
        }

    def convert_to_nodes(tree_dict, prefix=""):
        """dictをstreamlit-tree-select形式のノードリストに変換"""
        nodes = []
        folders = sorted([k for k, v in tree_dict.items() if v.get("_is_folder")])
        files = sorted([k for k, v in tree_dict.items() if v.get("_is_file")])

        for folder_name in folders:
            folder_data = tree_dict[folder_name]
            folder_path = f"{prefix}/{folder_name}" if prefix else folder_name
            children = convert_to_nodes(folder_data["_children"], folder_path)
            nodes.append({
                "label": folder_name,
                "value": f"folder:{folder_path}",
                "children": children,
            })

        for file_name in files:
            file_data = tree_dict[file_name]
            nodes.append({
                "label": file_name,
                "value": file_data["_abs_path"],
            })

        return nodes

    return convert_to_nodes(tree)


# ========= 設定保存・読み込み =========
def save_config(filepath: str):
    """設定をファイルに保存（新形式）"""
    data = {
        "search_path": st.session_state.search_path,
        "dest_path": st.session_state.dest_path,
        "exclude_dirs": list(st.session_state.exclude_dirs),
        "include_exts": list(st.session_state.include_exts),
        "selected_paths": list(st.session_state.selected_paths),
    }
    Path(filepath).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config(filepath: str):
    """設定をファイルから読み込み（新旧両形式対応）"""
    if not Path(filepath).exists():
        return False

    data = json.loads(Path(filepath).read_text(encoding="utf-8"))

    # 共通フィールド
    if "search_path" in data:
        st.session_state.search_path = data["search_path"]
    if "dest_path" in data:
        st.session_state.dest_path = data["dest_path"]
    if "exclude_dirs" in data:
        st.session_state.exclude_dirs = data["exclude_dirs"]
    if "include_exts" in data:
        st.session_state.include_exts = data["include_exts"]

    # 選択パス（後方互換性：selected_abs_paths も対応）
    if "selected_paths" in data:
        st.session_state.selected_paths = set(data["selected_paths"])
    elif "selected_abs_paths" in data:
        # 旧形式：selected_abs_paths を selected_paths として使用
        st.session_state.selected_paths = set(data["selected_abs_paths"])

    return True


# ========= UI =========

# ---------- サイドバー ----------
with st.sidebar:
    # ---------- 設定ファイル ----------
    st.header("設定ファイル")

    config_col1 = st.columns(2)
    with config_col1[0]:  # 左: 読込
        if st.button("設定を読込", use_container_width=True):
            filepath = open_file_dialog("設定ファイルを選択")
            if filepath:
                if load_config(filepath):
                    st.success(f"読込: {Path(filepath).name}")
                    # 自動で検索を実行
                    if st.session_state.search_path and os.path.isdir(st.session_state.search_path):
                        with st.spinner("ファイルを検索中..."):
                            entries = search_files(
                                st.session_state.search_path,
                                tuple(st.session_state.exclude_dirs),
                                tuple(st.session_state.include_exts),
                            )
                            st.session_state.entries = entries
                            # 設定から読み込んだ選択を検証
                            existing_paths = {e["abs_path"] for e in entries}
                            st.session_state.selected_paths = st.session_state.selected_paths & existing_paths
                            st.session_state._tree_key_version += 1
                    st.rerun()
                else:
                    st.error("ファイルが見つかりません。")

    with config_col1[1]:  # 右: 保存
        if st.button("設定を保存", use_container_width=True):
            filepath = save_file_dialog("設定ファイルの保存先", defaultextension=".json")
            if filepath:
                save_config(filepath)
                st.success(f"保存: {Path(filepath).name}")

    st.divider()

    # ---------- 検索条件 ----------
    st.header("検索条件")

    # 検索フォルダ
    search_col = st.columns([3, 1])
    with search_col[0]:
        st.session_state.search_path = st.text_input(
            "検索対象フォルダ",
            value=st.session_state.search_path,
        )
    with search_col[1]:
        st.write("")  # スペーサー
        st.write("")
        if st.button("...", key="browse_search", help="フォルダを選択"):
            folder = open_folder_dialog("検索対象フォルダを選択")
            if folder:
                st.session_state.search_path = folder
                st.rerun()

    # 除外フォルダ
    exclude_dirs_input = st.text_input(
        "除外フォルダ（カンマ区切り）",
        value=", ".join(st.session_state.exclude_dirs),
    )
    st.session_state.exclude_dirs = [
        s.strip() for s in exclude_dirs_input.split(",") if s.strip()
    ]

    # 対象拡張子
    include_exts_input = st.text_input(
        "対象拡張子（空欄=すべて）",
        value=", ".join(st.session_state.include_exts),
    )
    st.session_state.include_exts = [
        s.strip() for s in include_exts_input.split(",") if s.strip()
    ]

    # 検索ボタン
    sidebar_col = st.columns(2)
    with sidebar_col[0]:
        start_search = st.button("検索", type="primary", use_container_width=True)
    with sidebar_col[1]:
        clear_state = st.button("クリア", use_container_width=True)

    if start_search:
        path = st.session_state.search_path
        if not path or not os.path.isdir(path):
            st.error("検索フォルダが不正です。")
        else:
            with st.spinner("ファイルを検索中..."):
                entries = search_files(
                    path,
                    tuple(st.session_state.exclude_dirs),
                    tuple(st.session_state.include_exts),
                )
                st.session_state.entries = entries
                # 既存の選択を維持するため、存在するパスのみ残す
                existing_paths = {e["abs_path"] for e in entries}
                st.session_state.selected_paths = st.session_state.selected_paths & existing_paths
                st.session_state._tree_key_version += 1
                st.success(f"{len(entries)} 件のファイルが見つかりました。")

    if clear_state:
        st.session_state.entries = []
        st.session_state.selected_paths = set()
        st.session_state._tree_key_version += 1
        st.info("クリアしました。")

    st.divider()

    # ---------- 保存セクション ----------
    st.header("保存")

    dest_col = st.columns([3, 1])
    with dest_col[0]:
        st.session_state.dest_path = st.text_input(
            "保存先フォルダ",
            value=st.session_state.dest_path,
        )
    with dest_col[1]:
        st.write("")
        st.write("")
        if st.button("...", key="browse_dest", help="フォルダを選択"):
            folder = open_folder_dialog("保存先フォルダを選択")
            if folder:
                st.session_state.dest_path = folder
                st.rerun()

    if st.button("ファイルを保存", type="primary", use_container_width=True):
        dest = st.session_state.dest_path
        if not dest:
            st.error("保存先を指定してください。")
        elif not st.session_state.selected_paths:
            st.info("ファイルが選択されていません。")
        else:
            os.makedirs(dest, exist_ok=True)

            # 対象ファイルを先に抽出
            targets = [e for e in st.session_state.entries if e["abs_path"] in st.session_state.selected_paths]

            # プログレスバー付きでコピー
            prog = st.progress(0)
            for i, entry in enumerate(targets):
                src = entry["abs_path"]
                rel = entry["rel_path"]
                dst = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                prog.progress((i + 1) / len(targets))

            st.success(f"{len(targets)} 件のファイルをコピーしました。")

# ---------- メインエリア ----------
st.header("ファイル選択ツール")

if st.session_state.entries:
    total_files = len(st.session_state.entries)
    selected_count = len(st.session_state.selected_paths)

    # メトリクス
    metric_col = st.columns([1, 1, 4])
    metric_col[0].metric("ファイル数", total_files)
    metric_col[1].metric("選択中", selected_count)

    # 選択中ファイル一覧
    if selected_count > 0:
        with st.expander(f"選択中のファイル一覧 ({selected_count}件)", expanded=False):
            for abs_path in sorted(st.session_state.selected_paths):
                st.text(abs_path)

    st.divider()

    # ツリービュー
    with st.spinner("ツリーを構築中..."):
        nodes = build_tree_nodes(st.session_state.entries)
        valid_file_paths = {e["abs_path"] for e in st.session_state.entries}

    tree_key = f"file_tree_v{st.session_state._tree_key_version}"

    result = tree_select(
        nodes,
        checked=list(st.session_state.selected_paths),
        expanded=st.session_state.tree_expanded,
        only_leaf_checkboxes=False,
        show_expand_all=True,
        key=tree_key,
    )

    if result:
        new_selected = set(
            v for v in result.get("checked", [])
            if v in valid_file_paths
        )
        new_expanded = result.get("expanded", [])

        st.session_state.tree_expanded = new_expanded

        if new_selected != st.session_state.selected_paths:
            st.session_state.selected_paths = new_selected
            st.rerun()
else:
    st.info("サイドバーの「検索」ボタンで検索を開始してください。")
