# -*- coding: utf-8 -*-
"""Tests for the workflow helper that chains analysis and extraction."""

import tempfile
import unittest
from pathlib import Path

import workflow


class RecordingRunner:
    """Collect commands instead of executing them."""

    def __init__(self):
        self.calls = []

    def __call__(self, command, cwd=None, check=True):  # pragma: no cover - simple proxy
        self.calls.append((list(command), cwd, check))
        return None


class WorkflowSelectionTests(unittest.TestCase):
    def test_select_inp_files_resolves_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir)
            (jobs_dir / "a.inp").write_text("", encoding="utf-8")
            (jobs_dir / "b.inp").write_text("", encoding="utf-8")

            selected = workflow.select_inp_files(jobs_dir, ["a", "b.inp"])
            self.assertEqual({path.name for path in selected}, {"a.inp", "b.inp"})


class WorkflowExecutionTests(unittest.TestCase):
    def test_run_analysis_and_extraction_orders_steps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir)
            results_dir = jobs_dir / "extracts"
            for name in ("case1.inp", "case2.inp"):
                (jobs_dir / name).write_text("*HEADING\n", encoding="utf-8")

            runner = RecordingRunner()
            result = workflow.run_analysis_and_extraction(
                jobs_dir=jobs_dir,
                results_dir=results_dir,
                abaqus_template="abaqus job={job_name} input={inp_path}",
                extract_template="python extract.py --odb {odb_path} --out {results_dir}",
                runner=runner,
            )

            expected_analysis = [
                ["abaqus", "job=case1", f"input={jobs_dir/'case1.inp'}"],
                ["abaqus", "job=case2", f"input={jobs_dir/'case2.inp'}"],
            ]
            expected_extraction = [
                [
                    "python",
                    "extract.py",
                    "--odb",
                    f"{jobs_dir/'case1.odb'}",
                    "--out",
                    f"{results_dir}",
                ],
                [
                    "python",
                    "extract.py",
                    "--odb",
                    f"{jobs_dir/'case2.odb'}",
                    "--out",
                    f"{results_dir}",
                ],
            ]

            self.assertEqual(result["analysis"], expected_analysis)
            self.assertEqual(result["extraction"], expected_extraction)

            executed = [call[0] for call in runner.calls]
            self.assertEqual(executed, expected_analysis + expected_extraction)


class WorkflowReselectionTests(unittest.TestCase):
    def test_run_analysis_and_extraction_uses_initial_target_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir)
            results_dir = jobs_dir / "extracts"
            first = jobs_dir / "keep.inp"
            second = jobs_dir / "delete.inp"
            first.write_text("*HEADING\n", encoding="utf-8")
            second.write_text("*HEADING\n", encoding="utf-8")

            class DeletingRunner(RecordingRunner):
                def __call__(self, command, cwd=None, check=True):
                    super().__call__(command, cwd, check)
                    # Remove one .inp file after the first analysis command to
                    # verify that extraction still targets the originally
                    # resolved list.
                    target = second
                    if target.exists():
                        target.unlink()
                    return None

            runner = DeletingRunner()
            result = workflow.run_analysis_and_extraction(
                jobs_dir=jobs_dir,
                results_dir=results_dir,
                abaqus_template="abaqus job={job_name} input={inp_path}",
                extract_template="python extract.py --odb {odb_path} --out {results_dir}",
                runner=runner,
            )

            self.assertEqual(len(result["analysis"]), 2)
            self.assertEqual(len(result["extraction"]), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
