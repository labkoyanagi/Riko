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
"""Streamlit app for extracting element stress histories directly from Abaqus ODB files."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import streamlit as st

SCRIPT_DIR = Path(__file__).resolve().parent
_candidate_root = SCRIPT_DIR.parent
if any(((_candidate_root / name).exists() for name in ("results", "extracts"))):
    BASE_ROOT = _candidate_root
else:
    BASE_ROOT = SCRIPT_DIR
APP_ROOT = SCRIPT_DIR
HELPER_PATH = (SCRIPT_DIR / "odb_extract_tool.py").resolve()
DEFAULT_RESULTS_DIR = (BASE_ROOT / "results").resolve()
DEFAULT_EXTRACT_ROOT = (BASE_ROOT / "extracts").resolve()

POSTPROCESSOR_CONTEXT_KEY = "postprocessor_context"


@dataclass
class JobRecord:
    name: str
    odb_path: Path
    source: str = "filesystem"
    exists: bool = False


@dataclass
class InstanceTargets:
    instance: str
    part: Optional[str] = None
    element_labels: List[int] = field(default_factory=list)


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def discover_jobs(base_dir: Path) -> List[JobRecord]:
    jobs: List[JobRecord] = []
    if not base_dir.exists():
        return jobs
    for odb_path in sorted(base_dir.glob("**/*.odb")):
        if odb_path.is_dir():
            continue
        resolved = odb_path.resolve()
        jobs.append(
            JobRecord(
                name=resolved.stem,
                odb_path=resolved,
                source="filesystem",
                exists=True,
            )
        )
    return jobs


def build_planned_jobs(
    planned_inp_files: Iterable[Path],
    results_dir: Path,
) -> List[JobRecord]:
    records: List[JobRecord] = []
    for inp_path in planned_inp_files:
        job_name = Path(inp_path).stem
        if not job_name:
            continue

        candidates = [
            Path(inp_path).with_suffix(".odb"),
            results_dir / job_name / f"{job_name}.odb",
            results_dir / f"{job_name}.odb",
        ]
        chosen = None
        for candidate in candidates:
            resolved = candidate.expanduser().resolve()
            if resolved.exists():
                chosen = JobRecord(name=job_name, odb_path=resolved, source="planned", exists=True)
                break
        if chosen is None:
            chosen = JobRecord(name=job_name, odb_path=resolved, source="planned", exists=False)
        if chosen:
            records.append(chosen)
    return records


def sanitize_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value)


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def parse_element_text(text: str) -> List[int]:
    labels: List[int] = []
    for raw in text.replace("\n", " ").replace("\t", " ").split():
        token = raw.strip().strip(",")
        if not token:
            continue
        try:
            labels.append(int(token))
        except ValueError:
            continue
    return labels


def build_command(base_command: str, extra: List[str]) -> List[str]:
    return [base_command] + ["python", str(HELPER_PATH)] + extra


@st.cache_data(show_spinner=False)
def load_instance_metadata(odb_path: str, abaqus_command: str, mtime: float) -> List[Dict[str, object]]:
    command = build_command(
        abaqus_command,
        ["--mode", "inspect", "--odb", odb_path],
    )
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as exc:  # pragma: no cover - depends on Abaqus
        raise RuntimeError(f"abaqus コマンドが見つかりません: {abaqus_command}") from exc

    if result.returncode != 0:
        raise RuntimeError(result.stderr or "Failed to inspect ODB.")

    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to decode inspector output: {exc}\n{result.stdout}") from exc

    return payload.get("instances", [])


def _resolve_abaqus_command(command: str) -> Optional[str]:
    """Return a usable abaqus command or ``None`` if not found."""
    candidate = Path(command)
    if candidate.exists():
        return str(candidate)
    resolved = shutil.which(command)
    return resolved


def run_extraction(
    job: JobRecord,
    component: str,
    targets: List[InstanceTargets],
    abaqus_command: str,
    step: Optional[str],
    extract_root: Path,
) -> pd.DataFrame:
    ensure_directory(extract_root)
    temp_name = f"_tmp_{sanitize_token(job.name)}_{uuid.uuid4().hex}.csv"
    temp_path = (extract_root / temp_name).resolve()

    arguments = [
        "--mode", "extract",
        "--odb", str(job.odb_path.resolve()),
        "--job", job.name,
        "--out", str(temp_path),
        "--component", component,
    ]
    if step:
        arguments.extend(["--step", step])

    for target in targets:
        if target.element_labels:
            label_text = " ".join(str(num) for num in sorted(set(target.element_labels)))
            prefix = target.instance
            if target.part:
                prefix = f"{prefix}|{target.part}"
            arguments.extend(["--target", f"{prefix}:{label_text}"])

    command = build_command(abaqus_command, arguments)
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as exc:  # pragma: no cover - depends on Abaqus
        if temp_path.exists():
            temp_path.unlink()
        raise RuntimeError(f"abaqus コマンドが見つかりません: {abaqus_command}") from exc

    if result.returncode != 0:
        if temp_path.exists():
            temp_path.unlink()
        err = result.stderr.strip() or result.stdout.strip() or "Abaqus extraction failed."
        raise RuntimeError(err)

    if not temp_path.exists():
        err = result.stderr.strip() or result.stdout.strip() or "Helper finished without creating output CSV."
        raise RuntimeError(
            "抽出ヘルパが出力CSVを生成しなかった。\n"
            f"コマンド: {command}\n"
            f"OUT: {result.stdout}\nERR: {err}"
        )

    try:
        frame = pd.read_csv(temp_path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
    return frame


def compute_component_max(summary: pd.DataFrame, component: str) -> pd.DataFrame:
    """Return per-job maximum values for the selected stress component.

    Non-numeric values are ignored by coercing to ``NaN`` before grouping.
    """
    if summary.empty or component not in summary.columns:
        return pd.DataFrame()

    numeric_values = pd.to_numeric(summary[component], errors="coerce")
    max_series = numeric_values.groupby(summary["job"]).max().dropna()
    if max_series.empty:
        return pd.DataFrame()

    return max_series.reset_index().rename(columns={component: f"max_{component}"})


def execute_extraction_pipeline(
    selected_jobs: List[JobRecord],
    job_targets: Dict[str, List[InstanceTargets]],
    component: str,
    step: Optional[str],
    abaqus_command: str,
    extract_dir: Path,
):
    summary_frames: List[pd.DataFrame] = []
    errors: List[str] = []

    progress = st.progress(0.0)
    status_placeholder = st.empty()

    for index, job in enumerate(selected_jobs, start=1):
        total = max(len(selected_jobs), 1)
        progress.progress(index / total)
        status_placeholder.info(f"抽出中: {job.name}")

        targets = [target for target in job_targets.get(job.name, []) if target.element_labels]
        if not targets:
            errors.append(f"ジョブ {job.name} に要素が指定されていません。")
            continue

        try:
            frame = run_extraction(job, component, targets, abaqus_command, step, extract_dir)
        except Exception as exc:  # pragma: no cover - external command
            errors.append(f"{job.name}: 抽出に失敗しました ({exc})")
            continue

        if frame.empty:
            errors.append(f"{job.name}: 抽出結果が空です。ステップや要素を確認してください。")
            continue

        summary_frames.append(frame)

        job_output_dir = job.odb_path.resolve().parent
        ensure_directory(job_output_dir)
        for (instance_name, part_value), group in frame.groupby(["instance", "part"], dropna=False):
            sanitized_part = ""
            if isinstance(part_value, str) and part_value:
                sanitized_part = sanitize_token(part_value)
            for element_label, element_df in group.groupby("element"):
                stem = f"{sanitize_token(job.name)}_{sanitize_token(instance_name)}"
                if sanitized_part:
                    stem += f"_{sanitized_part}"
                stem += f"_{component}_E{element_label}"
                output_path = unique_path(job_output_dir, stem, "_timeseries.csv")
                element_df.to_csv(output_path, index=False)

    progress.empty()
    status_placeholder.empty()

    if summary_frames:
        summary = pd.concat(summary_frames, ignore_index=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        job_directories = sorted({job.odb_path.resolve().parent for job in selected_jobs})
        summary_paths = []
        for directory in job_directories:
            path = unique_path(directory, f"_summary_{component}_{timestamp}", ".csv")
            summary.to_csv(path, index=False)
            summary_paths.append(path)
    else:
        summary = pd.DataFrame()
        summary_paths = []

    component_max = compute_component_max(summary, component)

    return {
        "summary": summary,
        "summary_paths": summary_paths,
        "errors": errors,
        "component_max": component_max,
    }


def _set_postprocessor_context(context: dict) -> None:
    st.session_state[POSTPROCESSOR_CONTEXT_KEY] = _normalize_postprocessor_context(context)


def get_postprocessor_context() -> dict:
    return st.session_state.get(
        POSTPROCESSOR_CONTEXT_KEY,
        {"ready": False, "message": "後処理タブがまだ初期化されていません。"},
    )


def _replicate_shared_targets(
    shared_targets: List[InstanceTargets], jobs: List[JobRecord]
) -> Dict[str, List[InstanceTargets]]:
    """Return per-job targets copied from a shared list.

    This ensures newly generated jobs inherit the same element settings
    without requiring per-job re-entry.
    """
    targets: Dict[str, List[InstanceTargets]] = {}
    for job in jobs:
        targets[job.name] = [
            InstanceTargets(
                instance=target.instance,
                part=target.part,
                element_labels=list(target.element_labels),
            )
            for target in shared_targets
        ]
    return targets


def _normalize_postprocessor_context(context: dict) -> dict:
    """Ensure the stored context reflects prerequisite readiness.

    - Requires at least one selected job
    - Requires at least one element label across the shared targets
    - Requires the abaqus command to be discoverable
    """
    normalized = dict(context)

    jobs = normalized.get("selected_jobs") or []
    shared_targets: List[InstanceTargets] = normalized.get("shared_targets") or []
    job_targets = normalized.get("job_targets") or {}

    if shared_targets and (not job_targets or set(job_targets.keys()) != {job.name for job in jobs}):
        job_targets = _replicate_shared_targets(shared_targets, jobs)
        normalized["job_targets"] = job_targets
    elif not shared_targets:
        # Derive a shared snapshot from any existing per-job mapping to persist across refreshes.
        for targets in job_targets.values():
            if targets:
                shared_targets = [
                    InstanceTargets(
                        instance=target.instance,
                        part=target.part,
                        element_labels=list(target.element_labels),
                    )
                    for target in targets
                ]
                break
        normalized["shared_targets"] = shared_targets

    abaqus_command = normalized.get("abaqus_command", "")
    extract_dir = normalized.get("extract_dir")
    resolved_command = _resolve_abaqus_command(str(abaqus_command)) if abaqus_command else None
    has_elements = any(target.element_labels for target in shared_targets)

    if not jobs:
        normalized.update({"ready": False, "message": "抽出対象となるジョブを選択してください。"})
        return normalized

    if not resolved_command:
        normalized.update({"ready": False, "message": "abaqus コマンドを確認してください。"})
        return normalized

    if not has_elements:
        normalized.update({"ready": False, "message": "共通の要素番号を入力してください。"})
        return normalized

    normalized.update(
        {
            "ready": True,
            "message": "",
            "abaqus_command": resolved_command,
            "extract_dir": str(extract_dir) if extract_dir else normalized.get("extract_dir"),
            "job_targets": job_targets,
            "shared_targets": shared_targets,
        }
    )
    return normalized


def set_postprocessor_context(context: dict) -> None:
    """Allow the dashboard to refresh the post-processor context."""
    _set_postprocessor_context(_normalize_postprocessor_context(context))


def run_extraction_from_context(context: dict):
    normalized = _normalize_postprocessor_context(context)
    if not normalized.get("ready"):
        raise ValueError(normalized.get("message", "Post-processing step is not ready."))

    return execute_extraction_pipeline(
        normalized["selected_jobs"],
        normalized["job_targets"],
        normalized["component"],
        normalized.get("step"),
        normalized["abaqus_command"],
        Path(normalized["extract_dir"]),
    )


def render_source_section(
    default_results_dir: Path,
    planned_inp_files: Optional[Iterable[Path]] = None,
) -> List[JobRecord]:
    st.subheader("(A) 対象結果の選択")

    jobs: Dict[str, JobRecord] = {}
    if planned_inp_files:
        for job in build_planned_jobs(planned_inp_files, default_results_dir):
            jobs[job.name] = job

    discovered = discover_jobs(default_results_dir)
    for job in discovered:
        jobs.setdefault(job.name, job)

    if not jobs:
        st.info("抽出対象となる ODB が見つかりません。② の結果を出力するとここに表示されます。")
        return []

    job_list = list(jobs.values())
    table_records = []
    for job in job_list:
        table_records.append(
            {
                "job": job.name,
                "source": "② 実行予定" if job.source == "planned" else "ファイルシステム",
                "odb": str(job.odb_path),
                "状態": "存在" if job.exists else "未生成",
            }
        )
    st.dataframe(pd.DataFrame(table_records), use_container_width=True)

    defaults = [job.name for job in job_list]
    selected_names = st.multiselect("抽出するジョブ", defaults, default=defaults)
    return [jobs[name] for name in selected_names]


def _manual_instance_names_global(seed_default: str = "") -> List[str]:
    """Allow manual entry of common instance names even before ODB creation.

    ``seed_default`` provides an initial suggestion (e.g., ``"INSTANCE-1"``)
    the first time the input is shown so users can predefine targets before
    jobs generate ODBs.
    """
    if seed_default and "manual_instances_global" not in st.session_state:
        st.session_state["manual_instances_global"] = seed_default

    manual_text = st.text_input(
        "追加インスタンス名 (全ジョブ共通)",
        value=st.session_state.get("manual_instances_global", seed_default),
        key="manual_instances_global",
        placeholder="例: INSTANCE-1, INSTANCE-2",
    )

    names = []
    for token in manual_text.replace("\n", ",").split(","):
        stripped = token.strip()
        if stripped:
            names.append(stripped)
    return names


def _collect_instance_options(
    jobs: List[JobRecord], abaqus_command: str
) -> Dict[str, Dict[str, str]]:
    """Return available instance names across all jobs.

    The returned mapping is keyed by instance name, with the first seen
    ``partName`` kept as a hint for default values.
    """
    options: Dict[str, Dict[str, str]] = {}
    for job in jobs:
        if not job.odb_path.exists():
            st.info(
                f"ジョブ {job.name}: ② の実行後に ODB が生成されたタイミングでインスタンス一覧を取得します。"
            )
            continue
        try:
            instances = load_instance_metadata(
                str(job.odb_path),
                abaqus_command,
                job.odb_path.stat().st_mtime,
            )
        except Exception as exc:  # pragma: no cover - external command
            st.warning(f"ジョブ {job.name} のインスタンス一覧取得に失敗: {exc}")
            continue

        for entry in instances:
            name = entry.get("name", "")
            if not name:
                continue
            part_name = entry.get("partName", "")
            if name not in options or (not options[name].get("partName") and part_name):
                options[name] = {"partName": part_name}

    return options


def render_targets_section(
    jobs: List[JobRecord],
    abaqus_command: str,
) -> tuple[Dict[str, List[InstanceTargets]], List[InstanceTargets]]:
    st.subheader("(B) 抽出条件")

    instance_options = _collect_instance_options(jobs, abaqus_command)
    seed_manual = "INSTANCE-1" if not instance_options else ""
    if not instance_options:
        st.info(
            "ODB がまだ生成されていないジョブでも、ここでインスタンス名や要素番号を先に登録できます。"
        )
    manual_instances = _manual_instance_names_global(seed_default=seed_manual)

    option_names = sorted(instance_options.keys())
    selection_key = "common_instances"
    previous_selection = st.session_state.get(selection_key, [])
    defaults = previous_selection or manual_instances or option_names
    selected_instances = st.multiselect(
        "対象インスタンス (全ジョブ共通)",
        options=option_names,
        default=[name for name in defaults if name in option_names],
        key=selection_key,
    )
    if manual_instances:
        for name in manual_instances:
            if name not in selected_instances:
                selected_instances.append(name)
    elif not selected_instances and seed_manual:
        selected_instances.append(seed_manual)

    targets: List[InstanceTargets] = []
    for instance_name in selected_instances:
        default_part = instance_options.get(instance_name, {}).get("partName", "")
        part_key = f"part_common_{instance_name}"
        part_value = st.text_input(
            f"パート名 (インスタンス: {instance_name})",
            value=default_part,
            key=part_key,
            placeholder="例: PART-1",
        ).strip()

        element_key = f"elements_common_{instance_name}"
        if element_key not in st.session_state:
            st.session_state[element_key] = ""
        element_text = st.text_input(
            f"要素番号 (インスタンス: {instance_name})",
            key=element_key,
            placeholder="例: 12345, 23456",
        )
        labels = parse_element_text(element_text)

        targets.append(
            InstanceTargets(
                instance=instance_name,
                part=part_value or None,
                element_labels=labels,
            )
        )

    job_targets: Dict[str, List[InstanceTargets]] = _replicate_shared_targets(
        targets, jobs
    )
    return job_targets, targets


def render_controls() -> Dict[str, Optional[str]]:
    component = st.selectbox("成分", options=["S11", "S22", "S33", "S12", "S13", "S23"], index=1)
    step = st.text_input("ステップ名またはインデックス (任意)", value="")
    return {"component": component, "step": step.strip() or None}


def run_app(
    abaqus_command: str,
    default_results_dir: Optional[Path] = None,
    extract_root: Optional[Path] = None,
    planned_inp_files: Optional[Iterable[Path]] = None,
    within_dashboard: bool = False,
) -> None:
    results_dir = (default_results_dir or DEFAULT_RESULTS_DIR).resolve()
    extract_dir = (extract_root or DEFAULT_EXTRACT_ROOT).resolve()
    for path in (results_dir, extract_dir):
        ensure_directory(path)

    _set_postprocessor_context({
        "ready": False,
        "message": "抽出条件を設定してください。",
        "shared_targets": [],
    })

    if not within_dashboard:
        st.set_page_config(page_title="Abaqus ODB Post-Processor", layout="wide")
        st.title("ODB 応力抽出アプリ")
    else:
        st.header("ODB 応力抽出")

    with st.sidebar:
        st.header("設定")
        st.caption("診断: 使用パス")
        st.code(
            "APP_ROOT = {}\nDEFAULT_RESULTS_DIR = {}\nEXTRACT_ROOT = {}".format(
                APP_ROOT, results_dir, extract_dir
            )
        )
        st.caption("Abaqus Python 2.7 が利用可能なコマンドを指定してください。")

    abaqus_command = abaqus_command.strip()
    if not abaqus_command:
        st.error("abaqus コマンドを入力してください。")
        _set_postprocessor_context({"ready": False, "message": "abaqus コマンドを入力してください。"})
        return

    command_path = Path(abaqus_command)
    if command_path.exists():
        abaqus_command = str(command_path)
    else:
        resolved = shutil.which(abaqus_command)
        if resolved is None:
            st.error(f"指定されたコマンドが見つかりません: {abaqus_command}")
            _set_postprocessor_context({"ready": False, "message": "abaqus コマンドを確認してください。"})
            return
        abaqus_command = resolved

    selected_jobs = render_source_section(
        results_dir,
        planned_inp_files=planned_inp_files,
    )
    if not selected_jobs:
        _set_postprocessor_context({
            "ready": False,
            "message": "抽出対象となる ODB を選択してください。",
            "selected_jobs": [],
            "job_targets": {},
            "shared_targets": [],
            "component": None,
            "step": None,
            "abaqus_command": abaqus_command,
            "extract_dir": str(extract_dir),
        })
        return

    job_targets, shared_targets = render_targets_section(selected_jobs, abaqus_command)
    controls = render_controls()

    context = _normalize_postprocessor_context(
        {
            "ready": True,
            "message": "",
            "selected_jobs": selected_jobs,
            "job_targets": job_targets,
            "shared_targets": shared_targets,
            "component": controls["component"],
            "step": controls["step"],
            "abaqus_command": abaqus_command,
            "extract_dir": str(extract_dir),
        }
    )
    _set_postprocessor_context(context)

    if st.button("Extract", type="primary"):
        if not context.get("ready"):
            st.warning(context.get("message"))
            return

        result = run_extraction_from_context(context)
        summary = result["summary"]
        summary_paths = result["summary_paths"]
        errors = result["errors"]
        component_max = result.get("component_max", pd.DataFrame())

        if not summary.empty and summary_paths:
            primary_summary = summary_paths[0]
            location_text = ", ".join(str(path) for path in summary_paths)
            st.success(f"抽出が完了しました。サマリーファイル: {location_text}")
            st.dataframe(summary.head(100), use_container_width=True)
            csv_data = summary.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Summary CSV をダウンロード",
                data=csv_data,
                file_name=primary_summary.name,
                mime="text/csv",
            )
            if not component_max.empty:
                st.subheader(f"ジョブ別 {controls['component']} 最大値")
                st.dataframe(component_max, use_container_width=True)
        else:
            st.warning("有効な抽出結果が得られませんでした。設定を確認してください。")

        if errors:
            st.error("\n".join(errors))


def main() -> None:
    run_app("abaqus")


if __name__ == "__main__":
    main()
