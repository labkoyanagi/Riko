# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:nomarker
#     text_representation:
#       extension: .py
#       format_name: nomarker
#       format_version: '1.0'
#     jupytext_version: 1.17.0
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---
"""Streamlit app for generating multiple Abaqus .inp files from a template."""
from __future__ import annotations

import hashlib
import itertools
import re
from pathlib import Path

import streamlit as st

GENERATOR_CONTEXT_KEY = "generator_context"

SCRIPT_DIR = Path(__file__).resolve().parent
_candidate_root = SCRIPT_DIR.parent
if (_candidate_root / "jobs").exists() or (_candidate_root / "results").exists():
    APP_ROOT = _candidate_root
else:
    APP_ROOT = SCRIPT_DIR

DEFAULT_OUTPUT_DIR = (APP_ROOT / "jobs").resolve()


def init_session_state() -> None:
    """Initialise the structures used for dynamic text areas."""
    if "target_ids" not in st.session_state:
        st.session_state.target_ids = [0]
        st.session_state.next_target_id = 1
    if "replacement_ids" not in st.session_state:
        st.session_state.replacement_ids = {0: [0]}
    if "next_replacement_id" not in st.session_state:
        st.session_state.next_replacement_id = {0: 1}

    # Clean up legacy state keys from earlier versions.
    st.session_state.pop("target_count", None)
    st.session_state.pop("replacement_count", None)


def add_target() -> None:
    """Append a new target entry with an initial replacement slot."""
    new_id = st.session_state.next_target_id
    st.session_state.next_target_id += 1

    st.session_state.target_ids.append(new_id)
    st.session_state.replacement_ids[new_id] = [0]
    st.session_state.next_replacement_id[new_id] = 1


def remove_target(target_id: int) -> None:
    """Remove an existing target and its associated replacements."""
    if len(st.session_state.target_ids) <= 1:
        return

    st.session_state.target_ids = [
        tid for tid in st.session_state.target_ids if tid != target_id
    ]
    st.session_state.pop(f"target_text_{target_id}", None)

    for replacement_id in st.session_state.replacement_ids.get(target_id, []):
        st.session_state.pop(
            f"replacement_text_{target_id}_{replacement_id}", None
        )

    st.session_state.replacement_ids.pop(target_id, None)
    st.session_state.next_replacement_id.pop(target_id, None)
    st.rerun()


def add_replacement(target_id: int) -> None:
    """Append a replacement candidate for the given target."""
    next_id = st.session_state.next_replacement_id.get(target_id, 0)
    st.session_state.next_replacement_id[target_id] = next_id + 1
    st.session_state.replacement_ids.setdefault(target_id, []).append(next_id)


def remove_replacement(target_id: int, replacement_id: int) -> None:
    """Remove a replacement candidate if more than one exists."""
    candidates = st.session_state.replacement_ids.get(target_id, [])
    if len(candidates) <= 1:
        return

    remaining_ids = []
    remaining_texts = []
    for rid in candidates:
        key = f"replacement_text_{target_id}_{rid}"
        if rid == replacement_id:
            st.session_state.pop(key, None)
            continue
        remaining_ids.append(rid)
        remaining_texts.append(st.session_state.get(key, ""))
        st.session_state.pop(key, None)

    new_ids = list(range(len(remaining_ids)))
    st.session_state.replacement_ids[target_id] = new_ids
    st.session_state.next_replacement_id[target_id] = len(new_ids)
    for new_id, text in zip(new_ids, remaining_texts):
        st.session_state[f"replacement_text_{target_id}_{new_id}"] = text
    st.rerun()


def store_uploaded_template(uploaded_file, output_directory: Path) -> None:
    """Store the uploaded template content in the chosen output folder."""
    file_bytes = uploaded_file.getvalue()
    file_hash = hashlib.md5(file_bytes).hexdigest()
    if st.session_state.get("template_hash") == file_hash:
        return

    try:
        template_text = file_bytes.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        template_text = file_bytes.decode("cp932", errors="replace")
        encoding = "cp932"

    output_directory.mkdir(parents=True, exist_ok=True)
    template_path = output_directory / uploaded_file.name
    template_path.write_bytes(file_bytes)

    newline = "\r\n" if "\r\n" in template_text else "\n"

    st.session_state.template_content = template_text
    st.session_state.template_path = str(template_path)
    st.session_state.template_name = uploaded_file.name
    st.session_state.template_hash = file_hash
    st.session_state.template_encoding = encoding
    st.session_state.template_newline = newline


