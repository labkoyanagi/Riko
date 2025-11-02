"""Streamlit app for generating multiple Abaqus .inp files from a template."""

from __future__ import annotations

import hashlib
import itertools
import re
import tempfile
from pathlib import Path

import streamlit as st


def init_session_state() -> None:
    """Initialise the counters used for dynamic text areas."""

    if "target_count" not in st.session_state:
        st.session_state.target_count = 1

    if "replacement_count" not in st.session_state:
        st.session_state.replacement_count = {0: 1}


def store_uploaded_template(uploaded_file) -> None:
    """Store the uploaded template content and its temporary path."""

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

    temp_dir = Path(tempfile.mkdtemp(prefix="inp_template_"))
    template_path = temp_dir / uploaded_file.name
    template_path.write_bytes(file_bytes)

    st.session_state.template_content = template_text
    st.session_state.template_path = str(template_path)
    st.session_state.template_name = uploaded_file.name
    st.session_state.template_hash = file_hash
    st.session_state.template_encoding = encoding


def ensure_replacement_counter(target_index: int) -> None:
    """Guarantee that a replacement counter exists for the given target."""

    if target_index not in st.session_state.replacement_count:
        st.session_state.replacement_count[target_index] = 1


def render_target_inputs():
    """Render the text areas used for collecting target strings."""

    st.subheader("(Ⅱ) 置換対象の入力")
    search_mode = st.radio(
        "検索方式",
        ["完全一致", "部分一致"],
        index=0,
        key="search_mode_radio",
    )
    st.session_state.search_mode = search_mode

    for target_index in range(st.session_state.target_count):
        st.text_area(
            f"置換対象テキスト ({target_index + 1})",
            key=f"target_text_{target_index}",
            height=180,
        )
        ensure_replacement_counter(target_index)

        render_replacement_inputs(target_index)

    if st.button("置換対象を追加", key="add_target_button"):
        new_index = st.session_state.target_count
        st.session_state.target_count += 1
        st.session_state.replacement_count[new_index] = 1
        st.rerun()


def render_replacement_inputs(target_index: int) -> None:
    """Render the replacement candidates for a specific target."""

    st.markdown(f"**(Ⅲ) 置換後テキスト ({target_index + 1})**")
    candidate_total = st.session_state.replacement_count[target_index]

    for candidate_index in range(candidate_total):
        st.text_area(
            f"候補 {target_index + 1}-{candidate_index + 1}",
            key=f"replacement_text_{target_index}_{candidate_index}",
            height=180,
        )

    if st.button("追加", key=f"add_replacement_button_{target_index}"):
        st.session_state.replacement_count[target_index] = candidate_total + 1
        st.rerun()


def collect_targets_and_replacements():
    """Collect target strings and their replacement candidates from session state."""

    targets = []

    for target_index in range(st.session_state.target_count):
        target_text = st.session_state.get(f"target_text_{target_index}", "").strip()
        if not target_text:
            continue

        candidate_count = st.session_state.replacement_count.get(target_index, 0)
        replacements = []
        for candidate_index in range(candidate_count):
            replacement_text = st.session_state.get(
                f"replacement_text_{target_index}_{candidate_index}", ""
            ).strip()
            if replacement_text:
                replacements.append(
                    {
                        "index": candidate_index + 1,
                        "text": replacement_text,
                    }
                )

        if replacements:
            targets.append(
                {
                    "target_index": target_index + 1,
                    "text": target_text,
                    "replacements": replacements,
                }
            )

    return targets


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


def main():
    st.set_page_config(page_title="Abaqus inp generator", layout="wide")
    st.title("Abaqus .inp 自動生成アプリ")

    init_session_state()

    st.subheader("(Ⅰ) テンプレートとなる inp ファイルのアップロード")
    uploaded_file = st.file_uploader("テンプレート .inp ファイルを選択", type=["inp"])

    if uploaded_file is not None:
        store_uploaded_template(uploaded_file)
        st.success(f"テンプレート '{uploaded_file.name}' を読み込みました。")

    if "template_content" not in st.session_state:
        st.info("テンプレートファイルをアップロードしてください。")
        return

    render_target_inputs()

    targets = collect_targets_and_replacements()
    combinations = build_combinations(targets)

    st.subheader("(Ⅳ) 置換の組み合わせ設定と出力")

    if not combinations:
        st.info("置換対象と置換後テキストを入力してください。")
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
        return

    if st.button("Generate inputs", type="primary"):
        output_directory = Path(st.session_state.template_path).parent
        successful, skipped = generate_files(
            selected_combinations,
            st.session_state.template_content,
            output_directory,
            st.session_state.search_mode,
            st.session_state.get("template_encoding", "utf-8"),
        )

        if successful:
            st.success(f"{successful} 個のファイルを出力しました。出力先: {output_directory}")
        if skipped:
            st.warning(
                "置換対象が見つからなかったため、以下の組み合わせをスキップしました: "
                + ", ".join(skipped)
            )


if __name__ == "__main__":
    main()

