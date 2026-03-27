"""Streamlit UI for running ② 解析と ③ 結果抽出を個別または連続実行するツール。"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import streamlit as st

import workflow


def _path_input(label: str, default: Path, key: str) -> Path:
    """Render a path text input and return a resolved Path."""

    default_value = st.session_state.get(key, str(default))
    text = st.text_input(label, value=default_value, key=key)
    return Path(text).expanduser().resolve()


def _discover_inp_files(jobs_dir: Path) -> List[Path]:
    """Return sorted .inp files if the folder exists."""

    if not jobs_dir.exists():
        return []
    return sorted(jobs_dir.glob("*.inp"))


def _select_targets(jobs_dir: Path) -> List[str]:
    """Show available .inp files and return user selections."""

    available = _discover_inp_files(jobs_dir)
    if not available:
        st.info("指定フォルダに .inp ファイルが見つかりません。")
        return []

    labels = [path.name for path in available]
    selected = st.multiselect("対象 .inp", labels, default=labels)
    return selected


def _render_command_inputs():
    st.subheader("(B) コマンド設定")
    abaqus_template = st.text_input(
        "② 解析コマンドテンプレート",
        value=st.session_state.get(
            "abaqus_template", "abaqus job={job_name} input={inp_path}"
        ),
        help="{job_name} と {inp_path} を使ってコマンドを構成します。",
    )
    st.session_state["abaqus_template"] = abaqus_template

    extract_template = st.text_input(
        "③ 抽出コマンドテンプレート",
        value=st.session_state.get(
            "extract_template",
            "python extract_results.py --odb {odb_path} --out {results_dir}",
        ),
        help="{job_name} / {odb_path} / {results_dir} を使用して抽出コマンドを作成します。",
    )
    st.session_state["extract_template"] = extract_template

    return abaqus_template, extract_template


def _run_analysis(jobs_dir: Path, abaqus_template: str, targets: Iterable[str]):
    try:
        commands = workflow.run_analyses(jobs_dir, abaqus_template, targets)
    except Exception as exc:  # pragma: no cover - UI feedback
        st.error(f"② 解析に失敗しました: {exc}")
        return
    st.success(f"② 解析を {len(commands)} 件実行しました。")
    st.code("\n".join(" ".join(cmd) for cmd in commands))


def _run_extraction(
    jobs_dir: Path,
    results_dir: Path,
    extract_template: str,
    targets: Iterable[str],
):
    try:
        commands = workflow.run_extractions(
            jobs_dir, results_dir, extract_template, targets
        )
    except Exception as exc:  # pragma: no cover - UI feedback
        st.error(f"③ 抽出に失敗しました: {exc}")
        return
    st.success(f"③ 抽出を {len(commands)} 件実行しました。")
    st.code("\n".join(" ".join(cmd) for cmd in commands))


def _run_analysis_and_extraction(
    jobs_dir: Path,
    results_dir: Path,
    abaqus_template: str,
    extract_template: str,
    targets: Iterable[str],
):
    try:
        result = workflow.run_analysis_and_extraction(
            jobs_dir=jobs_dir,
            results_dir=results_dir,
            abaqus_template=abaqus_template,
            extract_template=extract_template,
            targets=targets,
        )
    except Exception as exc:  # pragma: no cover - UI feedback
        st.error(f"②→③ 一括実行に失敗しました: {exc}")
        return
    st.success(
        f"② 解析 {len(result['analysis'])} 件 → ③ 抽出 {len(result['extraction'])} 件を完了しました。"
    )
    st.code("\n".join(" ".join(cmd) for cmd in result["analysis"]))
    st.code("\n".join(" ".join(cmd) for cmd in result["extraction"]))


def main() -> None:
    st.set_page_config(page_title="②→③ 一括実行", layout="wide")
    st.title("Abaqus ② 解析 & ③ 抽出 実行ツール")
    st.caption("② と ③ を個別または一括で実行できます。① は対象外です。")

    st.subheader("(A) 対象フォルダ設定")
    jobs_dir = _path_input(".inp フォルダ", Path("jobs"), "jobs_dir")
    results_dir = _path_input("抽出結果フォルダ", Path("extracts"), "results_dir")
    targets = _select_targets(jobs_dir)

    abaqus_template, extract_template = _render_command_inputs()

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("② 解析のみ実行", use_container_width=True):
            _run_analysis(jobs_dir, abaqus_template, targets or None)
    with col2:
        if st.button("③ 抽出のみ実行", use_container_width=True):
            _run_extraction(jobs_dir, results_dir, extract_template, targets or None)
    with col3:
        if st.button("②→③ 一括実行", type="primary", use_container_width=True):
            _run_analysis_and_extraction(
                jobs_dir, results_dir, abaqus_template, extract_template, targets or None
            )


if __name__ == "__main__":
    main()
