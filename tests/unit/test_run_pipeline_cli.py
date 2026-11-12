"""CLI contract tests for ``blender/run_pipeline.py``.

These run under plain pytest (no Blender) and lock in the behaviour that made
the last CI run confusing:

* a bad/placeholder base body fails with an actionable message and exit 1;
* a Python-level failure exits 1 instead of 0, so CI goes red at that step;
* a fallback GLB is only written on explicit request and is always flagged.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "blender" / "run_pipeline.py"

try:  # bpy-based stages need a real Blender runtime, these tests don't
    import bpy  # noqa: F401

    HAS_BPY = True
except Exception:  # any import failure means "no bpy here"
    HAS_BPY = False

requires_no_bpy = pytest.mark.skipif(
    HAS_BPY, reason="expects a Python environment without bpy (plain pytest)"
)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )


def _fake_blend(path: Path) -> Path:
    """A file with a valid .blend magic header but no usable contents."""
    path.write_bytes(b"BLENDER-v300" + b"\x00" * 64)
    return path


@pytest.mark.unit
@requires_no_bpy
def test_placeholder_base_body_fails_with_actionable_message(tmp_path: Path) -> None:
    blend = tmp_path / "BASE_BODY.blend"
    blend.write_text("# PLACEHOLDER — replace with a real Blender file\n")
    out = tmp_path / "result.json"

    proc = _run_cli("--stage", "validate", "--blend", str(blend), "--out", str(out))

    assert proc.returncode == 1, proc.stderr
    result = json.loads(out.read_text())
    assert result["status"] == "fail"
    assert "not a real .blend file" in result["error"]
    assert "placeholder" in result["error"].lower()


@pytest.mark.unit
@requires_no_bpy
def test_missing_base_body_fails(tmp_path: Path) -> None:
    out = tmp_path / "result.json"
    proc = _run_cli(
        "--stage",
        "validate",
        "--blend",
        str(tmp_path / "nope.blend"),
        "--out",
        str(out),
    )

    assert proc.returncode == 1
    result = json.loads(out.read_text())
    assert result["status"] == "fail"
    assert "base body not found" in result["error"]


@pytest.mark.unit
@requires_no_bpy
def test_pipeline_imports_and_reports_missing_bpy_instead_of_crashing(tmp_path: Path) -> None:
    """The exact regression: import errors must not be swallowed as a fake pass."""
    blend = _fake_blend(tmp_path / "run_pipeline_cli_test.blend")
    out = tmp_path / "result.json"
    outputs = REPO_ROOT / "outputs" / "run_pipeline_cli_test"
    try:
        proc = _run_cli("--stage", "validate", "--blend", str(blend), "--out", str(out))
        assert proc.returncode == 1, proc.stderr

        result = json.loads(out.read_text())
        payload = json.dumps(result)
        assert result["status"] == "fail"
        assert "attempted relative import" not in payload
        assert "failed to import bpy_scripts.pipeline" not in payload

        # Validators ran; they report that bpy is unavailable outside Blender.
        validators = result["metrics"]["validators"]
        assert validators
        assert all(v["status"] == "fail" for v in validators.values())
    finally:
        shutil.rmtree(outputs, ignore_errors=True)


@pytest.mark.unit
def test_unknown_stage_fails() -> None:
    proc = _run_cli("--stage", "not-a-stage", "--out", "/tmp/unknown_stage.json")
    assert proc.returncode == 1
    assert "unknown stage" in proc.stderr


@pytest.mark.unit
def test_fallback_glb_is_opt_in_and_flagged(tmp_path: Path) -> None:
    blend = _fake_blend(tmp_path / "fallback_test.blend")
    out = tmp_path / "result.json"
    outputs = REPO_ROOT / "outputs" / "fallback_test"
    try:
        proc = _run_cli(
            "--stage",
            "validate",
            "--blend",
            str(blend),
            "--out",
            str(out),
            "--allow-fallback-glb",
        )
        assert proc.returncode == 1
        metrics = json.loads(out.read_text())["metrics"]
        assert metrics["glb_is_fallback"] is True
        assert Path(metrics["glb_path"]).exists()
        assert metrics["glb_file_size_bytes"] > 0
    finally:
        shutil.rmtree(outputs, ignore_errors=True)


@pytest.mark.unit
def test_no_fallback_glb_by_default(tmp_path: Path) -> None:
    blend = _fake_blend(tmp_path / "no_fallback_test.blend")
    out = tmp_path / "result.json"
    outputs = REPO_ROOT / "outputs" / "no_fallback_test"
    try:
        proc = _run_cli("--stage", "validate", "--blend", str(blend), "--out", str(out))
        assert proc.returncode == 1
        metrics = json.loads(out.read_text())["metrics"]
        assert metrics["glb_is_fallback"] is False
        assert metrics["glb_path"] is None
        # The validator report may exist, but no .glb may be written.
        assert not list(outputs.glob("*.glb"))
    finally:
        shutil.rmtree(outputs, ignore_errors=True)