def render_target_inputs() -> None:
    """Render the text areas used for collecting target strings."""
    st.subheader("(Ⅱ) 置換対象の入力")

    search_mode = st.radio(
        "検索方式",
        ["完全一致", "部分一致"],
        index=0,
        key="search_mode_radio",
    )
    st.session_state.search_mode = search_mode

    for display_index, target_id in enumerate(st.session_state.target_ids, start=1):
        cols = st.columns([3, 1])
        with cols[0]:
            st.text_area(
                f"置換対象テキスト ({display_index})",
                key=f"target_text_{target_id}",
                height=180,
            )
        with cols[1]:
            if len(st.session_state.target_ids) > 1:
                if st.button(
                    "削除",
                    key=f"remove_target_button_{target_id}",
                ):
                    remove_target(target_id)
        render_replacement_inputs(target_id, display_index)

    if st.button("置換対象を追加", key="add_target_button"):
        add_target()
        st.rerun()


def render_replacement_inputs(target_id: int, target_display_index: int) -> None:
    """Render the replacement candidates for a specific target."""
    st.markdown(f"**(Ⅲ) 置換後テキスト ({target_display_index})**")
    candidate_ids = st.session_state.replacement_ids.get(target_id, [0])
    for display_index, candidate_id in enumerate(candidate_ids, start=1):
        cols = st.columns([3, 1])
        with cols[0]:
            st.text_area(
                f"候補 {target_display_index}-{display_index}",
                key=f"replacement_text_{target_id}_{candidate_id}",
                height=180,
            )
        with cols[1]:
            if len(candidate_ids) > 1:
                if st.button(
                    "削除",
                    key=f"remove_replacement_button_{target_id}_{candidate_id}",
                ):
                    remove_replacement(target_id, candidate_id)

    if st.button("追加", key=f"add_replacement_button_{target_id}"):
        add_replacement(target_id)
        st.rerun()


def normalise_newlines(text: str) -> str:
    """Match pasted text newlines to the uploaded template."""
    newline = st.session_state.get("template_newline", "\n")
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline == "\n":
        return unified
    return unified.replace("\n", newline)


def collect_targets_and_replacements():
    """Collect target strings and their replacement candidates from session state."""
    targets = []
    for target_display_index, target_id in enumerate(
        st.session_state.target_ids, start=1
    ):
        raw_target = st.session_state.get(f"target_text_{target_id}", "")
        if not raw_target.strip():
            continue
        target_text = normalise_newlines(raw_target)

        replacements = []
        candidate_ids = st.session_state.replacement_ids.get(target_id, [])
        for candidate_display_index, candidate_id in enumerate(
            candidate_ids, start=1
        ):
            raw_replacement = st.session_state.get(
                f"replacement_text_{target_id}_{candidate_id}", ""
            )
            if raw_replacement.strip():
                replacement_text = normalise_newlines(raw_replacement)
                replacements.append(
                    {
                        "index": candidate_display_index,
                        "text": replacement_text,
                    }
                )
        if replacements:
            targets.append(
                {
                    "target_index": target_display_index,
                    "text": target_text,
                    "replacements": replacements,
                }
            )
    return targets


def _get_output_directory() -> Path:
    default_value = st.session_state.get("generator_output_dir", str(DEFAULT_OUTPUT_DIR))
    text_value = st.text_input("出力先フォルダ (テンプレートと同じフォルダを指定)", value=default_value)
    output_directory = Path(text_value).expanduser()
    st.session_state.generator_output_dir = str(output_directory)
    return output_directory


def build_combinations(target_definitions):
    """Create the Cartesian product of all replacement candidates."""
    if not target_definitions:
        return []

    replacement_lists = [target["replacements"] for target in target_definitions]
    combinations = []
    for product in itertools.product(*replacement_lists):
        label = "-".join(str(item["index"]) for item in product)
        combinations.append(
            {
                "label": label,
                "pairs": [
                    {
                        "target": target_definitions[idx],
                        "replacement": product[idx],
                    }
                    for idx in range(len(product))
                ],
            }
        )
    return combinations


def combinations_to_table(combinations):
    """Convert combinations into a structure suitable for display."""
    rows = []
    for combo in combinations:
        row = {"組み合わせ番号": combo["label"]}
        for pair in combo["pairs"]:
            target_idx = pair["target"]["target_index"]
            row[f"対象({target_idx})"] = pair["target"]["text"]
            row[f"置換({target_idx})"] = pair["replacement"]["text"]
        rows.append(row)
    return rows


def apply_replacements(base_text: str, combo_pairs, search_mode: str):
    """Apply all replacements for a single combination."""
    updated_text = base_text
    counts = []
    for pair in combo_pairs:
        target_text = pair["target"]["text"]
        replacement_text = pair["replacement"]["text"]
        if search_mode == "完全一致":
            count = updated_text.count(target_text)
            updated_text = updated_text.replace(target_text, replacement_text)
        else:
            # Case-insensitive partial matching using regular expressions.
            pattern = re.compile(re.escape(target_text), re.IGNORECASE)
            updated_text, count = pattern.subn(replacement_text, updated_text)
        counts.append(count)
    return updated_text, counts


