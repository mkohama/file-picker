import os
import re
import json
import shutil
import time
import streamlit as st
from pathlib import Path
from streamlit_tree_select import tree_select

# ========= 設定ファイルパス =========
CONFIG_PATH = Path("filecollect_config.json")

# ========= デフォルト設定 =========
# fmt: off
DEFAULT_SEARCH_PATH = Path(".")
DEFAULT_EXCLUDE_DIRS = [
    "old",
    "memo",
    "temp2",
    "old2",
    "old_最新版",
    "work",

]
DEFAULT_INCLUDE_EXTS = [".docx", ".xlsx", ".xls", ".pptx", ".pdf", ".txt", ".md"]
DEFAULT_EXCLUDE_FILE_PATTERNS = [
    r"^~.*", 
    r".*コピー.*", 
    r".*copy.*",
]
VERSION_REGEX = re.compile(r"\d+[_\.]\d+(?:[_\.]\d+)*")
DATE_REGEX = re.compile(r"_(\d{8})(?=\.|$)")
DEFAULT_PAGE_SIZE = 50
MAX_HISTORY = 10
# fmt: on


# ========= Streamlit ページ設定 =========
st.set_page_config(page_title="ファイル検索・収集ツール", layout="wide")


# ========= グループキー生成ヘルパー（早期に必要なため上部に配置） =========
def get_group_key(rel_path: str):
    """ファイルのグループキーを生成（バージョンフォルダと日付を除去）"""
    parts = rel_path.split(os.sep)
    # バージョンフォルダを除去（ファイル名以外のパーツから）
    non_version_parts = [
        p for p in parts[:-1] if not VERSION_REGEX.fullmatch(p)
    ]
    # ファイル名から日付を除去
    filename = parts[-1]
    base_filename = DATE_REGEX.sub("", filename)
    # グループキーを構築
    if non_version_parts:
        return os.path.join(*non_version_parts, base_filename)
    return base_filename


