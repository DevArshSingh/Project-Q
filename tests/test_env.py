"""Phase 0 smoke tests: the environment has everything later phases need."""

import shutil
import subprocess
import sys

import pytest


def test_python_is_312():
    # CLAUDE.md: the project runs on Python 3.12 from .venv, not the system 3.14.
    assert sys.version_info[:2] == (3, 12)


def test_core_imports():
    import matplotlib  # noqa: F401
    import networkx  # noqa: F401
    import numpy  # noqa: F401
    import qiskit  # noqa: F401
    import qiskit_aer  # noqa: F401
    import qiskit_ibm_runtime  # noqa: F401
    import scipy  # noqa: F401

    import project_q

    assert project_q.__version__


def test_opt_mapper_import():
    # Optional by design: if the add-on breaks, we build the Hamiltonian by hand (SPEC.md).
    pytest.importorskip("qiskit_addon_opt_mapper")


def test_yosys_available():
    assert shutil.which("yosys"), "Yosys not found on PATH; install it (e.g. `sudo apt install yosys`)."
    out = subprocess.run(["yosys", "-V"], capture_output=True, text=True, check=True)
    assert "Yosys" in out.stdout


def test_cli_help_runs():
    out = subprocess.run(
        [sys.executable, "-m", "project_q.cli", "--help"], capture_output=True, text=True, check=True
    )
    assert "partition" in out.stdout and "benchmark" in out.stdout
