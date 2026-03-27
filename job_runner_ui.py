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
"""Streamlit app for running Abaqus input files sequentially."""
from __future__ import annotations

import csv
import os
import queue
import shlex
import smtplib
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st

SUCCESS_TOKEN = "THE ANALYSIS HAS COMPLETED SUCCESSFULLY"

SCRIPT_DIR = Path(__file__).resolve().parent
_candidate_root = SCRIPT_DIR.parent
if (_candidate_root / "jobs").exists() or (_candidate_root / "results").exists():
    APP_ROOT = _candidate_root
else:
    APP_ROOT = SCRIPT_DIR

DEFAULT_SOURCE_DIR = (APP_ROOT / "jobs").resolve()
DEFAULT_RESULTS_DIR = (APP_ROOT / "results").resolve()

LOG_TAIL_LINES = 20
JOB_RUNNER_CONTEXT_KEY = "job_runner_context"

SMTP_HOST = os.environ.get("APP_SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("APP_SMTP_PORT", "25"))
SMTP_SENDER = os.environ.get("APP_SMTP_SENDER", "abaqus-notify@example.com")


@dataclass
class JobResult:
    job: str
    input_path: Path
    status: str
    reason: str
    skip_policy: str
    return_code: Optional[int]
    elapsed_sec: float
    results_dir: Optional[Path]
    timestamp: datetime
    stderr_tail: str
    sta_tail: str

    def to_row(self) -> Dict[str, object]:
        return {
            "job": self.job,
            "input_path": str(self.input_path),
            "status": self.status,
            "reason": self.reason,
            "skip_policy": self.skip_policy,
            "return_code": self.return_code,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "results_dir": str(self.results_dir) if self.results_dir else "",
            "timestamp": self.timestamp.isoformat(timespec="seconds"),
            "stderr_tail": self.stderr_tail,
            "sta_tail": self.sta_tail,
        }


def list_inp_files(directory: Path, pattern: str, sort_order: str) -> List[Path]:
    if not directory.exists() or not directory.is_dir():
        return []
    pattern = pattern or "*.inp"
    files = list(directory.glob(pattern))
    files = [f for f in files if f.is_file() and f.suffix.lower() == ".inp"]
    if sort_order == "更新時刻昇順":
        files.sort(key=lambda p: p.stat().st_mtime)
    else:
        files.sort(key=lambda p: p.name.lower())
    return files


def initialise_selection(paths: Iterable[Path]) -> None:
    selection = st.session_state.setdefault("job_selection", {})
    for path in paths:
        selection.setdefault(str(path), True)

    existing_keys = {str(path) for path in paths}
    stale_keys = [key for key in selection if key not in existing_keys]
    for key in stale_keys:
        del selection[key]


def render_source_section(default_dir: Optional[Path] = None) -> Tuple[Path, List[Path]]:
    st.header("(A) ソースフォルダの選択")
    base_dir = (default_dir or DEFAULT_SOURCE_DIR).resolve()
    use_custom_dir = st.checkbox("フォルダを指定", value=False, key="use_custom_dir")
    if use_custom_dir:
        custom_default = st.session_state.get("custom_source_dir", str(base_dir))
        custom_path = st.text_input(
            "入力フォルダのパス",
            value=custom_default,
            key="custom_source_dir",
        )
        source_dir = Path(custom_path).expanduser()
    else:
        source_dir = base_dir

    st.markdown(f"使用フォルダ: `{source_dir}`")

    col_pattern, col_sort, _ = st.columns([2, 2, 1])
    pattern_default = st.session_state.get("pattern", "*.inp")
    pattern = col_pattern.text_input("フィルタ (glob)", value=pattern_default, key="pattern")
    sort_order = col_sort.selectbox("並び順", ["ファイル名昇順", "更新時刻昇順"], index=0)

    files = list_inp_files(source_dir, pattern, sort_order)
    initialise_selection(files)

    if not files:
        st.info("指定された条件で .inp ファイルが見つかりませんでした。")
        return source_dir, files

    return source_dir, files


def render_job_selection(files: List[Path]) -> Tuple[List[Path], pd.DataFrame]:
    st.header("(C) ジョブ選択")
    if not files:
        return [], pd.DataFrame()

    selection_map = st.session_state.job_selection
    file_records = []
    for path in files:
        stat = path.stat()
        file_records.append(
            {
                "ファイル名": path.name,
                "更新日時": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "サイズ (KB)": f"{stat.st_size / 1024:.1f}",
                "パス": str(path),
            }
        )

    col_select_all, col_clear = st.columns(2)
    if col_select_all.button("全選択"):
        for key in selection_map:
            selection_map[key] = True
    if col_clear.button("全解除"):
        for key in selection_map:
            selection_map[key] = False

    rows = []
    for record in file_records:
        path_str = record["パス"]
        rows.append(
            {
                "選択": bool(selection_map.get(path_str, False)),
                **record,
            }
        )

    df = pd.DataFrame(rows)
    edited_df = st.data_editor(
        df,
        hide_index=True,
        column_config={
            "選択": st.column_config.CheckboxColumn("選択", default=True),
            "ファイル名": st.column_config.TextColumn("ファイル名", disabled=True),
            "更新日時": st.column_config.TextColumn("更新日時", disabled=True),
            "サイズ (KB)": st.column_config.TextColumn("サイズ (KB)", disabled=True),
            "パス": st.column_config.TextColumn("パス", disabled=True),
        },
        key="job_editor",
    )

    selected_files: List[Path] = []
    if isinstance(edited_df, pd.DataFrame):
        for _, row in edited_df.iterrows():
            path = Path(row["パス"])
            is_selected = bool(row["選択"])
            selection_map[str(path)] = is_selected
            if is_selected:
                selected_files.append(path)

    st.markdown(f"**選択 {len(selected_files)} / 全 {len(files)}**")
    return selected_files, edited_df


def render_execution_settings(default_command: Optional[str] = None) -> Dict[str, object]:
    st.header("(B) 実行設定")

    initial_command = default_command or st.session_state.get("abaqus_path", "abaqus")
    abaqus_path = st.text_input("Abaqus 実行コマンド", value=initial_command)
    st.session_state.abaqus_path = abaqus_path

    cpu_options = [1, 6, 12]
    default_cpu = st.session_state.get("cpus", 1)
    if default_cpu not in cpu_options:
        default_cpu = 1
    cpus = st.selectbox("使用コア数", cpu_options, index=cpu_options.index(default_cpu))
    st.session_state.cpus = cpus

    execution_method = st.selectbox(
        "実行方法",
        ["Streamlit から直接実行", "Windows バッチスクリプト経由", "Slurm シェルスクリプト経由"],
        index=0,
    )
    st.session_state.execution_method = execution_method

    skip_enabled = st.checkbox("再実行スキップ", value=st.session_state.get("skip_enabled", True))
    st.session_state.skip_enabled = skip_enabled

    skip_policy = st.selectbox(
        "スキップ基準",
        ["結果ファイル存在（.odb/.dat）", "成功フラグ確認（.sta）"],
        index=0,
    )
    st.session_state.skip_policy = skip_policy

    stop_on_failure = st.checkbox("失敗で停止", value=st.session_state.get("stop_on_failure", False))
    st.session_state.stop_on_failure = stop_on_failure

    dry_run = st.toggle("コマンドだけ表示（実行しない）", value=st.session_state.get("dry_run", False))
    st.session_state.dry_run = dry_run

    notify_email = st.checkbox(
        "解析完了時に通知メールを送る",
        value=st.session_state.get("notify_email", False),
    )
    st.session_state.notify_email = notify_email
    notify_email_to = ""
    if notify_email:
        notify_email_to = st.text_input(
            "送信先メールアドレス",
            value=st.session_state.get("notify_email_to", ""),
            placeholder="example@example.com",
        ).strip()
    st.session_state.notify_email_to = notify_email_to

    return {
        "abaqus_path": abaqus_path,
        "cpus": int(cpus),
        "execution_method": execution_method,
        "skip_enabled": skip_enabled,
        "skip_policy": skip_policy,
        "stop_on_failure": stop_on_failure,
        "dry_run": dry_run,
        "notify_email": notify_email,
        "notify_email_to": notify_email_to,
    }


def command_for_job(abaqus_path: str, job_name: str, input_path: Path, cpus: int) -> List[str]:
    return [
        abaqus_path,
        f"job={job_name}",
        f"input={str(input_path)}",
        f"cpus={cpus}",
        "interactive",
    ]


def format_command_display(command: Sequence[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(list(command))
    return " ".join(shlex.quote(part) for part in command)


def read_file_tail(path: Path, max_lines: int = LOG_TAIL_LINES) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return "".join(deque(fh, maxlen=max_lines))
    except OSError:
        return ""


def sta_contains_success(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if SUCCESS_TOKEN in line:
                    return True
    except OSError:
        return False
    return False


def should_skip_job(
    input_path: Path,
    skip_enabled: bool,
    skip_policy: str,
    results_base: Path,
) -> Tuple[bool, str]:
    if not skip_enabled:
        return False, ""

    job_name = input_path.stem

    if skip_policy == "結果ファイル存在（.odb/.dat）":
        candidate_extensions = [".odb", ".dat"]
        for ext in candidate_extensions:
            same_dir = input_path.with_suffix(ext)
            if same_dir.exists():
                return True, f"同階層に {ext} が存在"

            results_dir = results_base / job_name
            candidate = results_dir / f"{job_name}{ext}"
            if candidate.exists():
                return True, f"results 配下に {ext} が存在"
        return False, ""

    if skip_policy == "成功フラグ確認（.sta）":
        sta_paths = [input_path.with_suffix(".sta"), results_base / job_name / f"{job_name}.sta"]
        for sta_path in sta_paths:
            if sta_contains_success(sta_path):
                return True, "過去の解析で成功済み"
        return False, "成功フラグ未検出"

    return False, ""


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def capture_process_output(
    process: subprocess.Popen,
    log_placeholder,
    tail_lines: int = LOG_TAIL_LINES,
) -> Tuple[str, str]:
    stdout_queue: "queue.Queue[str]" = queue.Queue()
    stderr_queue: "queue.Queue[str]" = queue.Queue()
    stdout_tail: Deque[str] = deque(maxlen=tail_lines)
    stderr_tail: Deque[str] = deque(maxlen=tail_lines)

    def enqueue(pipe, q: "queue.Queue[str]") -> None:
        try:
            for line in iter(pipe.readline, ""):
                q.put(line)
            pipe.close()
        except Exception:
            pass

    threads = [
        threading.Thread(target=enqueue, args=(process.stdout, stdout_queue)),
        threading.Thread(target=enqueue, args=(process.stderr, stderr_queue)),
    ]
    for thread in threads:
        thread.daemon = True
        thread.start()

    while process.poll() is None:
        updated = False
        try:
            line = stdout_queue.get_nowait()
            stdout_tail.append(line)
            updated = True
        except queue.Empty:
            pass
        try:
            line = stderr_queue.get_nowait()
            stderr_tail.append(line)
            updated = True
        except queue.Empty:
            pass
        if updated and log_placeholder is not None:
            log_placeholder.text(
                "".join(["[stdout]\n", "".join(stdout_tail), "\n[stderr]\n", "".join(stderr_tail)])
            )
        time.sleep(0.1)

    while True:
        try:
            line = stdout_queue.get_nowait()
            stdout_tail.append(line)
        except queue.Empty:
            break
    while True:
        try:
            line = stderr_queue.get_nowait()
            stderr_tail.append(line)
        except queue.Empty:
            break

    for thread in threads:
        thread.join(timeout=0.5)

    if log_placeholder is not None:
        log_placeholder.text(
            "".join(["[stdout]\n", "".join(stdout_tail), "\n[stderr]\n", "".join(stderr_tail)])
        )

    return "".join(stdout_tail), "".join(stderr_tail)


def execute_direct(
    command: Sequence[str],
    log_placeholder,
    work_dir: Path,
) -> Tuple[int, str, str]:
    if sys.platform == "win32":
        popen_command = subprocess.list2cmdline(list(command))
        process = subprocess.Popen(
            popen_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            universal_newlines=True,
            bufsize=1,
            shell=True,
            cwd=str(work_dir),
        )
    else:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            universal_newlines=True,
            bufsize=1,
            cwd=str(work_dir),
        )

    stdout_tail, stderr_tail = capture_process_output(process, log_placeholder)
    return_code = process.wait()
    return return_code, stdout_tail, stderr_tail


def execute_via_script(
    script_path: Path,
    input_path: Path,
    abaqus_path: str,
    cpus: int,
    log_placeholder,
    skip_enabled: bool,
    skip_policy: str,
) -> Tuple[int, str, str]:
    env = os.environ.copy()
    env["APP_RUN_INP_LIST"] = str(input_path)
    env["APP_RUN_SKIP_ENABLED"] = "1" if skip_enabled else "0"
    env["APP_RUN_SKIP_POLICY"] = skip_policy
    env["APP_RUN_RESULTS_BASE"] = str(input_path.parent.resolve())
    env["APP_RUN_SUCCESS_TOKEN"] = SUCCESS_TOKEN

    if script_path.suffix.lower() == ".bat":
        command = [
            "cmd.exe",
            "/c",
            str(script_path),
            str(input_path.parent),
            abaqus_path,
            str(cpus),
        ]
    else:
        command = [str(script_path), str(input_path.parent), abaqus_path, str(cpus)]

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        universal_newlines=True,
        bufsize=1,
        env=env,
        shell=False,
    )

    stdout_tail, stderr_tail = capture_process_output(process, log_placeholder)
    return process.wait(), stdout_tail, stderr_tail


def run_job(
    input_path: Path,
    config: Dict[str, object],
    log_placeholder,
    results_base: Path,
) -> JobResult:
    job_name = input_path.stem
    abaqus_path = str(config["abaqus_path"])
    cpus = int(config["cpus"])
    skip_enabled = bool(config["skip_enabled"])
    skip_policy = str(config["skip_policy"])
    dry_run = bool(config["dry_run"])

    skip, reason = should_skip_job(input_path, skip_enabled, skip_policy, results_base)
    if skip:
        return JobResult(
            job=job_name,
            input_path=input_path,
            status="skipped",
            reason=reason,
            skip_policy=skip_policy,
            return_code=None,
            elapsed_sec=0.0,
            results_dir=None,
            timestamp=datetime.now(),
            stderr_tail="",
            sta_tail="",
        )

    command = command_for_job(abaqus_path, job_name, input_path, cpus)
    command_display = format_command_display(command)
    if dry_run:
        log_placeholder.text(command_display)
        return JobResult(
            job=job_name,
            input_path=input_path,
            status="dry_run",
            reason="ドライラン",
            skip_policy=skip_policy,
            return_code=None,
            elapsed_sec=0.0,
            results_dir=None,
            timestamp=datetime.now(),
            stderr_tail="",
            sta_tail="",
        )

    start_time = time.time()
    stdout_tail = ""
    stderr_tail = ""
    work_dir = input_path.parent

    if config["execution_method"] == "Streamlit から直接実行":
        log_placeholder.text(command_display)
        return_code, stdout_tail, stderr_tail = execute_direct(
            command, log_placeholder, work_dir
        )
    else:
        if config["execution_method"] == "Windows バッチスクリプト経由":
            script_path = SCRIPT_DIR / "app_run_win.bat"
        else:
            script_path = SCRIPT_DIR / "app_run_slurm.sh"
        if not script_path.exists():
            log_placeholder.error(f"スクリプトが見つかりません: {script_path}")
            return JobResult(
                job=job_name,
                input_path=input_path,
                status="failed",
                reason=f"{script_path} が見つかりません",
                skip_policy=skip_policy,
                return_code=None,
                elapsed_sec=0.0,
                results_dir=None,
                timestamp=datetime.now(),
                stderr_tail="",
                sta_tail="",
            )
        return_code, stdout_tail, stderr_tail = execute_via_script(
            script_path,
            input_path,
            abaqus_path,
            cpus,
            log_placeholder,
            skip_enabled,
            skip_policy,
        )

    elapsed = time.time() - start_time
    sta_path_candidates = [input_path.with_suffix(".sta"), results_base / job_name / f"{job_name}.sta"]
    sta_tail = ""
    success_flag_detected = False
    for sta_path in sta_path_candidates:
        tail = read_file_tail(sta_path)
        if tail:
            sta_tail = tail
            if sta_contains_success(sta_path):
                success_flag_detected = True
            if not sta_tail:
                sta_tail = tail
            break

    results_dir = input_path.parent
    status = "success" if return_code == 0 and success_flag_detected else "failed"
    if status == "success":
        reason = ""
    else:
        parts = []
        if return_code not in (None, 0):
            parts.append(f"return_code={return_code}")
        if not success_flag_detected:
            parts.append("STA で成功フラグ未検出")
        reason = " / ".join(parts) if parts else "不明なエラー"

    return JobResult(
        job=job_name,
        input_path=input_path,
        status=status,
        reason=reason,
        skip_policy=skip_policy,
        return_code=return_code,
        elapsed_sec=elapsed,
        results_dir=results_dir,
        timestamp=datetime.now(),
        stderr_tail=stderr_tail,
        sta_tail=sta_tail,
    )


def write_results_log(rows: List[JobResult], log_dir: Path) -> Optional[Path]:
    if not rows:
        return None
    ensure_directory(log_dir)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"_run_log_{timestamp}.csv"
    with log_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "job",
                "input_path",
                "status",
                "reason",
                "skip_policy",
                "return_code",
                "elapsed_sec",
                "results_dir",
                "timestamp",
                "stderr_tail",
                "sta_tail",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_row())
    return log_path


def _send_completion_email(
    results: List[JobResult],
    recipient: str,
    log_path: Optional[Path],
) -> Tuple[bool, str]:
    if not recipient:
        return False, "送信先アドレスが指定されていません。"

    successes = [r for r in results if r.status == "success"]
    failures = [r for r in results if r.status == "failed"]
    skipped = [r for r in results if r.status == "skipped"]

    subject = "Abaqus 解析完了通知"
    body_lines = [
        "Abaqus ジョブの実行が完了しました。",
        "",
        f"成功: {len(successes)} 件",
        f"失敗: {len(failures)} 件",
        f"スキップ: {len(skipped)} 件",
    ]
    if log_path:
        body_lines.append(f"ログ: {log_path}")
    if failures:
        body_lines.append("")
        body_lines.append("失敗ジョブ:")
        for item in failures:
            body_lines.append(f"- {item.job}: {item.reason}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = SMTP_SENDER
    message["To"] = recipient
    message.set_content("\n".join(body_lines))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as client:
            client.send_message(message)
    except Exception as exc:  # pragma: no cover - network dependent
        return False, str(exc)
    return True, ""


def run_selected_jobs(
    selected_files: List[Path],
    config: Dict[str, object],
    log_dir: Path,
    results_base: Path,
) -> None:
    if not selected_files:
        st.warning("実行対象が選択されていません。")
        return

    progress = st.progress(0)
    log_placeholder = st.empty()
    table_placeholder = st.empty()
    status_placeholder = st.empty()

    results: List[JobResult] = []
    total = len(selected_files)

    for index, input_path in enumerate(selected_files, start=1):
        status_placeholder.markdown(f"**実行中:** {input_path.name} ({index}/{total})")
        job_result = run_job(input_path, config, log_placeholder, results_base)
        results.append(job_result)

        progress.progress(index / total)
        table_placeholder.dataframe(pd.DataFrame([r.to_row() for r in results]))

        if job_result.status == "failed" and config.get("stop_on_failure"):
            st.error(f"{job_result.job} で失敗したため停止します。")
            break

    log_path = write_results_log(results, log_dir)
    if log_path:
        st.success(f"結果を {log_path} に保存しました。")

    if config.get("notify_email"):
        recipient = str(config.get("notify_email_to", "")).strip()
        sent, error = _send_completion_email(results, recipient, log_path)
        if sent:
            st.success(f"通知メールを送信しました: {recipient}")
        else:
            st.warning(f"通知メールの送信に失敗しました: {error}")


def _set_job_runner_context(context: dict) -> None:
    st.session_state[JOB_RUNNER_CONTEXT_KEY] = context


def get_job_runner_context() -> dict:
    return st.session_state.get(
        JOB_RUNNER_CONTEXT_KEY,
        {"ready": False, "message": "ジョブ実行タブがまだ初期化されていません。"},
    )


def set_job_runner_context(context: dict) -> None:
    """Allow external callers (dashboard) to refresh the runner context."""
    _set_job_runner_context(context)


def run_jobs_from_context(context: dict) -> None:
    if not context.get("ready"):
        raise ValueError(context.get("message", "Job runner step is not ready."))
    run_selected_jobs(
        context["selected_files"],
        context["config"],
        context["log_dir"],
        context["results_base"],
    )


def run_app(
    default_source_dir: Optional[Path] = None,
    default_results_dir: Optional[Path] = None,
    default_abaqus_command: Optional[str] = None,
    within_dashboard: bool = False,
) -> None:
    if not within_dashboard:
        st.set_page_config(page_title="Abaqus ジョブ一括実行", layout="wide")
        st.title("Abaqus 入力ファイル一括実行 (App-Run)")
    else:
        st.header("Abaqus 入力ファイル一括実行")

    _set_job_runner_context({"ready": False, "message": "ジョブ一覧を読み込み中です。"})

    source_dir, files = render_source_section(default_dir=default_source_dir)
    config = render_execution_settings(default_command=default_abaqus_command)
    selected_files, _ = render_job_selection(files)

    st.header("(D) 実行＆モニタ")
    results_base = (default_results_dir or DEFAULT_RESULTS_DIR).resolve()

    if selected_files:
        context = {
            "ready": True,
            "message": "",
            "selected_files": selected_files,
            "config": config,
            "log_dir": source_dir,
            "results_base": results_base,
        }
    else:
        context = {
            "ready": False,
            "message": "実行対象が選択されていません。",
            "selected_files": [],
            "config": config,
            "log_dir": source_dir,
            "results_base": results_base,
        }

    _set_job_runner_context(context)
    run_button = st.button("Run selected jobs", disabled=not selected_files)
    if run_button and context.get("ready"):
        run_jobs_from_context(context)
    elif run_button:
        st.warning(context.get("message"))

    st.header("(E) 出力の取り扱い")
    st.markdown(
        "- 実行ディレクトリは .inp ファイルと同じフォルダです。\n"
        "- Abaqus の出力ファイル（.odb, .dat など）は実行元のフォルダに残ります。\n"
        "- 実行ログは入力フォルダに _run_log_YYYYmmdd-HHMMSS.csv として保存されます。"
    )


def main() -> None:
    run_app()


if __name__ == "__main__":
    main()
