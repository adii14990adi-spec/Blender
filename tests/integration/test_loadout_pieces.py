"""End-to-end run of the multi-piece loadout outfit.

The jacket/parka tests prove the single-garment path; this file proves the
``pieces``/``details`` path actually produces a skinned, textured, validated
export: four garment pieces, ten hard details, six materials, one GLB.
"""

from __future__ import annotations

import json
import os

import pytest
from conftest import VARIANTS, requires_bpy

LOADOUT = "tactical_loadout_01"
EXPECTED_STAGES = ["shell", "shrinkwrap", "weights", "uv_material", "export"]

pytestmark = [pytest.mark.integration, requires_bpy]


@pytest.fixture(scope="module")
def loadout_results():
    from bpy_scripts.pipeline import run_full_pipeline

    results = {}
    for variant in VARIANTS:
        results[variant] = run_full_pipeline(
            "", region="MULTI", params={"variant": variant, "outfit": LOADOUT}
        )
    return results


def _stage(result, name):
    for stage in result.get("stages", []):
        if stage["stage"] == name:
            return stage
    raise AssertionError(f"stage {name!r} missing from {[s['stage'] for s in result['stages']]}")


@pytest.mark.parametrize("variant", VARIANTS)
def test_loadout_pipeline_passes(loadout_results, variant):
    result = loadout_results[variant]
    assert result["status"] == "pass", json.dumps(result, indent=2)[-4000:]
    assert [stage["stage"] for stage in result["stages"]] == EXPECTED_STAGES
    assert all(stage["status"] == "pass" for stage in result["stages"])


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_piece_is_cut_fitted_and_measured(loadout_results, variant):
    shell = _stage(loadout_results[variant], "shell")["metrics"]
    pieces = {piece["key"]: piece for piece in shell["pieces"]}

    assert set(pieces) == {"tee", "cargo", "belt", "boots"}
    assert shell["piece_count"] == 4
    assert shell["detail_count"] == len(shell["details"]) >= 10

    for key, piece in pieces.items():
        assert piece["stats"]["face_count"] >= 100, key
        assert piece["pinned_vertices"] > 0, f"{key} has no pinned boundary vertices"
    # Region-specific cuts: a tee covers the arms, the trousers cannot.
    assert pieces["tee"]["limits"]["sleeve_cut"] > 0
    assert pieces["cargo"]["limits"]["sleeve_cut"] == 0
    assert pieces["boots"]["coverage"]["bottom"] > pieces["cargo"]["coverage"]["bottom"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_pieces_are_joined_into_one_garment_object(loadout_results, variant):
    fit = _stage(loadout_results[variant], "shrinkwrap")["metrics"]

    assert len(fit["pieces"]) == 4
    # Every piece after the first is joined into CLOTH_Shell, plus the details.
    assert fit["joined"]["joined_objects"] == (len(fit["pieces"]) - 1) + len(fit["detail_objects"])
    assert fit["garment_stats"]["face_count"] > 4000
    assert set(fit["material_slots"]) == {
        "MAT_Cloth_tactical_tee",
        "MAT_Cloth_tactical_cargo",
        "MAT_Cloth_tactical_webbing",
        "MAT_Cloth_tactical_boot",
        "MAT_Cloth_tactical_gear",
        "MAT_Cloth_tactical_patch",
    }
    # Watertight and positively oriented on every piece, not just on the union.
    for piece in fit["pieces"]:
        assert piece["shell"]["boundary_edges"] == 0, piece["key"]
        assert piece["shell"]["non_manifold_edges"] == 0, piece["key"]
        assert piece["shell"]["volume_m3"] > 0, piece["key"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_material_has_its_own_packed_texture_set(loadout_results, variant):
    material = _stage(loadout_results[variant], "uv_material")["metrics"]

    assert material["material_count"] == 6
    assert len(material["materials"]) == 6
    assert len({entry["name"] for entry in material["materials"]}) == 6
    for entry in material["materials"]:
        assert all(entry["texture_nodes"].values()), entry["name"]
        assert entry["provenance"]["base_color"] in {"existing", "procedural"}

    uv = material["uv"]
    assert uv["uv_layer_count"] == 1
    assert uv["coverage"] >= 0.05
    assert uv["nan_loops"] == 0
    assert uv["degenerate_faces"] == 0


@pytest.mark.parametrize("variant", VARIANTS)
def test_loadout_exports_are_game_ready(loadout_results, variant):
    export = _stage(loadout_results[variant], "export")["metrics"]

    glb = export["glb_validation"]
    assert glb["ok"] is True, glb["errors"]
    stats = glb["stats"]
    assert stats["has_joints"] and stats["has_weights"]
    assert stats["skins"] == 1
    assert stats["joints"][0] >= 15
    assert stats["weight_sum_violations"] == 0
    assert stats["influence_violations"] == 0
    assert stats["unweighted_vertices_sampled"] == 0
    assert stats["triangles"] > 8000, "a loadout is more geometry than a jacket"
    assert stats["images"] >= 6, "one base-colour map per material at the very least"

    fbx = export["fbx_validation"]
    assert fbx["ok"] is True, fbx["errors"]
    assert fbx["stats"]["skin_deformers"] == 1

    artifacts = export["artifacts"]
    for kind in ("fbx", "glb", "final_blend"):
        assert os.path.exists(artifacts[kind]), artifacts[kind]
