import os
import re
import json
import shutil
import streamlit as st
from pathlib import Path

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
    "ウェハーアライメント",
    "0_フォーマット",

]
DEFAULT_INCLUDE_EXTS = [".docx", ".xlsx", ".xls", ".pptx", ".pdf", ".txt", ".md"]
DEFAULT_EXCLUDE_FILE_PATTERNS = [
    r"^~.*", 
    r".*コピー.*", 
    r".*copy.*",
]
VERSION_REGEX = re.compile(r"\b\d+[_\.]\d+(?:[_\.]\d+)*\b")
DATE_REGEX = re.compile(r"_(\d{8})(?=\.|$)")
DEFAULT_PAGE_SIZE = 50
MAX_HISTORY = 10
# fmt: on


# ========= Streamlit ページ設定 =========
st.set_page_config(page_title="ファイル検索・収集ツール", layout="wide")


# ========= セッション初期化 =========
def init_state():
    defaults = {
        "entries": [],
        "groups": {},
        "versions_map": {},
        "ver_to_entry_map": {},
        "ver_subver_to_entry_map": {},  # ← 追加
        "subversions_map": {},  # ← 追加
        "selected_version": {},
        "selected_subversion": {},  # ← 追加
        "selected_group": {},
        "page": 1,
        "page_size": DEFAULT_PAGE_SIZE,
        "filter_text": "",
        "search_path": DEFAULT_SEARCH_PATH,
        "dest_path": "",
        "exclude_dirs": DEFAULT_EXCLUDE_DIRS.copy(),
        "include_exts": DEFAULT_INCLUDE_EXTS.copy(),
        "exclude_file_patterns": DEFAULT_EXCLUDE_FILE_PATTERNS.copy(),
        "search_history": [],
        "dest_history": [],
        # ウィジェットキーのデフォルト値
        "_search_path_input": str(DEFAULT_SEARCH_PATH),
        "_dest_path_input": "",
        "_filter_text_input": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()


# ========= 設定保存 =========
def save_config():
    data = {
        "search_path": st.session_state.search_path,
        "dest_path": st.session_state.dest_path,
        "exclude_dirs": list(st.session_state.exclude_dirs),
        "include_exts": list(st.session_state.include_exts),
        "exclude_file_patterns": list(st.session_state.exclude_file_patterns),
        "search_history": list(st.session_state.search_history),
        "dest_history": list(st.session_state.dest_history),
        "selected_group": st.session_state.selected_group,
        "selected_version": st.session_state.selected_version,
        "selected_subversion": st.session_state.selected_subversion,  # ← 追加
        "filter_text": st.session_state.filter_text,
        "page": st.session_state.page,
        "page_size": st.session_state.page_size,
    }

    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ========= 設定ロード =========
def load_config():
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
        "selected_subversion",  # ← 追加
        "filter_text",
        "page",
        "page_size",
    ]:
        if k in data:
            st.session_state[k] = data[k]

    # ウィジェットキーも更新（テキスト入力欄に反映させるため）
    if "search_path" in data:
        st.session_state._search_path_input = data["search_path"]
    if "dest_path" in data:
        st.session_state._dest_path_input = data["dest_path"]
    if "filter_text" in data:
        st.session_state._filter_text_input = data["filter_text"]

    return True


# ========= 履歴追加ユーティリティ =========
def push_history(lst, v):
    if not v:
        return lst
    new = [v] + [x for x in lst if x != v]
    return new[:MAX_HISTORY]


# ========= 検索結果のセッション保存 =========
def store_search_results(entries, groups, versions_map, ver_to_entry_map,
                         ver_subver_to_entry_map, subversions_map):
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
        if re.fullmatch(r"\d+[_\.]\d+(?:[_\.]\d+)*", part):
            return part.replace(".", "_")
    return "-"