# ========= セッション初期化 =========
def init_state():
    defaults = {
        "entries": [],
        "groups": {},
        "versions_map": {},
        "ver_to_entry_map": {},
        "ver_subver_to_entry_map": {},
        "subversions_map": {},
        "selected_version": {},
        "selected_subversion": {},
        "selected_group": {},
        "selected_abs_paths": set(),  # パスベースの選択状態（タブ間共有）
        "_need_sync_to_group": False,  # ツリービューからグループビューへの同期フラグ
        "_tree_key_version": 0,  # ツリーコンポーネントのバージョン（外部同期時にインクリメント）
        "_group_ui_version": 0,  # グループビューのUIコンポーネントのバージョン（外部同期時にインクリメント）
        "_pending_toasts": [],  # 保留中のトーストメッセージ（rerun後に表示）
        "page": 1,
        "page_size": DEFAULT_PAGE_SIZE,
        "filter_text": "",
        "filter_use_regex": False,
        "search_path": DEFAULT_SEARCH_PATH,
        "dest_path": "",
        "exclude_dirs": DEFAULT_EXCLUDE_DIRS.copy(),
        "include_exts": DEFAULT_INCLUDE_EXTS.copy(),
        "exclude_file_patterns": DEFAULT_EXCLUDE_FILE_PATTERNS.copy(),
        "search_history": [],
        "dest_history": [],
        "_config_just_loaded": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # 設定ロード直後の場合、ウィジェットキーを状態変数から同期
    # （この時点ではまだウィジェットが描画されていないので変更可能）
    if st.session_state.get("_config_just_loaded") == True:
        st.session_state._search_path_input = str(st.session_state.search_path)
        st.session_state._dest_path_input = str(st.session_state.dest_path)
        st.session_state._filter_text_input = str(st.session_state.filter_text)
        # フラグを「メッセージ表示待ち」に変更
        st.session_state._config_just_loaded = "show_message"


init_state()

# ツリービューからの同期要求を処理（ウィジェット描画前に実行）
if st.session_state.get("_need_sync_to_group"):
    st.session_state._need_sync_to_group = False
    # 選択状態をリセットし、selected_abs_pathsから再構築
    for fn in st.session_state.selected_group:
        st.session_state.selected_group[fn] = False
    for abs_path in st.session_state.selected_abs_paths:
        for e in st.session_state.entries:
            if e["abs_path"] == abs_path:
                group_key = get_group_key(e["rel_path"])
                ver = e["version"]
                subver = e["subversion"]
                st.session_state.selected_group[group_key] = True
                st.session_state.selected_version[group_key] = ver
                if group_key not in st.session_state.selected_subversion:
                    st.session_state.selected_subversion[group_key] = {}
                st.session_state.selected_subversion[group_key][ver] = subver
                break

    # ウィジェットキーも同期（ウィジェット描画前なので安全）
    for fn in st.session_state.selected_group:
        st.session_state[f"sel_{fn}"] = st.session_state.selected_group[fn]

    # グループビューのセレクトボックスを再初期化（外部から同期されたため）
    st.session_state._group_ui_version += 1

# 保留中のトーストを表示（rerun後に実行される）
if st.session_state._pending_toasts:
    for msg in st.session_state._pending_toasts:
        st.toast(msg)
    st.session_state._pending_toasts = []


# ========= 選択状態同期ヘルパー =========
def get_entry_by_abs_path(abs_path):
    """パスからエントリを取得"""
    for e in st.session_state.entries:
        if e["abs_path"] == abs_path:
            return e
    return None


def sync_group_to_paths():
    """グループ選択 → パス選択に同期"""
    new_paths = set()
    for fn, sel in st.session_state.selected_group.items():
        if not sel:
            continue
        ver = st.session_state.selected_version.get(fn)
        if not ver:
            continue
        subver_dict = st.session_state.selected_subversion.get(fn, {})
        subver = subver_dict.get(ver, "-") if isinstance(subver_dict, dict) else "-"
        # エントリを検索
        entry_map = st.session_state.ver_subver_to_entry_map.get(fn, {})
        ver_map = entry_map.get(ver, {})
        entry = ver_map.get(subver)
        if entry:
            new_paths.add(entry["abs_path"])
    st.session_state.selected_abs_paths = new_paths
    st.session_state._tree_key_version += 1  # ツリーコンポーネントを再作成


def sync_paths_to_group():
    """パス選択 → グループ選択に同期"""
    # 現在のグループ選択をクリア
    for fn in st.session_state.selected_group:
        st.session_state.selected_group[fn] = False

    # パスからグループ選択を再構築
    for abs_path in st.session_state.selected_abs_paths:
        entry = get_entry_by_abs_path(abs_path)
        if entry:
            # グループキーを使用（base_nameではなくrel_pathから生成）
            group_key = get_group_key(entry["rel_path"])
            ver = entry["version"]
            subver = entry["subversion"]
            st.session_state.selected_group[group_key] = True
            st.session_state.selected_version[group_key] = ver
            if group_key not in st.session_state.selected_subversion:
                st.session_state.selected_subversion[group_key] = {}
            st.session_state.selected_subversion[group_key][ver] = subver


def resolve_version_conflict(new_selected: set, old_selected: set) -> tuple:
    """
    同じグループの複数バージョン選択を解決
    新しく追加されたファイルを優先し、同じグループの古いファイルは解除

    Returns:
        (resolved_set, removed_groups): 解決後のセット, 除外されたグループ名のリスト
    """
    added = new_selected - old_selected

    # 追加されたパスのグループキーを取得
    added_groups = {}
    for abs_path in added:
        entry = get_entry_by_abs_path(abs_path)
        if entry:
            group_key = get_group_key(entry["rel_path"])
            added_groups[group_key] = abs_path

    # 結果セットと除外グループリストを作成
    result = set()
    removed_group_names = []

    for abs_path in new_selected:
        entry = get_entry_by_abs_path(abs_path)
        if entry:
            group_key = get_group_key(entry["rel_path"])
            if group_key in added_groups:
                if abs_path == added_groups[group_key]:
                    result.add(abs_path)
                else:
                    # 同じグループの古いパスを除外
                    if group_key not in removed_group_names:
                        removed_group_names.append(group_key)
            else:
                result.add(abs_path)
        else:
            result.add(abs_path)

    return result, removed_group_names


# ========= ツリー構造生成（streamlit-tree-select用） =========
def build_tree_nodes(entries):
    """streamlit-tree-select用のノードリストを構築"""
    # 中間構造を構築
    tree = {}
    for entry in entries:
        parts = entry["rel_path"].replace("\\", "/").split("/")
        current = tree
        for i, part in enumerate(parts[:-1]):
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
        # フォルダを先に、ファイルを後に
        folders = sorted([k for k, v in tree_dict.items() if v.get("_is_folder")])
        files = sorted([k for k, v in tree_dict.items() if v.get("_is_file")])

        for folder_name in folders:
            folder_data = tree_dict[folder_name]
            folder_path = f"{prefix}/{folder_name}" if prefix else folder_name
            children = convert_to_nodes(folder_data["_children"], folder_path)
            nodes.append({
                "label": folder_name,
                "value": f"folder:{folder_path}",  # フォルダにはprefixを付ける
                "children": children,
            })

        for file_name in files:
            file_data = tree_dict[file_name]
            nodes.append({
                "label": file_name,
                "value": file_data["_abs_path"],  # ファイルは絶対パス
            })

        return nodes

    return convert_to_nodes(tree)


# ========= 設定保存 =========
def save_config():
    data = {
        "search_path": str(st.session_state.search_path),
        "dest_path": str(st.session_state.dest_path),
        "exclude_dirs": list(st.session_state.exclude_dirs),
        "include_exts": list(st.session_state.include_exts),
        "exclude_file_patterns": list(st.session_state.exclude_file_patterns),
        "search_history": list(st.session_state.search_history),
        "dest_history": list(st.session_state.dest_history),
        "selected_group": st.session_state.selected_group,
        "selected_version": st.session_state.selected_version,
        "selected_subversion": st.session_state.selected_subversion,
        "selected_abs_paths": list(st.session_state.selected_abs_paths),  # パスベース選択
        "filter_text": st.session_state.filter_text,
        "page": st.session_state.page,
        "page_size": st.session_state.page_size,
    }

    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ========= 設定ロード =========
def load_config():
    """設定をロードする。ウィジェットキーは init_state で同期されるため、
    ここでは状態変数のみを更新し、st.rerun() で再描画させる。
    """
    if not CONFIG_PATH.exists():
        st.warning("設定ファイルがありません。")
        return False

    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    for k in [
        "search_path",
        "dest_path",
        "exclude_dirs",
        "include_exts",
        "exclude_file_patterns",
        "search_history",
        "dest_history",
        "selected_group",
        "selected_version",
        "selected_subversion",
        "filter_text",
        "page",
        "page_size",
    ]:
        if k in data:
            st.session_state[k] = data[k]

    # パスベース選択をロード（後方互換性：なければグループ選択から生成）
    if "selected_abs_paths" in data:
        st.session_state.selected_abs_paths = set(data["selected_abs_paths"])
    else:
        # 古い設定ファイル：グループ選択から同期（entriesがロードされた後に実行）
        st.session_state.selected_abs_paths = set()

    # ウィジェットキーはここでは更新しない（ウィジェット描画後は変更不可）
    # 代わりに _config_loaded フラグを立てて、再描画時に init で同期する
    st.session_state._config_just_loaded = True

    return True


# ========= 履歴追加ユーティリティ =========
def push_history(lst, v):
    if not v:
        return lst
    new = [v] + [x for x in lst if x != v]
    return new[:MAX_HISTORY]


# ========= 検索結果のセッション保存 =========
def store_search_results(
    entries,
    groups,
    versions_map,
    ver_to_entry_map,
    ver_subver_to_entry_map,
    subversions_map,
):
    """検索結果をセッションステートに保存"""
    st.session_state.entries = entries
    st.session_state.groups = groups
    st.session_state.versions_map = versions_map
    st.session_state.ver_to_entry_map = ver_to_entry_map
    st.session_state.ver_subver_to_entry_map = ver_subver_to_entry_map
    st.session_state.subversions_map = subversions_map


def clear_search_results():
    """検索結果をクリア"""
    st.session_state.entries = []
    st.session_state.groups = {}
    st.session_state.versions_map = {}
    st.session_state.ver_to_entry_map = {}
    st.session_state.ver_subver_to_entry_map = {}
    st.session_state.subversions_map = {}
    st.session_state.selected_group = {}
    st.session_state.selected_version = {}
    st.session_state.selected_subversion = {}
    st.session_state.page = 1


def ensure_subversion_initialized(fn, ver, default_subver):
    """サブバージョンの初期化を確保"""
    if fn not in st.session_state.selected_subversion:
        st.session_state.selected_subversion[fn] = {}
    if not isinstance(st.session_state.selected_subversion[fn], dict):
        st.session_state.selected_subversion[fn] = {}
    if ver not in st.session_state.selected_subversion[fn]:
        st.session_state.selected_subversion[fn][ver] = default_subver


def select_version_for_file(fn, ver, subver, select=True):
    """ファイルのバージョン選択状態を更新"""
    st.session_state.selected_version[fn] = ver
    ensure_subversion_initialized(fn, ver, subver)
    st.session_state.selected_subversion[fn][ver] = subver
    st.session_state.selected_group[fn] = select
    st.session_state[f"sel_{fn}"] = select
    # バージョンselectboxのウィジェットキーも更新
    st.session_state[f"ver_{fn}"] = ver
    # サブバージョンselectboxのウィジェットキーも更新
    st.session_state[f"subver_{fn}_{ver}"] = subver


# ========= ユーティリティ =========
def normalize_exclude_dirs(lst):
    return [s.strip() for s in lst if s.strip()]


def normalize_include_exts(lst):
    """含める拡張子を正規化"""
    return [
        (
            ("." + s.strip().lower())
            if not s.strip().startswith(".")
            else s.strip().lower()
        )
        for s in lst
        if s.strip()
    ]


def normalize_exclude_file_patterns(lst):
    """除外ファイル名パターンを正規化（正規表現としてコンパイル）"""
    patterns = []
    for s in lst:
        s = s.strip()
        if not s:
            continue
        try:
            patterns.append(re.compile(s, re.IGNORECASE))
        except re.error as e:
            st.warning(f"正規表現エラー: '{s}' - {e}")
    return patterns


def is_excluded_filename(filename, patterns):
    """ファイル名が除外パターンに一致するか判定"""
    for pattern in patterns:
        if pattern.search(filename):
            return True
    return False


def parse_filter_query(query: str):
    """フィルタクエリをパースしてAND/OR/除外条件に分解

    構文:
      - スペース区切り: AND条件
      - | 区切り: OR条件
      - -プレフィックス: 除外条件

    例: "設計 仕様 -draft" → AND=["設計", "仕様"], OR=[], EXCLUDE=["draft"]
    例: "設計|仕様" → AND=[], OR=["設計", "仕様"], EXCLUDE=[]
    例: "設計|仕様 -draft" → AND=[], OR=["設計", "仕様"], EXCLUDE=["draft"]
    """
    if not query.strip():
        return [], [], []

    and_terms = []
    or_terms = []
    exclude_terms = []

    # スペースで分割
    tokens = query.split()

    for token in tokens:
        if not token:
            continue

        # 除外条件（-プレフィックス）
        if token.startswith("-") and len(token) > 1:
            exclude_terms.append(token[1:].lower())
        # OR条件（|を含む）
        elif "|" in token:
            parts = [p.strip().lower() for p in token.split("|") if p.strip()]
            or_terms.extend(parts)
        # AND条件
        else:
            and_terms.append(token.lower())

    return and_terms, or_terms, exclude_terms


def match_filter(filename: str, filter_text: str, use_regex: bool = False) -> bool:
    """ファイル名がフィルタ条件にマッチするか判定

    Args:
        filename: チェック対象のファイル名
        filter_text: フィルタ文字列
        use_regex: True=正規表現モード, False=演算子モード

    Returns:
        マッチすればTrue
    """
    if not filter_text.strip():
        return True

    fn_lower = filename.lower()

    # 正規表現モード
    if use_regex:
        try:
            return bool(re.search(filter_text, filename, re.IGNORECASE))
        except re.error:
            # 正規表現エラーの場合は通常の部分一致にフォールバック
            return filter_text.lower() in fn_lower

    # 演算子モード
    and_terms, or_terms, exclude_terms = parse_filter_query(filter_text)

    # 除外条件チェック（1つでもマッチしたらFalse）
    for term in exclude_terms:
        if term in fn_lower:
            return False

    # OR条件チェック（1つでもマッチすればOK、空ならスキップ）
    if or_terms:
        if not any(term in fn_lower for term in or_terms):
            return False

    # AND条件チェック（全てマッチする必要あり）
    for term in and_terms:
        if term not in fn_lower:
            return False

    return True


def extract_date_from_filename(filename):
    """ファイル名から日付（YYYYMMDD）を抽出"""
    m = DATE_REGEX.search(filename)
    if m:
        return m.group(1)
    return None


def get_base_filename(filename):
    """ファイル名から日付部分を除去したベース名を取得"""
    # 拡張子を分離
    name, ext = os.path.splitext(filename)
    # 日付部分を削除
    base_name = DATE_REGEX.sub("", name)
    return base_name + ext


def find_version_from_relpath(rel_path: str):
    """相対パスからバージョンフォルダを検出（フォルダ名が完全にバージョン番号の場合のみ）"""
    dir_part = os.path.dirname(rel_path)
    if not dir_part:
        return "-"
    for part in reversed(dir_part.split(os.sep)):
        # フォルダ名全体がバージョン番号の場合のみマッチ
        if VERSION_REGEX.fullmatch(part):
            return part.replace(".", "_")
    return "-"


def version_key(ver: str):
    """バージョン文字列をソート用のタプルに変換"""
    # "-" はバージョンフォルダなし = 最新として扱う
    if ver == "-":
        return (float("inf"),)
    ver_normalized = ver.replace(".", "_")
    m = re.match(r"^(\d+)_([\d_]+)$", ver_normalized)
    if not m:
        return (-1,)
    leading = int(m.group(1))
    rest = m.group(2)
    parts = tuple(int(x) for x in rest.split("_"))
    return (leading, *parts)


def subversion_key(subver: str):
    """サブバージョン（日付 or None）をソート用のタプルに変換"""
    if subver is None or subver == "-":
        # 日付なし = 最新として扱う
        return (99999999,)
    try:
        return (int(subver),)
    except Exception:
        return (0,)


@st.cache_data(show_spinner=False)
def search_files(
    root: str, exclude_dirs: list, include_exts: list, exclude_file_patterns: list
):
    entries = []
    exclude_dirs_norm = set(d.lower() for d in exclude_dirs)
    include_exts_norm = set(e.lower() for e in include_exts)

    file_patterns = normalize_exclude_file_patterns(exclude_file_patterns)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in exclude_dirs_norm]
        for fn in filenames:
            if is_excluded_filename(fn, file_patterns):
                continue

            ext = os.path.splitext(fn)[1].lower()

            if include_exts_norm and ext not in include_exts_norm:
                continue

            abs_path = os.path.join(dirpath, fn)
            rel_path = os.path.relpath(abs_path, root)
            version = find_version_from_relpath(rel_path)

            # ★ 追加：日付とベース名を抽出
            date = extract_date_from_filename(fn)
            base_name = get_base_filename(fn)

            entries.append(
                {
                    "file_name": fn,
                    "base_name": base_name,  # ← 追加
                    "version": version,
                    "subversion": date if date else "-",  # ← 追加
                    "rel_path": rel_path,
                    "abs_path": abs_path,
                }
            )
    return entries


