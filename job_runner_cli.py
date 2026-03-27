"""Command-line runner for executing Abaqus input files sequentially."""
from __future__ import annotations

import argparse
import csv
import os
import shlex
import smtplib
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

SUCCESS_TOKEN_DEFAULT = "THE ANALYSIS HAS COMPLETED SUCCESSFULLY"
LOG_TAIL_LINES = 20

SCRIPT_DIR = Path(__file__).resolve().parent
_candidate_root = SCRIPT_DIR.parent
if (_candidate_root / "jobs").exists() or (_candidate_root / "results").exists():
    APP_ROOT = _candidate_root
else:
    APP_ROOT = SCRIPT_DIR

DEFAULT_INP_DIR = (APP_ROOT / "jobs").resolve()
DEFAULT_RESULTS_DIR = (APP_ROOT / "results").resolve()

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

    def to_row(self) -> dict:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Abaqus jobs sequentially.")
    parser.add_argument("inp_dir", nargs="?", help="Directory that contains .inp files")
    parser.add_argument("abaqus_path", nargs="?", help="Abaqus command path")
    parser.add_argument("cpus", nargs="?", help="Number of CPU cores")
    parser.add_argument("--dry-run", action="store_true", help="Print commands only")
    parser.add_argument(
        "--notify-email",
        action="store_true",
        help="Send a completion email after all jobs finish",
    )
    parser.add_argument(
        "--notify-email-to",
        help="Email address for completion notifications",
        default=None,
    )
    return parser.parse_args()


def bool_from_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip() in {"1", "true", "TRUE", "yes", "on"}


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_file_tail(path: Path, max_lines: int = LOG_TAIL_LINES) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return "".join(deque(fh, maxlen=max_lines))
    except OSError:
        return ""


def sta_contains_success(path: Path, success_token: str) -> bool:
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if success_token in line:
                    return True
    except OSError:
        return False
    return False


def should_skip(
    input_path: Path,
    skip_enabled: bool,
    skip_policy: str,
    results_base: Path,
    success_token: str,
) -> Tuple[bool, str]:
    if not skip_enabled:
        return False, ""
    job_name = input_path.stem
    candidate_bases: List[Path] = []
    for base in [results_base, DEFAULT_RESULTS_DIR]:
        if base and base not in candidate_bases:
            candidate_bases.append(base)
    if skip_policy == "成功フラグ確認（.sta）":
        sta_candidates = [input_path.with_suffix(".sta")]
        sta_candidates.extend(base / job_name / f"{job_name}.sta" for base in candidate_bases)
        for sta_path in sta_candidates:
            if sta_contains_success(sta_path, success_token):
                return True, "STA 成功フラグを確認"
        return False, "STA 成功フラグなし"
    for ext in (".odb", ".dat"):
        if input_path.with_suffix(ext).exists():
            return True, f"同階層に {ext} が存在"
        for base in candidate_bases:
            candidate = base / job_name / f"{job_name}{ext}"
            if candidate.exists():
                return True, f"results 配下に {ext} が存在"
    return False, ""


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


def run_command(command: Sequence[str], working_dir: Path) -> Tuple[int, str, str]:
    if sys.platform == "win32":
        popen_command = subprocess.list2cmdline(list(command))
        process = subprocess.Popen(
            popen_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            universal_newlines=True,
            shell=True,
            cwd=str(working_dir),
        )
    else:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            universal_newlines=True,
            cwd=str(working_dir),
        )
    stdout_data, stderr_data = process.communicate()
    if stdout_data:
        sys.stdout.write(stdout_data)
    if stderr_data:
        sys.stderr.write(stderr_data)
    return process.returncode, stdout_data, stderr_data


def write_results_log(results: List[JobResult], results_base: Path) -> Path:
    ensure_directory(results_base)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = results_base / f"_run_log_{timestamp}.csv"
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
        for result in results:
            writer.writerow(result.to_row())
    return log_path


