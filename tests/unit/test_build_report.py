"""Unit tests for ``tools/build_report.py`` — the readable build report.

The report is the evidence artefact the project points at, so a multi-piece
outfit must be described piece by piece instead of reporting the first piece and
calling it the garment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import build_report  # noqa: E402  (path bootstrap above)


def _stage(name, metrics, status="pass"):
    return {"stage": name, "status": status, "metrics": metrics}


def _single_piece_run():
    return {
        "variant": "male",
        "outfit": "jacket_urban_01",
        "status": "pass",
        "stages": [
            _stage(
                "shell",
                {
                    "garment_stats": {"face_count": 3318},
                    "body_stats": {"face_count": 12812},
                    "patch": {"opening_faces_removed": 120},
                },
            ),
            _stage(
                "shrinkwrap",
                {
                    "fit": {
                        "min_clearance": 0.0063,
                        "mean_clearance": 0.009,
                        "max_clearance": 0.012,
                        "penetrating_vertices": 0,
                        "edge_distortion": {"max": 1.2, "p99": 0.2},
                    },
                    "shell": {
                        "vertex_count": 6930,
                        "face_count": 6930,
                        "thickness": 0.004,
                        "volume_m3": 0.01,
                        "boundary_edges": 0,
                        "non_manifold_edges": 0,
                        "degenerate_faces": 0,
                    },
                },
            ),
        ],
        "artifacts": {"glb": "/tmp/nope.glb"},
    }


def _loadout_run():
    run = _single_piece_run()
    run["outfit"] = "tactical_loadout_01"
    run["stages"][0] = _stage(
        "shell",
        {
            "garment_stats": {"face_count": 6376},
            "body_stats": {"face_count": 12812},
            "patch": {"opening_faces_removed": 0},
            "details": [{"id": f"pouch_{index}"} for index in range(3)],
        },
    )
    run["stages"][1] = _stage(
        "shrinkwrap",
        {
            "fit": {
                "min_clearance": 0.0002,
                "mean_clearance": 0.01,
                "max_clearance": 0.015,
                "penetrating_vertices": 0,
                "edge_distortion": {"max": 0.9, "p99": 0.17},
                "pieces": [
                    {
                        "key": "tee",
                        "region": "UPPER_BODY",
                        "fit_mode": "surface",
                        "min_clearance": 0.009,
                    },
                    {"key": "boots", "region": "FOOTWEAR", "fit_mode": "shell"},
                ],
            },
            "shell": {
                "vertex_count": 2870,
                "face_count": 5336,
                "thickness": 0.004,
                "volume_m3": 0.01,
                "boundary_edges": 0,
                "non_manifold_edges": 0,
                "degenerate_faces": 0,
            },
            "pieces": [
                {
                    "key": "tee",
                    "region": "UPPER_BODY",
                    "fit_mode": "surface",
                    "fit": {"min_clearance": 0.009, "penetrating_vertices": 0},
                    "shell": {"face_count": 5336, "boundary_edges": 0, "non_manifold_edges": 0},
                },
                {
                    "key": "boots",
                    "region": "FOOTWEAR",
                    "fit_mode": "shell",
                    "fit": {"min_clearance": 0.0026, "penetrating_vertices": 2},
                    "shell": {"face_count": 3402, "boundary_edges": 0, "non_manifold_edges": 0},
                },
            ],
        },
    )
    run["stages"].append(
        _stage(
            "uv_material",
            {
                "material_count": 6,
                "materials": [
                    {"name": "MAT_Cloth_tactical_tee"},
                    {"name": "MAT_Cloth_tactical_boot"},
                ],
            },
        )
    )
    return run


@pytest.mark.unit
def test_single_garment_report_has_no_piece_table():
    summary = build_report.summarise(_single_piece_run())
    assert summary["pieces"] == []
    assert summary["material_count"] == 1

    markdown = build_report.to_markdown([summary], "2026-01-01 00:00 UTC")
    assert "Multi-piece outfit" not in markdown
    assert "| Front-opening faces removed | 120 |" in markdown
    assert "## male · jacket_urban_01 — PASS" in markdown


@pytest.mark.unit
def test_loadout_report_lists_every_piece_and_material():
    summary = build_report.summarise(_loadout_run())
    assert [piece["key"] for piece in summary["pieces"]] == ["tee", "boots"]
    assert summary["pieces"][1]["penetrating"] == 2
    assert summary["pieces"][1]["fit_mode"] == "shell"
    assert summary["detail_count"] == 3
    assert summary["material_count"] == 6

    markdown = build_report.to_markdown([summary], "2026-01-01 00:00 UTC")
    assert "Multi-piece outfit — 2 pieces, 3 hard details, 6 materials" in markdown
    assert "| `boots` | FOOTWEAR | shell | 3402 | 0.0026 m | 2 | yes |" in markdown
    assert "Materials: `MAT_Cloth_tactical_tee`, `MAT_Cloth_tactical_boot`" in markdown
    assert "| Material slots | 6 |" in markdown


@pytest.mark.unit
def test_report_orders_runs_and_marks_failures():
    failed = _single_piece_run()
    failed["status"] = "fail"
    failed["error"] = "garment penetrates the body"
    markdown = build_report.to_markdown(
        [build_report.summarise(_single_piece_run()), build_report.summarise(failed)],
        "2026-01-01 00:00 UTC",
    )
    assert "**Overall: FAIL**" in markdown
    assert "> **Error:** garment penetrates the body" in markdown
    assert markdown.index("## male · jacket_urban_01 — PASS") < markdown.index(
        "## male · jacket_urban_01 — FAIL"
    )