@st.cache_data(show_spinner=False)
def build_group_struct(entries):
    groups = {}
    versions_map = {}
    ver_to_entry_map = {}
    ver_subver_to_entry_map = {}  # ← 追加
    subversions_map = {}  # ← 追加

    # ★ ステップ1：グループキー（バージョンフォルダと日付を除いたパス）でグループ化
    for e in entries:
        group_key = get_group_key(e["rel_path"])
        groups.setdefault(group_key, []).append(e)

    # ★ ステップ2：各グループ内でバージョンとサブバージョンを整理
    for base_name, items in groups.items():
        ver_subver_map = {}  # {version: {subversion: entry}}

        for e in items:
            ver = e["version"]
            fn = e["file_name"]

            # ファイル名から日付（サブバージョン）を抽出
            date_match = DATE_REGEX.search(fn)
            subver = date_match.group(1) if date_match else "-"

            if ver not in ver_subver_map:
                ver_subver_map[ver] = {}

            ver_subver_map[ver][subver] = e

        # バージョンをソート
        versions_sorted = sorted(ver_subver_map.keys(), key=version_key, reverse=True)
        versions_map[base_name] = versions_sorted

        # サブバージョンマップを作成
        subversions_map[base_name] = {}
        for ver in versions_sorted:
            # サブバージョンをソート（"-" が最初=最新扱い、日付は降順）
            all_subvers = list(ver_subver_map[ver].keys())
            dates = sorted([s for s in all_subvers if s != "-"], reverse=True)
            subvers = (["-"] if "-" in all_subvers else []) + dates
            subversions_map[base_name][ver] = subvers

        # エントリマップを構築
        ver_subver_to_entry_map[base_name] = ver_subver_map

        # 後方互換性のため ver_to_entry_map も保持（最新サブバージョンを使用）
        ver_to_entry_map[base_name] = {}
        for ver in versions_sorted:
            latest_subver = subversions_map[base_name][ver][0]
            ver_to_entry_map[base_name][ver] = ver_subver_map[ver][latest_subver]

        # グループのアイテムをソート（計算済みのsubversionを使用）
        items.sort(
            key=lambda x: (version_key(x["version"]), x["subversion"]),
            reverse=True,
        )
        groups[base_name] = items

    return (
        groups,
        versions_map,
        ver_to_entry_map,
        ver_subver_to_entry_map,
        subversions_map,
    )