def get_group_key(rel_path: str):
    """ファイルのグループキーを生成（バージョンフォルダと日付を除去）"""
    parts = rel_path.split(os.sep)
    # バージョンフォルダを除去（ファイル名以外のパーツから）
    non_version_parts = [
        p for p in parts[:-1]
        if not re.fullmatch(r"\d+[_\.]\d+(?:[_\.]\d+)*", p)
    ]
    # ファイル名から日付を除去
    filename = parts[-1]
    base_filename = re.sub(r"_\d{8}(?=\.\w+$)", "", filename)
    # グループキーを構築
    if non_version_parts:
        return os.path.join(*non_version_parts, base_filename)
    return base_filename


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
            date_match = re.search(r"_(\d{8})(?=\.\w+$)", fn)
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

        # グループのアイテムをソート
        items.sort(
            key=lambda x: (
                version_key(x["version"]),
                (
                    "",
                    (
                        re.search(r"_(\d{8})(?=\.\w+$)", x["file_name"]).group(1)
                        if re.search(r"_(\d{8})(?=\.\w+$)", x["file_name"])
                        else "-"
                    ),
                ),
            ),
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
st.title("ファイル検索・収集ツール")

# ---------- 設定保存・ロード ----------
with st.expander("設定の保存／ロード", expanded=False):

    c1, c2, c3 = st.columns(3)

    with c1:
        if st.button("設定を保存する"):
            save_config()
            st.success("設定を保存しました。")

    with c2:
        if st.button("設定をロードする"):
            ok = load_config()
            if ok:
                st.success("設定をロードしました。続けて検索を自動実行します。")

                if st.session_state.search_path and os.path.isdir(
                    st.session_state.search_path
                ):
                    ex_dirs = normalize_exclude_dirs(st.session_state.exclude_dirs)
                    inc_exts = normalize_include_exts(st.session_state.include_exts)
                    ex_patterns = st.session_state.exclude_file_patterns

                    entries = search_files(
                        st.session_state.search_path, ex_dirs, inc_exts, ex_patterns
                    )
                    (
                        groups,
                        versions_map,
                        ver_to_entry_map,
                        ver_subver_to_entry_map,
                        subversions_map,
                    ) = build_group_struct(entries)

                    store_search_results(
                        entries, groups, versions_map, ver_to_entry_map,
                        ver_subver_to_entry_map, subversions_map
                    )

                    # 選択状態を新しいグループに基づいてマージ
                    # 保存された選択状態のうち、現在のグループに存在するもののみ反映
                    old_selected = st.session_state.selected_group.copy()
                    st.session_state.selected_group = {
                        fn: old_selected.get(fn, False) for fn in groups
                    }

                    for fn in groups:
                        if fn not in st.session_state.selected_version:
                            latest_ver = versions_map[fn][0]
                            st.session_state.selected_version[fn] = latest_ver
                            st.session_state.selected_subversion[fn] = subversions_map[
                                fn
                            ][latest_ver][0]

                    st.info(f"再検索しました（{len(entries)} 件）")

    with c3:
        if st.button("キャッシュをクリア"):
            st.cache_data.clear()
            st.info("キャッシュをクリアしました。再検索してください。")


# ---------- 検索条件 ----------
with st.expander("検索条件", expanded=True):

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
            "最近使った検索フォルダ",
            ["- 選択 -"] + st.session_state.search_history,
            key="_search_history_select",
            on_change=on_search_history_change,
        )

    exclude_dirs_input = st.text_input(
        "除外フォルダ名（カンマ区切り）",
        value=", ".join(s.strip() for s in st.session_state.exclude_dirs if s.strip()),
    )
    st.session_state.exclude_dirs = [
        s.strip() for s in exclude_dirs_input.split(",") if s.strip()
    ]

    include_exts_input = st.text_input(
        "対象拡張子（カンマ区切り、空欄=すべて）",
        value=", ".join(s.strip() for s in st.session_state.include_exts if s.strip()),
    )
    st.session_state.include_exts = [
        s.strip() for s in include_exts_input.split(",") if s.strip()
    ]

    st.session_state.exclude_file_patterns = st.text_area(
        "除外ファイル名パターン（正規表現、1行1パターン）",
        value="\n".join(st.session_state.exclude_file_patterns),
        height=100,
        help="例: ^~.* (チルダで始まる), .*コピー.* (コピーを含む)",
    ).split("\n")

    col = st.columns([1, 1, 5])
    with col[0]:
        start_search = st.button("ファイルを検索", type="primary")
    with col[1]:
        clear_state = st.button("結果クリア")

    if start_search:
        path = st.session_state.search_path
        if not path or not os.path.isdir(path):
            st.error("検索フォルダが不正です。")
        else:
            ex_dirs = normalize_exclude_dirs(st.session_state.exclude_dirs)
            inc_exts = normalize_include_exts(st.session_state.include_exts)
            ex_patterns = st.session_state.exclude_file_patterns

            entries = search_files(path, ex_dirs, inc_exts, ex_patterns)
            (
                groups,
                versions_map,
                ver_to_entry_map,
                ver_subver_to_entry_map,
                subversions_map,
            ) = build_group_struct(entries)

            store_search_results(
                entries, groups, versions_map, ver_to_entry_map,
                ver_subver_to_entry_map, subversions_map
            )
            st.session_state.selected_group = {fn: False for fn in groups}
            st.session_state.selected_version = {
                fn: versions_map[fn][0] for fn in versions_map
            }
            st.session_state.selected_subversion = {}  # ← 追加
            st.session_state.page = 1

            st.session_state.search_history = push_history(
                st.session_state.search_history, path
            )

            st.success(f"{len(entries)} 件を検出しました。")

    if clear_state:
        clear_search_results()
        st.info("クリアしました。")

