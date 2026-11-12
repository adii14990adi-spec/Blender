"""Mocked full-pipeline test: stage N+1 must open stage N's checkpoint.

This is the acceptance check for the second CI bug: the export stage ran
against the raw ``BASE_BODY.blend``, which never contains a ``CLOTH_Shell``
object because only the shell stage creates it. Mocked stages keep this test
Blender-free.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest
import run_pipeline

from bpy_scripts import pipeline

BASE_BLEND = "assets/base_body/BASE_BODY.blend"
OUTFIT = "jacket_urban_01"


def _fake_stage(name: str, index: int, calls: list[tuple[str, str]], status: str = "pass"):
    def run(blend_path, region="UPPER_BODY", params=None, outfit=None):
        calls.append((name, blend_path))
        if status != "pass":
            return {
                "status": "fail",
                "stage": name,
                "metrics": {"error": f"{name} failed on purpose"},
                "checkpoint": None,
            }
        return {
            "status": "pass",
            "stage": name,
            "metrics": {},
            "checkpoint": f"/tmp/{outfit}/{index:02d}_{name}.blend",
        }

    return types.SimpleNamespace(run=run)


def _patch(monkeypatch, calls, tmp_path: Path, failing: str | None = None):
    registry = {
        name: _fake_stage(
            name,
            index,
            calls,
            status="fail" if name == failing else "pass",
        )
        for index, name in enumerate(pipeline.STAGE_ORDER)
    }
    monkeypatch.setattr(pipeline, "STAGE_REGISTRY", registry)
    monkeypatch.setattr(
        pipeline,
        "validation_report_path",
        lambda outfit: str(tmp_path / outfit / "validation_report.json"),
    )


@pytest.mark.unit
def test_full_pipeline_passes_each_checkpoint_to_the_next_stage(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str]] = []
    _patch(monkeypatch, calls, tmp_path)

    result = pipeline.run_full_pipeline(BASE_BLEND, "UPPER_BODY", {"outfit": OUTFIT})

    assert result["status"] == "pass"
    assert result["outfit"] == OUTFIT

    expected = [BASE_BLEND]
    expected += [
        f"/tmp/{OUTFIT}/{index:02d}_{name}.blend"
        for index, name in enumerate(pipeline.STAGE_ORDER[:-1])
    ]
    assert [path for _, path in calls] == expected
    assert [name for name, _ in calls] == list(pipeline.STAGE_ORDER)


@pytest.mark.unit
def test_full_pipeline_stops_at_the_first_failure(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str]] = []
    _patch(monkeypatch, calls, tmp_path, failing="weights")

    result = pipeline.run_full_pipeline(BASE_BLEND, "UPPER_BODY", {"outfit": OUTFIT})

    assert result["status"] == "fail"
    assert result["stage"] == "weights"
    assert [name for name, _ in calls] == ["shell", "shrinkwrap", "weights"]


@pytest.mark.unit
@pytest.mark.parametrize("stage_flag", ["full", "all"])
def test_cli_routes_full_run_aliases_to_run_full_pipeline(
    monkeypatch, tmp_path, stage_flag: str
) -> None:
    blend = tmp_path / "BASE_BODY.blend"
    blend.write_bytes(b"BLENDER-v300" + b"\x00" * 64)
    out = tmp_path / "result.json"

    seen: dict[str, str] = {}

    def fake_full(blend_path, region="UPPER_BODY", params=None):
        seen["blend"] = blend_path
        return {
            "status": "pass",
            "stage": "full",
            "metrics": {"staged": "full"},
            "checkpoint": None,
            "outfit": OUTFIT,
        }

    monkeypatch.setattr(pipeline, "run_full_pipeline", fake_full)

    exit_code = run_pipeline.main(["--stage", stage_flag, "--blend", str(blend), "--out", str(out)])

    assert exit_code == 0
    assert seen["blend"] == str(blend)
    payload = json.loads(out.read_text())
    assert payload["status"] == "pass"
    assert payload["metrics"]["staged"] == "full"