# ========= UI =========

# ---------- サイドバー ----------
with st.sidebar:
    # ---------- 設定保存・ロード ----------
    with st.expander("設定", expanded=False):
        # 設定ロード後のメッセージ表示と自動検索
        if st.session_state.get("_config_just_loaded") == "show_message":
            st.success("設定をロードしました。")
            st.session_state._config_just_loaded = False

            # 自動検索を実行
            if st.session_state.search_path and os.path.isdir(
                st.session_state.search_path
            ):
                times = {}

                with st.spinner("ファイルを検索中..."):
                    start = time.time()
                    ex_dirs = normalize_exclude_dirs(st.session_state.exclude_dirs)
                    inc_exts = normalize_include_exts(st.session_state.include_exts)
                    ex_patterns = st.session_state.exclude_file_patterns
                    entries = search_files(
                        st.session_state.search_path, ex_dirs, inc_exts, ex_patterns
                    )
                    times["検索"] = time.time() - start

                with st.spinner(f"グループ構造を構築中... ({len(entries)} 件)"):
                    start = time.time()
                    (
                        groups,
                        versions_map,
                        ver_to_entry_map,
                        ver_subver_to_entry_map,
                        subversions_map,
                    ) = build_group_struct(entries)
                    times["グループ構築"] = time.time() - start

                with st.spinner("選択状態をマージ中..."):
                    start = time.time()
                    store_search_results(
                        entries,
                        groups,
                        versions_map,
                        ver_to_entry_map,
                        ver_subver_to_entry_map,
                        subversions_map,
                    )

                    old_selected = st.session_state.selected_group.copy()
                    st.session_state.selected_group = {
                        fn: old_selected.get(fn, False) for fn in groups
                    }

                    for fn in groups:
                        if fn not in st.session_state.selected_version:
                            latest_ver = versions_map[fn][0]
                            st.session_state.selected_version[fn] = latest_ver
                            st.session_state.selected_subversion[fn] = {
                                latest_ver: subversions_map[fn][latest_ver][0]
                            }
                    times["マージ"] = time.time() - start

                with st.spinner("ツリービューと同期中..."):
                    start = time.time()
                    # グループ選択 → パス選択に同期（ツリービュー用）
                    sync_group_to_paths()
                    times["同期"] = time.time() - start

                # 経過時間を表示
                total = sum(times.values())
                time_str = " / ".join([f"{k}: {v:.1f}秒" for k, v in times.items()])
                st.info(f"完了（{len(entries)}件）- {time_str} / 合計: {total:.1f}秒")

        if st.button("設定をロード", use_container_width=True):
            ok = load_config()
            if ok:
                st.rerun()

        if st.button("設定を保存", use_container_width=True):
            save_config()
            st.success("保存しました。")

    st.header("検索条件")

    st.text_input(
        "検索対象フォルダ",
        key="_search_path_input",
        on_change=lambda: setattr(
            st.session_state, "search_path", st.session_state._search_path_input
        ),
    )

    if st.session_state.search_history:

        def on_search_history_change():
            sel = st.session_state._search_history_select
            if sel != "- 選択 -":
                st.session_state.search_path = sel
                st.session_state._search_path_input = sel

        st.selectbox(
            "履歴",
            ["- 選択 -"] + st.session_state.search_history,
            key="_search_history_select",
            on_change=on_search_history_change,
        )

    exclude_dirs_input = st.text_input(
        "除外フォルダ（カンマ区切り）",
        value=", ".join(s.strip() for s in st.session_state.exclude_dirs if s.strip()),
    )
    st.session_state.exclude_dirs = [
        s.strip() for s in exclude_dirs_input.split(",") if s.strip()
    ]

    include_exts_input = st.text_input(
        "対象拡張子（空欄=すべて）",
        value=", ".join(s.strip() for s in st.session_state.include_exts if s.strip()),
    )
    st.session_state.include_exts = [
        s.strip() for s in include_exts_input.split(",") if s.strip()
    ]

    with st.expander("除外パターン", expanded=False):
        st.session_state.exclude_file_patterns = st.text_area(
            "正規表現（1行1パターン）",
            value="\n".join(st.session_state.exclude_file_patterns),
            height=80,
            help="例: ^~.* (チルダで始まる), .*コピー.* (コピーを含む)",
            label_visibility="collapsed",
        ).split("\n")

    sidebar_col = st.columns(2)
    with sidebar_col[0]:
        start_search = st.button("検索", type="primary", use_container_width=True)
    with sidebar_col[1]:
        clear_state = st.button("クリア", use_container_width=True)

    # 検索処理
    if start_search:
        path = st.session_state.search_path
        if not path or not os.path.isdir(path):
            st.error("検索フォルダが不正です。")
        else:
            times = {}

            with st.spinner("ファイルを検索中..."):
                start = time.time()
                ex_dirs = normalize_exclude_dirs(st.session_state.exclude_dirs)
                inc_exts = normalize_include_exts(st.session_state.include_exts)
                ex_patterns = st.session_state.exclude_file_patterns
                entries = search_files(path, ex_dirs, inc_exts, ex_patterns)
                times["検索"] = time.time() - start

            with st.spinner(f"グループ構造を構築中... ({len(entries)} 件)"):
                start = time.time()
                (
                    groups,
                    versions_map,
                    ver_to_entry_map,
                    ver_subver_to_entry_map,
                    subversions_map,
                ) = build_group_struct(entries)
                times["グループ構築"] = time.time() - start

            with st.spinner("選択状態をマージ中..."):
                start = time.time()
                store_search_results(
                    entries,
                    groups,
                    versions_map,
                    ver_to_entry_map,
                    ver_subver_to_entry_map,
                    subversions_map,
                )

                # 以前の選択状態を保持
                old_selected_group = st.session_state.selected_group.copy()
                old_selected_version = st.session_state.selected_version.copy()
                old_selected_subversion = st.session_state.selected_subversion.copy()

                # 新しいグループに対して選択状態をマージ
                st.session_state.selected_group = {
                    fn: old_selected_group.get(fn, False) for fn in groups
                }

                # バージョン選択をマージ
                st.session_state.selected_version = {}
                for fn in versions_map:
                    if (
                        fn in old_selected_version
                        and old_selected_version[fn] in versions_map[fn]
                    ):
                        st.session_state.selected_version[fn] = old_selected_version[fn]
                    else:
                        st.session_state.selected_version[fn] = versions_map[fn][0]

                # サブバージョン選択をマージ
                st.session_state.selected_subversion = {}
                for fn in versions_map:
                    ver = st.session_state.selected_version[fn]
                    available_subvers = subversions_map.get(fn, {}).get(ver, ["-"])
                    old_fn_subvers = old_selected_subversion.get(fn, {})
                    if isinstance(old_fn_subvers, dict) and ver in old_fn_subvers:
                        old_subver = old_fn_subvers[ver]
                        if old_subver in available_subvers:
                            st.session_state.selected_subversion[fn] = {ver: old_subver}
                        else:
                            st.session_state.selected_subversion[fn] = {
                                ver: available_subvers[0]
                            }
                    else:
                        st.session_state.selected_subversion[fn] = {
                            ver: available_subvers[0]
                        }

                st.session_state.page = 1
                st.session_state.search_history = push_history(
                    st.session_state.search_history, path
                )
                times["マージ"] = time.time() - start

            # 経過時間を表示
            total = sum(times.values())
            time_str = " / ".join([f"{k}: {v:.1f}秒" for k, v in times.items()])
            preserved_count = sum(
                1 for v in st.session_state.selected_group.values() if v
            )
            if preserved_count > 0:
                st.success(f"{len(entries)}件（{preserved_count}件維持）- {time_str} / 合計: {total:.1f}秒")
            else:
                st.success(f"{len(entries)}件 - {time_str} / 合計: {total:.1f}秒")

    if clear_state:
        clear_search_results()
        st.info("クリアしました。")

    st.divider()

    # ---------- 保存セクション ----------
    st.header("保存")

    st.text_input(
        "保存先フォルダ",
        key="_dest_path_input",
        on_change=lambda: setattr(
            st.session_state, "dest_path", st.session_state._dest_path_input
        ),
    )

    if st.session_state.dest_history:

        def on_dest_history_change():
            sel = st.session_state._dest_history_select
            if sel != "- 選択 -":
                st.session_state.dest_path = sel
                st.session_state._dest_path_input = sel

        st.selectbox(
            "履歴",
            ["- 選択 -"] + st.session_state.dest_history,
            key="_dest_history_select",
            on_change=on_dest_history_change,
        )

    save_button = st.button("ファイルを保存", type="primary", use_container_width=True)

    if save_button:
        dest = st.session_state.dest_path
        if not dest:
            st.error("保存先を指定してください。")
        else:
            os.makedirs(dest, exist_ok=True)

            # selected_abs_paths からコピー対象エントリを取得
            targets = []
            for abs_path in st.session_state.selected_abs_paths:
                entry = get_entry_by_abs_path(abs_path)
                if entry:
                    targets.append(entry)

            if not targets:
                st.info("対象が選択されていません。")
            else:
                st.session_state.dest_history = push_history(
                    st.session_state.dest_history, dest
                )

                prog = st.progress(0)
                for i, e in enumerate(targets):
                    src = e["abs_path"]
                    rel = e["rel_path"]
                    dst = os.path.join(dest, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    prog.progress((i + 1) / len(targets))

                st.success(f"{len(targets)} 件コピー")

# ---------- メインエリア ----------
st.header("ファイル検索・収集ツール")

# タブ
tab_group, tab_tree = st.tabs(["グループビュー", "ツリービュー"])

# ---------- グループビュー ----------
with tab_group:
    if st.session_state.groups:
        total_files = len(st.session_state.entries)
        total_groups = len(st.session_state.groups)
        selected_count = sum(1 for v in st.session_state.selected_group.values() if v)

        # メトリクス（コンパクト）
        metric_col = st.columns([1, 1, 1, 3])
        metric_col[0].metric("ファイル数", total_files)
        metric_col[1].metric("グループ数", total_groups)
        metric_col[2].metric("選択中", selected_count)

        # 選択中ファイル一覧
        if selected_count > 0:
            with st.expander(f"選択中のファイル一覧 ({selected_count}件)", expanded=False):
                selected_files = [
                    fn
                    for fn in st.session_state.groups
                    if st.session_state.selected_group.get(fn)
                ]
                for fn in selected_files:
                    ver = st.session_state.selected_version.get(fn, "-")
                    subver_dict = st.session_state.selected_subversion.get(fn, {})
                    subver = subver_dict.get(ver, "-") if isinstance(subver_dict, dict) else "-"
                    # ver_subver_to_entry_map を使用してエントリを取得
                    entry = st.session_state.ver_subver_to_entry_map.get(fn, {}).get(ver, {}).get(subver)
                    if entry:
                        st.text(entry["abs_path"])
                    else:
                        st.text(fn)

        # フィルタ行
        st.caption("フィルタ（スペース=AND, |=OR, -=除外）")
        filter_col = st.columns([5, 1])
        with filter_col[0]:
            st.text_input(
                "フィルタ",
                key="_filter_text_input",
                on_change=lambda: setattr(
                    st.session_state, "filter_text", st.session_state._filter_text_input
                ),
                label_visibility="collapsed",
            )
        with filter_col[1]:
            st.checkbox(
                "正規表現",
                key="_filter_use_regex",
                value=st.session_state.filter_use_regex,
                on_change=lambda: setattr(
                    st.session_state, "filter_use_regex", st.session_state._filter_use_regex
                ),
            )

        filtered = [
            fn
            for fn in st.session_state.groups
            if match_filter(
                fn, st.session_state.filter_text, st.session_state.filter_use_regex
            )
        ]

        # ページネーション計算
        total_pages = max(
            1,
            (len(filtered) + st.session_state.page_size - 1) // st.session_state.page_size,
        )
        if st.session_state.page > total_pages:
            st.session_state.page = total_pages

        start_idx = (st.session_state.page - 1) * st.session_state.page_size
        end_idx = start_idx + st.session_state.page_size
        disp = filtered[start_idx:end_idx]

        # ページネーション
        page_col = st.columns([1, 1, 2, 1, 1, 3])
        with page_col[0]:
            if st.button("◀◀", key="page_first"):
                st.session_state.page = 1
        with page_col[1]:
            if st.button("◀", key="page_prev"):
                st.session_state.page = max(1, st.session_state.page - 1)
        with page_col[2]:
            st.write(f"**{st.session_state.page} / {total_pages} ページ**")
        with page_col[3]:
            if st.button("▶", key="page_next"):
                st.session_state.page = min(total_pages, st.session_state.page + 1)
        with page_col[4]:
            if st.button("▶▶", key="page_last"):
                st.session_state.page = total_pages
        with page_col[5]:
            st.session_state.page_size = st.selectbox(
                "表示件数",
                [25, 50, 100, 200],
                index=[25, 50, 100, 200].index(st.session_state.page_size),
                label_visibility="collapsed",
            )

        # 選択操作
        with st.expander("選択操作", expanded=False):
            cols = st.columns([1, 1, 0.3, 1.2, 1.2, 0.8])
            with cols[0]:
                if st.button("ページ全選択", key="page_select_all"):
                    for fn in filtered[start_idx:end_idx]:
                        st.session_state.selected_group[fn] = True
                        st.session_state[f"sel_{fn}"] = True
                    sync_group_to_paths()
                    st.rerun()
            with cols[1]:
                if st.button("ページ全解除", key="page_unselect_all"):
                    for fn in filtered[start_idx:end_idx]:
                        st.session_state.selected_group[fn] = False
                        st.session_state[f"sel_{fn}"] = False
                    sync_group_to_paths()
                    st.rerun()
            with cols[2]:
                st.write("")  # 区切り
            with cols[3]:
                if st.button("最新版を選択", key="all_select_latest", help="検索結果全体"):
                    for fn in filtered:
                        if st.session_state.versions_map.get(fn):
                            latest = st.session_state.versions_map[fn][0]
                            latest_subversions = st.session_state.subversions_map.get(
                                fn, {}
                            ).get(latest, ["-"])
                            select_version_for_file(fn, latest, latest_subversions[0])
                    sync_group_to_paths()
                    st.rerun()
            with cols[4]:
                if st.button("最古版を選択", key="all_select_oldest", help="検索結果全体"):
                    for fn in filtered:
                        if st.session_state.versions_map.get(fn):
                            oldest = st.session_state.versions_map[fn][-1]
                            oldest_subversions = st.session_state.subversions_map.get(
                                fn, {}
                            ).get(oldest, ["-"])
                            select_version_for_file(fn, oldest, oldest_subversions[-1])
                    sync_group_to_paths()
                    st.rerun()
            with cols[5]:
                if st.button("選択解除", key="all_unselect", help="検索結果全体"):
                    for fn in filtered:
                        st.session_state.selected_group[fn] = False
                        st.session_state[f"sel_{fn}"] = False
                    sync_group_to_paths()
                    st.rerun()

        st.divider()

        # チェックボックス用コールバック関数を生成
        def make_checkbox_callback(file_name):
            def callback():
                st.session_state.selected_group[file_name] = st.session_state[
                    f"sel_{file_name}"
                ]
                sync_group_to_paths()  # 内部で _tree_key_version をインクリメント

            return callback

        for fn in disp:
            versions = st.session_state.versions_map[fn]
            ver = st.session_state.selected_version[fn]

            # ★ 追加：サブバージョン（日付）の取得
            subversions = st.session_state.subversions_map.get(fn, {}).get(ver, ["-"])

            # サブバージョンの初期化
            ensure_subversion_initialized(fn, ver, subversions[0])
            subver = st.session_state.selected_subversion[fn][ver]

            # 現在のバージョンの index を取得
            try:
                ver_idx = versions.index(ver)
            except ValueError:
                ver_idx = 0
                st.session_state.selected_version[fn] = versions[0]

            # 現在のサブバージョンの index を取得
            try:
                subver_idx = subversions.index(subver)
            except ValueError:
                subver_idx = 0
                st.session_state.selected_subversion[fn][ver] = subversions[0]

            # エントリの取得（バージョン + サブバージョン）
            entry = st.session_state.ver_subver_to_entry_map[fn][ver][subver]

            row = st.columns([1, 3, 2, 2, 8])

            with row[0]:
                # ウィジェットキーの初期化（まだ存在しない場合のみ）
                if f"sel_{fn}" not in st.session_state:
                    st.session_state[f"sel_{fn}"] = st.session_state.selected_group.get(fn, False)
                st.checkbox(
                    "選択",
                    key=f"sel_{fn}",
                    on_change=make_checkbox_callback(fn),
                    label_visibility="collapsed",
                )

            with row[1]:
                st.write(fn)

            with row[2]:
                new_ver = st.selectbox(
                    "ver",
                    versions,
                    index=ver_idx,
                    key=f"ver_{fn}_v{st.session_state._group_ui_version}",
                    label_visibility="collapsed",
                )

                # ★ 修正：バージョンが変更された場合の処理
                if new_ver != ver:
                    st.session_state.selected_version[fn] = new_ver
                    new_subversions = st.session_state.subversions_map.get(fn, {}).get(
                        new_ver, ["-"]
                    )
                    # サブバージョンを強制的に最新に設定
                    if fn not in st.session_state.selected_subversion:
                        st.session_state.selected_subversion[fn] = {}
                    st.session_state.selected_subversion[fn][new_ver] = new_subversions[0]
                    sync_group_to_paths()
                    st.rerun()  # 画面を再描画してサブバージョンselectboxを更新
                else:
                    # 既に選択されているバージョンの場合は同期
                    st.session_state.selected_version[fn] = new_ver

            with row[3]:
                # ★ 修正：現在選択中のバージョンに基づいてサブバージョンを取得
                current_ver = st.session_state.selected_version[fn]
                current_subversions = st.session_state.subversions_map.get(fn, {}).get(
                    current_ver, ["-"]
                )

                # サブバージョンの初期化（現在のバージョン用）
                ensure_subversion_initialized(fn, current_ver, current_subversions[0])
                current_subver = st.session_state.selected_subversion[fn][current_ver]

                try:
                    current_subver_idx = current_subversions.index(current_subver)
                except ValueError:
                    current_subver_idx = 0
                    st.session_state.selected_subversion[fn][current_ver] = (
                        current_subversions[0]
                    )

                new_subver = st.selectbox(
                    "subver",
                    current_subversions,
                    index=current_subver_idx,
                    key=f"subver_{fn}_{current_ver}_v{st.session_state._group_ui_version}",
                    label_visibility="collapsed",
                )
                if new_subver != current_subver:
                    st.session_state.selected_subversion[fn][current_ver] = new_subver
                    sync_group_to_paths()

            with row[4]:
                # ★ 修正：現在選択中のバージョン・サブバージョンでエントリを取得
                display_ver = st.session_state.selected_version[fn]
                display_subver = st.session_state.selected_subversion.get(fn, {}).get(
                    display_ver, "-"
                )
                display_entry = st.session_state.ver_subver_to_entry_map[fn][display_ver][
                    display_subver
                ]
                st.code(display_entry["rel_path"], language="")
    else:
        st.info("検索を実行してください。")

# ---------- ツリービュー ----------
with tab_tree:
    if st.session_state.entries:
        # 選択数を先に表示（session_state から）
        selected_count_tree = len(st.session_state.selected_abs_paths)
        st.metric("選択中", selected_count_tree)

        # 選択中ファイル一覧
        if selected_count_tree > 0:
            with st.expander(f"選択中のファイル一覧 ({selected_count_tree}件)", expanded=False):
                for abs_path in sorted(st.session_state.selected_abs_paths):
                    st.text(abs_path)

        st.divider()

        # ツリー構造を構築
        with st.spinner("ツリー構造を構築中..."):
            nodes = build_tree_nodes(st.session_state.entries)

            # 有効なファイルパスのセットを作成（フォルダ除外用）
            valid_file_paths = {e["abs_path"] for e in st.session_state.entries}

        # バージョン番号付きの key を使用
        # グループビューから同期されると version がインクリメントされ、新しいコンポーネントが作成される
        tree_key = f"file_tree_v{st.session_state._tree_key_version}"

        result = tree_select(
            nodes,
            checked=list(st.session_state.selected_abs_paths),
            expanded=st.session_state.get("tree_expanded", []),
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

            # expanded 状態を更新
            st.session_state.tree_expanded = new_expanded

            # 選択状態が変わった場合、グループビューに同期して再描画
            if new_selected != st.session_state.selected_abs_paths:
                # 競合を解決（同じグループの複数バージョン選択を防止）
                resolved, removed_groups = resolve_version_conflict(
                    new_selected, st.session_state.selected_abs_paths
                )

                # 競合があった場合はトーストメッセージをキューに追加（rerun後に表示）
                for group in removed_groups:
                    st.session_state._pending_toasts.append(f"'{group}' は別バージョンに置き換えました")

                st.session_state.selected_abs_paths = resolved
                st.session_state._need_sync_to_group = True

                # 競合があった場合はツリーを再初期化（内部状態をリセット）
                if removed_groups:
                    st.session_state._tree_key_version += 1

                st.rerun()
    else:
        st.info("検索を実行してください。")
