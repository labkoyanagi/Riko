"""Utilities for orchestrating Abaqus analysis and result extraction steps.

This module keeps the ability to run individual steps while also providing a
helper to run the analysis (step ②) and extraction (step ③) back-to-back.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path
from typing import Callable, Iterable, List, Sequence

Runner = Callable[[Sequence[str], Path | None], None]


def build_command_from_template(template: str, **kwargs) -> List[str]:
    """Render a command template and split it into arguments."""

    rendered = template.format(**kwargs)
    return shlex.split(rendered)


def normalize_inp_name(name: str) -> str:
    """Ensure an .inp suffix exists."""

    return name if name.endswith(".inp") else f"{name}.inp"


def select_inp_files(jobs_dir: Path, targets: Iterable[str] | None = None) -> List[Path]:
    """Collect .inp files from ``jobs_dir`` filtered by ``targets`` if provided."""

    if targets:
        normalized = {normalize_inp_name(item) for item in targets}
        candidates = [jobs_dir / name for name in normalized]
    else:
        candidates = sorted(jobs_dir.glob("*.inp"))

    existing = [path for path in candidates if path.is_file()]
    if not existing:
        raise FileNotFoundError("指定された .inp ファイルが見つかりませんでした。")
    return existing


def run_commands(
    commands: Iterable[Sequence[str]],
    working_directory: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> List[Sequence[str]]:
    """Execute commands sequentially and return the list that was executed."""

    executed = []
    for command in commands:
        runner(command, cwd=str(working_directory) if working_directory else None, check=True)
        executed.append(list(command))
    return executed


def build_analysis_commands(inp_files: Iterable[Path], abaqus_template: str) -> List[List[str]]:
    """Create Abaqus analysis commands for the provided ``.inp`` files."""

    return [
        build_command_from_template(abaqus_template, job_name=path.stem, inp_path=path)
        for path in inp_files
    ]


def build_extraction_commands(
    inp_files: Iterable[Path], results_dir: Path, extract_template: str
) -> List[List[str]]:
    """Create extraction commands that mirror the provided ``.inp`` files."""

    results_dir.mkdir(parents=True, exist_ok=True)
    return [
        build_command_from_template(
            extract_template,
            job_name=path.stem,
            odb_path=path.with_suffix(".odb"),
            results_dir=results_dir,
        )
        for path in inp_files
    ]


def run_analyses(
    jobs_dir: Path,
    abaqus_template: str,
    targets: Iterable[str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> List[Sequence[str]]:
    """Run Abaqus analyses for selected ``.inp`` files in ``jobs_dir``."""

    inp_files = select_inp_files(jobs_dir, targets)
    commands = build_analysis_commands(inp_files, abaqus_template)
    return run_commands(commands, working_directory=jobs_dir, runner=runner)


def run_extractions(
    jobs_dir: Path,
    results_dir: Path,
    extract_template: str,
    targets: Iterable[str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> List[Sequence[str]]:
    """Execute extraction commands for the selected job names."""

    inp_files = select_inp_files(jobs_dir, targets)
    commands = build_extraction_commands(inp_files, results_dir, extract_template)
    return run_commands(commands, working_directory=jobs_dir, runner=runner)


def run_analysis_and_extraction(
    jobs_dir: Path,
    results_dir: Path,
    abaqus_template: str,
    extract_template: str,
    targets: Iterable[str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    """Run step ② (analysis) and step ③ (extraction) back-to-back.

    ``targets`` may contain job names with or without the ``.inp`` suffix. When
    omitted, all ``.inp`` files inside ``jobs_dir`` are processed.
    """

    inp_files = select_inp_files(jobs_dir, targets)
    analysis_commands = build_analysis_commands(inp_files, abaqus_template)
    run_commands(analysis_commands, working_directory=jobs_dir, runner=runner)

    extraction_commands = build_extraction_commands(inp_files, results_dir, extract_template)
    run_commands(extraction_commands, working_directory=jobs_dir, runner=runner)
    return {
        "analysis": analysis_commands,
        "extraction": extraction_commands,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Define the command-line interface for individual and chained runs."""

    parser = argparse.ArgumentParser(description="Run Abaqus analyses and result extraction.")
    parser.add_argument(
        "--jobs-dir", type=Path, default=Path("jobs"), help="Directory containing .inp files."
    )
    parser.add_argument(
        "--results-dir", type=Path, default=Path("extracts"), help="Directory to store extraction outputs."
    )
    parser.add_argument(
        "--targets",
        nargs="*",
        help="Specific .inp filenames or job names to process. Defaults to all .inp files in jobs-dir.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run Abaqus analyses only (step ②).")
    run_parser.add_argument(
        "--abaqus-template",
        default="abaqus job={job_name} input={inp_path}",
        help="Command template for running Abaqus analyses.",
    )

    extract_parser = subparsers.add_parser("extract", help="Extract results only (step ③).")
    extract_parser.add_argument(
        "--extract-template",
        default="python extract_results.py --odb {odb_path} --out {results_dir}",
        help="Command template for running result extraction.",
    )

    combined_parser = subparsers.add_parser(
        "run-and-extract", help="Run analyses then extract results (steps ②＋③)."
    )
    combined_parser.add_argument(
        "--abaqus-template",
        default="abaqus job={job_name} input={inp_path}",
        help="Command template for running Abaqus analyses.",
    )
    combined_parser.add_argument(
        "--extract-template",
        default="python extract_results.py --odb {odb_path} --out {results_dir}",
        help="Command template for running result extraction.",
    )

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the workflow helper CLI."""

    args = parse_args(argv)

    try:
        if args.command == "run":
            run_analyses(args.jobs_dir, args.abaqus_template, args.targets)
        elif args.command == "extract":
            run_extractions(args.jobs_dir, args.results_dir, args.extract_template, args.targets)
        elif args.command == "run-and-extract":
            run_analysis_and_extraction(
                args.jobs_dir,
                args.results_dir,
                args.abaqus_template,
                args.extract_template,
                args.targets,
            )
        return 0
    except Exception as exc:  # pragma: no cover - CLI error surface
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
