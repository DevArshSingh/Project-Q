"""The notebook deliverable: up to date, well-formed, and runs top to bottom without Yosys."""

import importlib.util
import sys
from pathlib import Path

import nbformat
import pytest
from nbconvert.preprocessors import ExecutePreprocessor

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "project_q.ipynb"


def _builder():
    spec = importlib.util.spec_from_file_location("build_notebook", ROOT / "scripts" / "build_notebook.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_notebook_matches_builder_script():
    # Edit scripts/build_notebook.py, not the .ipynb; then run it. This catches forgetting either step.
    assert nbformat.read(NOTEBOOK, as_version=4) == _builder().build(), "run: python scripts/build_notebook.py"


def test_every_code_cell_has_markdown_before_it():
    cells = nbformat.read(NOTEBOOK, as_version=4).cells
    assert cells[0].cell_type == "markdown"
    for prev, cell in zip(cells, cells[1:]):
        if cell.cell_type == "code":
            assert prev.cell_type == "markdown", f"code cell {cell.id} has no explanation before it"


def test_notebook_runs_without_yosys(monkeypatch):
    # PATH = only the venv's bin dir, so `shutil.which("yosys")` fails inside the kernel,
    # exactly like a judge's laptop. Yosys lives in /usr/bin, which is not included.
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent))
    nb = nbformat.read(NOTEBOOK, as_version=4)
    ExecutePreprocessor(timeout=300, kernel_name="python3").preprocess(nb, {"metadata": {"path": str(NOTEBOOK.parent)}})

    text = "\n".join(o.get("text", "") for c in nb.cells if c.cell_type == "code" for o in c.outputs)
    assert "Yosys not installed" in text
    assert "64/64" in text
    assert "λ = 2.0: best balanced cut 2, lowest-energy state is optimal: True, ΔE = 1.0" in text
    assert "20 balanced splits" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