# ---------- 検索結果 ----------
if st.session_state.groups:

    total_files = len(st.session_state.entries)
    total_groups = len(st.session_state.groups)

    # selected_group から選択数を計算
    selected_count = sum(1 for v in st.session_state.selected_group.values() if v)

    m0, m1, m2 = st.columns(3)
    m0.metric("見つかったファイル数", total_files)
    m1.metric("ファイル名グループ数", total_groups)
    m2.metric("選択中", selected_count)

    st.text_input(
        "ファイル名フィルタ（部分一致）",
        key="_filter_text_input",
        on_change=lambda: setattr(
            st.session_state, "filter_text", st.session_state._filter_text_input
        ),
    )

    filtered = [
        fn
        for fn in st.session_state.groups
        if st.session_state.filter_text.lower() in fn.lower()
    ]

    st.session_state.page_size = st.selectbox(
        "ページサイズ",
        [25, 50, 100, 200],
        index=[25, 50, 100, 200].index(st.session_state.page_size),
    )

    # ---
    total_pages = max(
        1,
        (len(filtered) + st.session_state.page_size - 1) // st.session_state.page_size,
    )

    # ページ番号がtotal_pagesを超えないように制限（フィルタ適用で件数が減った場合の対策）
    if st.session_state.page > total_pages:
        st.session_state.page = total_pages

    # ナビゲーションボタン
    nav = st.columns([1, 1, 2, 1, 1])

    with nav[0]:
        if st.button("◀◀ 最初", key="page_first"):
            st.session_state.page = 1

    with nav[1]:
        if st.button("◀ 前へ", key="page_prev"):
            st.session_state.page = max(1, st.session_state.page - 1)

    with nav[2]:

        def on_page_change():
            st.session_state.page = st.session_state.page_input_widget

        st.number_input(
            "ページジャンプ",
            min_value=1,
            max_value=total_pages,
            value=st.session_state.page,
            step=1,
            key="page_input_widget",
            on_change=on_page_change,
            help="ページ番号を入力してジャンプ",
        )

    with nav[3]:
        if st.button("次へ ▶", key="page_next"):
            st.session_state.page = min(total_pages, st.session_state.page + 1)

    with nav[4]:
        if st.button("最後 ▶▶", key="page_last"):
            st.session_state.page = total_pages

    # ★ ページ番号表示をボタンの後に配置
    st.write(f"**現在のページ: {st.session_state.page} / {total_pages}**")

    # ---
    start_idx = (st.session_state.page - 1) * st.session_state.page_size
    end_idx = start_idx + st.session_state.page_size
    disp = filtered[start_idx:end_idx]

    # ---------- 操作 ----------
    st.subheader("選択操作")

    with st.expander("ページ単位の操作（今表示されているページのみ）", expanded=False):
        c = st.columns([1, 1])

        with c[0]:
            if st.button("ページ全選択", key="page_select_all"):
                for fn in filtered[start_idx:end_idx]:
                    st.session_state.selected_group[fn] = True
                    st.session_state[f"sel_{fn}"] = True
                st.rerun()

        with c[1]:
            if st.button("ページ全解除", key="page_unselect_all"):
                for fn in filtered[start_idx:end_idx]:
                    st.session_state.selected_group[fn] = False
                    st.session_state[f"sel_{fn}"] = False
                st.rerun()

    with st.expander("全体操作（検索結果すべて）", expanded=False):
        c2 = st.columns([1, 1, 1])  # ← 3列に変更

        # 全体：最新版を選択
        with c2[0]:
            if st.button("全体：最新版を選択", key="all_select_latest"):
                for fn in filtered:
                    if st.session_state.versions_map.get(fn):
                        latest = st.session_state.versions_map[fn][0]
                        latest_subversions = st.session_state.subversions_map.get(
                            fn, {}
                        ).get(latest, ["-"])
                        select_version_for_file(fn, latest, latest_subversions[0])

                st.rerun()

        # 全体：最古版を選択
        with c2[1]:
            if st.button("全体：最古版を選択", key="all_select_oldest"):
                for fn in filtered:
                    if st.session_state.versions_map.get(fn):
                        oldest = st.session_state.versions_map[fn][-1]
                        oldest_subversions = st.session_state.subversions_map.get(
                            fn, {}
                        ).get(oldest, ["-"])
                        select_version_for_file(fn, oldest, oldest_subversions[-1])

                st.rerun()

        # ★ 追加：全体：選択解除
        with c2[2]:
            if st.button("全体：選択解除", key="all_unselect"):
                for fn in filtered:
                    st.session_state.selected_group[fn] = False
                    st.session_state[f"sel_{fn}"] = False

                st.rerun()

    st.divider()

    # チェックボックス用コールバック関数を生成
    def make_checkbox_callback(file_name):
        def callback():
            st.session_state.selected_group[file_name] = st.session_state[
                f"sel_{file_name}"
            ]

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
                key=f"ver_{fn}",
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
                key=f"subver_{fn}_{current_ver}",
                label_visibility="collapsed",
            )
            st.session_state.selected_subversion[fn][current_ver] = new_subver

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

# ---------- 保存 ----------
st.subheader("ファイルの収集・保存")

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
        "最近使った保存先フォルダ",
        ["- 選択 -"] + st.session_state.dest_history,
        key="_dest_history_select",
        on_change=on_dest_history_change,
    )


if st.button("ファイルを保存する", type="primary"):

    dest = st.session_state.dest_path
    if not dest:
        st.error("保存先フォルダを指定してください。")
    else:
        os.makedirs(dest, exist_ok=True)

        targets = []
        for fn, sel in st.session_state.selected_group.items():
            if not sel:
                continue
            ver = st.session_state.selected_version[fn]
            subver = st.session_state.selected_subversion.get(fn, {}).get(ver, "-")

            # ★ 変更：バージョン + サブバージョンでエントリを取得
            entry = st.session_state.ver_subver_to_entry_map[fn][ver][subver]
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

            st.success(f"{len(targets)} 件コピーしました。")
