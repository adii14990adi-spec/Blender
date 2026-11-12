"""CLI contract for ``blender/run_pipeline.py`` (no Blender required).

Locks in the behaviour that made the previous CI confusing:

* a missing/placeholder base body fails with an actionable message and exit 1;
* ``--allow-fallback-glb`` is gone: a build must never publish a placeholder;
* every failure path writes a JSON report *and* exits non-zero, so a green job
  always means a real, validated build.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "blender" / "run_pipeline.py"


def _run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **dict(__import__("os").environ),
        "PIPELINE_OUTPUTS_ROOT": str(tmp_path / "outputs"),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "--out", str(tmp_path / "result.json"), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
        env=env,
    )


def _result(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "result.json").read_text())


@pytest.mark.unit
def test_missing_blend_fails_with_an_actionable_message(tmp_path: Path) -> None:
    proc = _run_cli(tmp_path, "--stage", "validate", "--blend", str(tmp_path / "nope.blend"))

    assert proc.returncode == 1, proc.stdout + proc.stderr
    result = _result(tmp_path)
    assert result["status"] == "fail"
    assert "not found" in (result["error"] or "").lower()


@pytest.mark.unit
def test_placeholder_blend_is_rejected(tmp_path: Path) -> None:
    blend = tmp_path / "base_body_Male.blend"
    blend.write_text("# PLACEHOLDER - replace with a real Blender file\n")

    proc = _run_cli(tmp_path, "--stage", "validate", "--blend", str(blend))

    assert proc.returncode == 1
    result = _result(tmp_path)
    assert result["status"] == "fail"
    assert "not a blender file" in result["error"].lower()


@pytest.mark.unit
def test_unknown_stage_exits_non_zero(tmp_path: Path) -> None:
    proc = _run_cli(tmp_path, "--stage", "not-a-stage")

    assert proc.returncode == 1
    assert "unknown stage" in proc.stderr


@pytest.mark.unit
def test_fallback_glb_flag_is_rejected(tmp_path: Path) -> None:
    """The custom placeholder GLB writer is gone; asking for it must fail loudly."""
    proc = _run_cli(tmp_path, "--stage", "validate", "--allow-fallback-glb")

    assert proc.returncode == 1
    result = _result(tmp_path)
    assert result["status"] == "fail"
    assert "--allow-fallback-glb" in result["error"]
    assert not list((tmp_path / "outputs").rglob("*.glb"))


@pytest.mark.unit
def test_list_assets_reports_both_body_variants(tmp_path: Path) -> None:
    proc = _run_cli(tmp_path, "--list-assets")

    assert proc.returncode == 0, proc.stderr
    assert "male" in proc.stdout and "female" in proc.stdout
    assert "jacket_urban_01" in proc.stdout


@pytest.mark.unit
def test_no_base_body_blend_assumption_remains(tmp_path: Path) -> None:
    """Resolution must come from the manifest, not a hard-coded BASE_BODY.blend."""
    proc = _run_cli(tmp_path, "--list-assets", "--variant", "female")

    assert proc.returncode == 0, proc.stderr
    assert "female" in proc.stdout
