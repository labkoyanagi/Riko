"""Unified Streamlit application covering template generation, job execution, and post-processing."""
from __future__ import annotations

from pathlib import Path
import sys

import importlib
import streamlit as st

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _load_module(primary: str, fallback: str):
    """Import a module from either the root or legacy ``apps`` package."""
    for name in (primary, fallback):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError(f"Could not import {primary} or {fallback}.")


template_app = _load_module(
    "template_generator", "apps.template_generator"
)
runner_app = _load_module("job_runner_ui", "apps.job_runner_ui")
post_app = _load_module("postprocessor_ui", "apps.postprocessor_ui")

DEFAULT_JOBS_DIR = (ROOT_DIR / "jobs").resolve()
DEFAULT_RESULTS_DIR = (ROOT_DIR / "results").resolve()
DEFAULT_EXTRACTS_DIR = (ROOT_DIR / "extracts").resolve()


def _path_input(label: str, default: Path, key: str) -> Path:
    default_str = st.session_state.get(key, str(default))
    value = st.text_input(label, value=default_str, key=key)
    return Path(value).expanduser()


def _generator_output_dir_fallback() -> Path:
    """Use the generator's chosen output folder when available."""
    if "generator_output_dir" in st.session_state:
        return Path(st.session_state["generator_output_dir"]).expanduser()

    gen_context = template_app.get_generation_context()
    output_dir = gen_context.get("output_directory")
    if output_dir:
        return Path(output_dir)

    return DEFAULT_JOBS_DIR


def main() -> None:
    st.set_page_config(page_title="Abaqus Workflow Suite", layout="wide")
    st.title("Abaqus Workflow Suite")
    st.caption("テンプレート生成、バッチ実行、後処理を1つのアプリでまとめて実行できます。")

    with st.sidebar:
        st.header("グローバル設定")
        jobs_dir = _path_input("ジョブフォルダ", DEFAULT_JOBS_DIR, "global_jobs_dir")
        results_dir = _path_input("結果フォルダ", DEFAULT_RESULTS_DIR, "global_results_dir")
        extracts_dir = _path_input("抽出CSVフォルダ", DEFAULT_EXTRACTS_DIR, "global_extract_dir")
        abaqus_default = st.session_state.get("global_abaqus_command", "abaqus")
        abaqus_command = st.text_input("abaqus コマンド", value=abaqus_default, key="global_abaqus_command")
        st.caption("ここで設定した値は各タブの初期値として使用されます。")

    generator_tab, run_tab, post_tab = st.tabs([
        "① inp生成", "② 解析ジョブ実行", "③ ODB 応力抽出"
    ])

    with generator_tab:
        template_app.run_app(within_dashboard=True)

    with run_tab:
        runner_app.run_app(
            default_source_dir=_generator_output_dir_fallback(),
            default_results_dir=results_dir,
            default_abaqus_command=abaqus_command,
            within_dashboard=True,
        )

    with post_tab:
        runner_state_for_post = runner_app.get_job_runner_context()
        planned_inp_files = runner_state_for_post.get("selected_files", [])
        if not planned_inp_files:
            planned_inp_files = list(_generator_output_dir_fallback().glob("*.inp"))

        post_app.run_app(
            abaqus_command=abaqus_command,
            default_results_dir=results_dir,
            extract_root=extracts_dir,
            planned_inp_files=planned_inp_files,
            within_dashboard=True,
        )

    st.header("④ ②→③ の連続実行 (① は含みません)")
    runner_ctx = runner_app.get_job_runner_context()
    post_ctx = post_app.get_postprocessor_context()

    status_info = [
        ("② 解析ジョブ", runner_ctx),
        ("③ ODB 抽出", post_ctx),
    ]
    cols = st.columns(len(status_info))
    for col, (label, context) in zip(cols, status_info):
        ready = bool(context.get("ready"))
        value = "準備OK" if ready else "未準備"
        delta = "" if ready else context.get("message", "")
        col.metric(label, value, delta=delta)

    chain_ready = all(context.get("ready") for _, context in status_info)
    if not chain_ready:
        st.caption("② と ③ の設定がそろうと連続実行ボタンが有効になります。")

    if st.button("②→③ をまとめて実行", disabled=not chain_ready):
        selected_files = runner_ctx.get("selected_files", [])
        if not selected_files:
            st.error("② 解析の対象 .inp が選択されていません。")
            return

        try:
            with st.spinner("② 解析ジョブ実行中..."):
                runner_app.run_jobs_from_context(runner_ctx)
        except Exception as exc:
            st.error(f"② の実行に失敗しました: {exc}")
            return

        refreshed_jobs = post_app.build_planned_jobs(
            selected_files, results_dir
        )
        shared_targets = post_ctx.get("shared_targets") or []
        if not shared_targets:
            for targets in (post_ctx.get("job_targets") or {}).values():
                if targets:
                    shared_targets = targets
                    break

        def _clone_targets(targets):
            return [
                post_app.InstanceTargets(
                    instance=target.instance,
                    part=target.part,
                    element_labels=list(target.element_labels),
                )
                for target in targets
            ]

        filtered_targets = {
            job.name: _clone_targets(shared_targets)
            for job in refreshed_jobs
        }

        refreshed_post_context = {
            "ready": bool(refreshed_jobs),
            "message": "" if refreshed_jobs else "抽出対象が見つかりません。",
            "selected_jobs": refreshed_jobs,
            "job_targets": filtered_targets,
            "shared_targets": shared_targets,
            "component": post_ctx.get("component") or "S22",
            "step": post_ctx.get("step"),
            "abaqus_command": abaqus_command,
            "extract_dir": post_ctx.get("extract_dir", str(extracts_dir)),
        }
        post_app.set_postprocessor_context(refreshed_post_context)

        try:
            with st.spinner("③ ODB 応力抽出中..."):
                post_result = post_app.run_extraction_from_context(
                    refreshed_post_context
                )
        except Exception as exc:
            st.error(f"③ の実行に失敗しました: {exc}")
            return

        summary = post_result["summary"]
        summary_paths = post_result["summary_paths"]
        errors = post_result["errors"]
        component_max = post_result.get("component_max")

        if not summary.empty and summary_paths:
            primary_summary = summary_paths[0]
            location_text = ", ".join(str(path) for path in summary_paths)
            st.success(f"③: 抽出完了。サマリーファイル: {location_text}")
            st.dataframe(summary.head(100), use_container_width=True)
            csv_data = summary.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Summary CSV をダウンロード",
                data=csv_data,
                file_name=primary_summary.name,
                mime="text/csv",
                key="full_run_summary_download",
            )
            if component_max is not None and not component_max.empty:
                st.subheader(f"ジョブ別 {refreshed_post_context['component']} 最大値")
                st.dataframe(component_max, use_container_width=True)
        else:
            st.warning("③: 有効な抽出結果が得られませんでした。設定を確認してください。")

        if errors:
            st.error("\n".join(errors))


if __name__ == "__main__":
    main()
