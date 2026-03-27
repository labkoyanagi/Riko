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
"""Entry point for running the App-Run Streamlit application."""
from __future__ import annotations

import runpy
from pathlib import Path


def main() -> None:
    script_path = Path(__file__).with_name("job_runner_ui.py")
    runpy.run_path(str(script_path))


if __name__ == "__main__":
    main()