def send_completion_email(
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


def main() -> int:
    args = parse_args()
    if args.inp_dir:
        inp_dir = Path(args.inp_dir).expanduser()
    else:
        env_inp = os.environ.get("APP_RUN_INP_DIR")
        inp_dir = Path(env_inp).expanduser() if env_inp else DEFAULT_INP_DIR

    abaqus_path = args.abaqus_path or os.environ.get("APP_RUN_ABAQUS_PATH") or "abaqus"
    cpus_raw = args.cpus or os.environ.get("APP_RUN_CPUS") or "1"
    try:
        cpus = int(cpus_raw)
    except ValueError:
        cpus = 1

    env_results = os.environ.get("APP_RUN_RESULTS_BASE")
    if env_results:
        results_base = Path(env_results).expanduser()
    else:
        results_base = inp_dir

    skip_enabled = bool_from_env("APP_RUN_SKIP_ENABLED", True)
    skip_policy = os.environ.get("APP_RUN_SKIP_POLICY", "結果ファイル存在（.odb/.dat）")
    success_token = os.environ.get("APP_RUN_SUCCESS_TOKEN", SUCCESS_TOKEN_DEFAULT)
    dry_run = args.dry_run or bool_from_env("APP_RUN_DRY_RUN", False)

    notify_enabled = args.notify_email or bool_from_env("APP_NOTIFY_EMAIL_ENABLED", False)
    notify_recipient = args.notify_email_to or os.environ.get("APP_NOTIFY_EMAIL_TO", "")

    inp_list_env = os.environ.get("APP_RUN_INP_LIST")
    if inp_list_env:
        files = [Path(item.strip()) for item in inp_list_env.splitlines() if item.strip()]
    else:
        files = sorted(inp_dir.glob("*.inp"))

    if not files:
        print("[INFO] 対象となる .inp ファイルが見つかりません。")
        return 0

    results: List[JobResult] = []
    for file_path in files:
        job_name = file_path.stem
        print(f"[INFO] --- {job_name} ---")
        skip, reason = should_skip(
            file_path,
            skip_enabled,
            skip_policy,
            results_base,
            success_token,
        )
        if skip:
            print(f"[INFO] スキップ: {job_name} ({reason})")
            results.append(
                JobResult(
                    job=job_name,
                    input_path=file_path,
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
            )
            continue

        command = command_for_job(abaqus_path, job_name, file_path, cpus)
        print("[INFO] 実行コマンド: " + format_command_display(command))
        if dry_run:
            results.append(
                JobResult(
                    job=job_name,
                    input_path=file_path,
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
            )
            continue

        start = time.time()
        return_code, stdout_data, stderr_data = run_command(command, file_path.parent)
        elapsed = time.time() - start

        sta_tail = ""
        success_flag = False
        sta_candidates = [file_path.with_suffix(".sta")]
        for base in [results_base, DEFAULT_RESULTS_DIR]:
            sta_candidates.append(base / job_name / f"{job_name}.sta")
        for sta_path in sta_candidates:
            tail = read_file_tail(sta_path)
            if tail:
                sta_tail = tail
                if sta_contains_success(sta_path, success_token):
                    success_flag = True
                    break

        results_dir = file_path.parent
        status = "success" if return_code == 0 and success_flag else "failed"
        if status == "success":
            reason = ""
        else:
            parts = []
            if return_code not in (None, 0):
                parts.append(f"return_code={return_code}")
            if not success_flag:
                parts.append("STA で成功フラグ未検出")
            reason = " / ".join(parts) if parts else "不明なエラー"

        results.append(
            JobResult(
                job=job_name,
                input_path=file_path,
                status=status,
                reason=reason,
                skip_policy=skip_policy,
                return_code=return_code,
                elapsed_sec=elapsed,
                results_dir=results_dir,
                timestamp=datetime.now(),
                stderr_tail="\n".join(stderr_data.splitlines()[-LOG_TAIL_LINES:]) if stderr_data else "",
                sta_tail=sta_tail,
            )
        )

    log_path = write_results_log(results, results_base)
    print(f"[INFO] ログを {log_path} に保存しました。")

    if notify_enabled:
        sent, error = send_completion_email(results, notify_recipient, log_path)
        if sent:
            print(f"[INFO] 通知メールを送信しました: {notify_recipient}")
        else:
            print(f"[WARN] 通知メールの送信に失敗しました: {error}")

    failures = [r for r in results if r.status == "failed"]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