def generate_files(
    selected_combinations,
    template_content,
    output_directory,
    search_mode,
    encoding,
):
    """Generate .inp files for all selected combinations."""
    successful = 0
    skipped = []
    base_name = Path(st.session_state.template_name).stem

    for combo in selected_combinations:
        updated_text, counts = apply_replacements(
            template_content, combo["pairs"], search_mode
        )
        if any(count == 0 for count in counts):
            skipped.append(combo["label"])
            continue

        output_name = f"{base_name}_({combo['label']}).inp"
        output_path = output_directory / output_name
        output_path.write_text(updated_text, encoding=encoding)
        successful += 1

    return successful, skipped


def _set_generation_context(context: dict) -> None:
    st.session_state[GENERATOR_CONTEXT_KEY] = context


def get_generation_context() -> dict:
    return st.session_state.get(
        GENERATOR_CONTEXT_KEY,
        {"ready": False, "message": "UI がまだ初期化されていません。"},
    )


def run_generation_from_context(context: dict, *, show_messages: bool = False) -> dict:
    if not context.get("ready"):
        raise ValueError(context.get("message", "Generation step is not ready."))

    output_directory = Path(context["output_directory"])
    successful, skipped = generate_files(
        context["selected_combinations"],
        context["template_content"],
        output_directory,
        context["search_mode"],
        context["template_encoding"],
    )
    result = {
        "successful": successful,
        "skipped": skipped,
        "output_directory": output_directory,
        "combo_labels": context.get("combo_labels", []),
    }

    if show_messages:
        if successful:
            st.success(f"{successful} 個のファイルを出力しました。出力先: {output_directory}")
        if skipped:
            st.warning(
                "置換対象が見つからなかったため、以下の組み合わせをスキップしました: "
                + ", ".join(skipped)
            )
    return result


def run_app(within_dashboard: bool = False) -> None:
    """Render the template-based inp generator UI."""
    if not within_dashboard:
        st.set_page_config(page_title="Abaqus inp generator", layout="wide")
        st.title("Abaqus .inp 自動生成アプリ")
    else:
        st.header("Abaqus .inp 自動生成")

    init_session_state()
    _set_generation_context({"ready": False, "message": "テンプレートファイルをアップロードしてください。"})

    st.subheader("(Ⅰ) テンプレートとなる inp ファイルのアップロード")
    output_directory = _get_output_directory()
    uploaded_file = st.file_uploader("テンプレート .inp ファイルを選択", type=["inp"])
    if uploaded_file is not None:
        store_uploaded_template(uploaded_file, output_directory)
        st.success(
            f"テンプレート '{uploaded_file.name}' を読み込みました。"
            f" 出力先: {output_directory}"
        )

    if "template_content" not in st.session_state:
        st.info("テンプレートファイルをアップロードしてください。")
        _set_generation_context({"ready": False, "message": "テンプレートファイルをアップロードしてください。"})
        return

    render_target_inputs()
    targets = collect_targets_and_replacements()
    combinations = build_combinations(targets)

    st.subheader("(Ⅳ) 置換の組み合わせ設定と出力")
    if not combinations:
        st.info("置換対象と置換後テキストを入力してください。")
        _set_generation_context({"ready": False, "message": "置換対象と置換後テキストを入力してください。"})
        return

    table_rows = combinations_to_table(combinations)
    if table_rows:
        st.dataframe(table_rows, use_container_width=True)

    selected_combinations = []
    st.write("生成対象の組み合わせを選択してください。")
    for combo in combinations:
        key = f"combo_select_{combo['label']}"
        default_value = st.session_state.get(key, True)
        selected = st.checkbox(
            f"組み合わせ {combo['label']}",
            value=default_value,
            key=key,
        )
        if selected:
            selected_combinations.append(combo)

    if not selected_combinations:
        st.warning("少なくとも1つの組み合わせを選択してください。")
        _set_generation_context({"ready": False, "message": "組み合わせを少なくとも1つ選択してください。"})
        return

    context = {
        "ready": True,
        "message": "",
        "template_path": st.session_state.template_path,
        "template_content": st.session_state.template_content,
        "template_encoding": st.session_state.get("template_encoding", "utf-8"),
        "search_mode": st.session_state.search_mode,
        "selected_combinations": selected_combinations,
        "output_directory": str(output_directory.resolve()),
        "combo_labels": [combo["label"] for combo in selected_combinations],
    }
    _set_generation_context(context)

    if st.button("Generate inputs", type="primary"):
        run_generation_from_context(context, show_messages=True)


def main() -> None:
    run_app(within_dashboard=False)


if __name__ == "__main__":
    main()
