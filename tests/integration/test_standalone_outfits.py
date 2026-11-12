"""Standalone garments whose textures come from disk, not from the generator.

`tactical_tee_01` (tee + chest patch) and `tactical_cargo_01` (cargo trousers
with thigh pockets) are single-garment outfits that declare
``"procedural": {"enabled": false}``: every map is committed under
``assets/textures/<outfit>/`` and the pipeline must use it verbatim. If a map
went missing the build would fail, which is the point — no silent placeholders.
"""

from __future__ import annotations

import json
import os

import pytest
from conftest import VARIANTS, requires_bpy

OUTFITS = {
    "tactical_tee_01": {"materials": 2, "details": 1, "min_triangles": 4000},
    "tactical_cargo_01": {"materials": 1, "details": 2, "min_triangles": 4000},
}
EXPECTED_STAGES = ["shell", "shrinkwrap", "weights", "uv_material", "export"]

pytestmark = [pytest.mark.integration, requires_bpy]


@pytest.fixture(scope="module")
def builds():
    from bpy_scripts.pipeline import run_full_pipeline

    results = {}
    for outfit in OUTFITS:
        for variant in VARIANTS:
            results[(variant, outfit)] = run_full_pipeline(
                "", region="MULTI", params={"variant": variant, "outfit": outfit}
            )
    return results


def _stage(result, name):
    for stage in result.get("stages", []):
        if stage["stage"] == name:
            return stage
    raise AssertionError(f"stage {name!r} missing")


@pytest.mark.parametrize("outfit", sorted(OUTFITS))
@pytest.mark.parametrize("variant", VARIANTS)
def test_standalone_outfit_passes_every_stage(builds, variant, outfit):
    result = builds[(variant, outfit)]
    assert result["status"] == "pass", json.dumps(result, indent=2)[-4000:]
    assert [stage["stage"] for stage in result["stages"]] == EXPECTED_STAGES
    assert all(stage["status"] == "pass" for stage in result["stages"])


@pytest.mark.parametrize("outfit", sorted(OUTFITS))
@pytest.mark.parametrize("variant", VARIANTS)
def test_committed_textures_are_used_instead_of_generated(builds, variant, outfit):
    material = _stage(builds[(variant, outfit)], "uv_material")["metrics"]

    entries = material.get("materials") or [material.get("material") or {}]
    assert len(entries) == OUTFITS[outfit]["materials"]
    for entry in entries:
        provenance = entry.get("provenance") or {}
        assert provenance, f"{entry.get('name')} reports no provenance"
        assert set(provenance.values()) == {"existing"}, (
            f"{entry.get('name')} fell back to generated maps: {provenance}"
        )
        assert all(entry.get("texture_nodes", {}).values()), entry.get("name")

    # Every map that shipped must be packed into the .blend as well.
    slots = (material.get("textures") or {}).get("slots") or {}
    if slots:
        assert all(slot.get("packed") for slot in slots.values()), slots


@pytest.mark.parametrize("outfit", sorted(OUTFITS))
@pytest.mark.parametrize("variant", VARIANTS)
def test_standalone_outfit_is_a_fitted_watertight_garment(builds, variant, outfit):
    shell = _stage(builds[(variant, outfit)], "shell")["metrics"]
    fit = _stage(builds[(variant, outfit)], "shrinkwrap")["metrics"]

    assert shell["detail_count"] == OUTFITS[outfit]["details"]
    assert fit["shell"]["boundary_edges"] == 0
    assert fit["shell"]["non_manifold_edges"] == 0
    assert fit["shell"]["volume_m3"] > 0
    assert fit["fit"]["min_clearance"] > 0
    assert fit["fit"]["penetrating_vertices"] == 0
    assert len(fit["material_slots"]) == OUTFITS[outfit]["materials"]


@pytest.mark.parametrize("outfit", sorted(OUTFITS))
@pytest.mark.parametrize("variant", VARIANTS)
def test_standalone_outfit_exports_a_game_ready_asset(builds, variant, outfit):
    export = _stage(builds[(variant, outfit)], "export")["metrics"]
    glb = export["glb_validation"]
    assert glb["ok"] is True, glb["errors"]

    stats = glb["stats"]
    assert stats["has_joints"] and stats["has_weights"]
    assert stats["has_uvs"] and stats["has_tangents"]
    assert stats["weight_sum_violations"] == 0
    assert stats["influence_violations"] == 0
    assert stats["triangles"] > OUTFITS[outfit]["min_triangles"]
    assert stats["materials"] == OUTFITS[outfit]["materials"]
    assert stats["images"] >= OUTFITS[outfit]["materials"] * 3

    fbx = export["fbx_validation"]
    assert fbx["ok"] is True, fbx["errors"]

    khronos = export.get("khronos_validator") or {}
    if khronos.get("status") == "pass":
        assert khronos.get("numErrors") == 0

    for kind in ("fbx", "glb", "final_blend"):
        assert os.path.exists(export["artifacts"][kind])
