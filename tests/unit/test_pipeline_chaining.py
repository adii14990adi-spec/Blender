"""Checkpoint chaining contract (mocked stages, no Blender required).

The original CI bug: the export stage ran against the raw base-body blend,
which never contains a ``CLOTH_Shell`` object because only the shell stage
creates it. These tests pin the rule that stage *N+1* always receives stage
*N*'s checkpoint, and that a stage claiming "pass" without a real file on disk
is treated as a failure instead of a fresh output.
"""

from __future__ import annotations

import json
import types

import pytest
import run_pipeline

from bpy_scripts import pipeline

BASE_BLEND = "/tmp/base_body_Male.blend"
OUTFIT = "jacket_urban_01"


def _fake_stage(name: str, index: int, calls: list, tmp_path, status: str = "pass"):
    def run(blend_path, region="UPPER_BODY", params=None, outfit=None):
        calls.append((name, blend_path))
        if status != "pass":
            return {
                "status": "fail",
                "stage": name,
                "metrics": {"error": f"{name} failed on purpose"},
                "checkpoint": None,
            }
        path = tmp_path / f"{index:02d}_{name}.blend"
        path.write_bytes(b"BLENDER" + b"\x00" * 4096)
        return {
            "status": "pass",
            "stage": name,
            "metrics": {},
            "checkpoint": str(path),
        }

    return types.SimpleNamespace(run=run)


def _patch(monkeypatch, calls, tmp_path, failing: str | None = None):
    registry = {
        name: _fake_stage(
            name,
            index,
            calls,
            tmp_path,
            status="fail" if name == failing else "pass",
        )
        for index, name in enumerate(pipeline.STAGE_ORDER)
    }
    monkeypatch.setattr(pipeline, "STAGE_REGISTRY", registry)
    monkeypatch.setattr(
        pipeline,
        "validation_report_path",
        lambda outfit, variant=None: str(
            tmp_path / (variant or "unknown") / outfit / "report.json"
        ),
    )
    monkeypatch.setattr(pipeline, "outfit_dir", lambda outfit, variant=None: str(tmp_path))


@pytest.mark.unit
def test_full_pipeline_passes_each_checkpoint_to_the_next_stage(monkeypatch, tmp_path) -> None:
    calls: list = []
    _patch(monkeypatch, calls, tmp_path)

    result = pipeline.run_full_pipeline(BASE_BLEND, "UPPER_BODY", {"outfit": OUTFIT})

    assert result["status"] == "pass", result
    assert result["outfit"] == OUTFIT
    assert [name for name, _ in calls] == list(pipeline.STAGE_ORDER)

    expected = [BASE_BLEND] + [
        str(tmp_path / f"{i:02d}_{name}.blend") for i, name in enumerate(pipeline.STAGE_ORDER[:-1])
    ]
    assert [path for _, path in calls] == expected


@pytest.mark.unit
def test_full_pipeline_stops_at_the_first_failure(monkeypatch, tmp_path) -> None:
    calls: list = []
    _patch(monkeypatch, calls, tmp_path, failing="weights")

    result = pipeline.run_full_pipeline(BASE_BLEND, "UPPER_BODY", {"outfit": OUTFIT})

    assert result["status"] == "fail"
    assert result["stage"] == "weights"
    assert [name for name, _ in calls] == ["shell", "shrinkwrap", "weights"]
    assert result["artifacts"] == {}


@pytest.mark.unit
def test_a_stage_that_reports_pass_without_a_file_is_a_failure(monkeypatch, tmp_path) -> None:
    """Stale/absent checkpoints must never be chained downstream."""
    calls: list = []
    registry = {
        name: _fake_stage(name, i, calls, tmp_path) for i, name in enumerate(pipeline.STAGE_ORDER)
    }
    broken = registry["shell"]

    def ghost_run(blend_path, region="UPPER_BODY", params=None, outfit=None):
        calls.append(("shell", blend_path))
        return {
            "status": "pass",
            "stage": "shell",
            "metrics": {},
            "checkpoint": str(tmp_path / "does_not_exist.blend"),
        }

    registry["shell"] = types.SimpleNamespace(run=ghost_run)
    monkeypatch.setattr(pipeline, "STAGE_REGISTRY", registry)
    monkeypatch.setattr(
        pipeline,
        "validation_report_path",
        lambda outfit, variant=None: str(tmp_path / "report.json"),
    )
    monkeypatch.setattr(pipeline, "outfit_dir", lambda outfit, variant=None: str(tmp_path))
    assert broken  # keep the reference alive / documents intent

    result = pipeline.run_full_pipeline(BASE_BLEND, "UPPER_BODY", {"outfit": OUTFIT})

    assert result["status"] == "fail"
    assert "checkpoint on disk" in result["error"]
    assert [name for name, _ in calls] == ["shell"]


@pytest.mark.unit
@pytest.mark.parametrize("stage_flag", ["full", "all"])
def test_cli_routes_full_run_aliases_to_run_full_pipeline(
    monkeypatch, tmp_path, stage_flag: str
) -> None:
    blend = tmp_path / "base_body_Male.blend"
    # >= config.MIN_BLEND_BYTES so the header check accepts it as a real file.
    blend.write_bytes(b"BLENDER-v300" + b"\x00" * 8192)
    out = tmp_path / "result.json"

    seen: dict = {}

    def fake_full(blend_path, region="UPPER_BODY", params=None):
        seen["blend"] = blend_path
        return {
            "status": "pass",
            "stage": "export",
            "metrics": {"staged": "full"},
            "checkpoint": None,
            "outfit": OUTFIT,
            "stages": [],
        }

    monkeypatch.setattr(pipeline, "run_full_pipeline", fake_full)

    exit_code = run_pipeline.main(["--stage", stage_flag, "--blend", str(blend), "--out", str(out)])

    assert exit_code == 0
    assert seen["blend"] == str(blend)
    payload = json.loads(out.read_text())
    assert payload["status"] == "pass"
    assert payload["metrics"]["staged"] == "full"
