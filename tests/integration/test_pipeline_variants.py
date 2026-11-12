"""Real end-to-end pipeline runs for both body variants.

These are the tests that matter: they execute the actual stages inside Blender
5.0 and then inspect the files that were written. There is no mocking and no
"assert it did not crash" — a stage that produces a bad garment, a bad rig or a
bad export fails here.

Run with::

    pytest tests/integration -m integration            # bpy wheel installed
    blender -b -P tools/run_pytest.py -- tests/integration
"""

from __future__ import annotations

import json
import os

import pytest
from conftest import OUTFIT, VARIANTS, requires_bpy

EXPECTED_STAGES = ["shell", "shrinkwrap", "weights", "uv_material", "export"]

pytestmark = [pytest.mark.integration, requires_bpy]


def _stage(result, name):
    for stage in result.get("stages", []):
        if stage["stage"] == name:
            return stage
    raise AssertionError(
        f"stage {name!r} missing from {[s['stage'] for s in result.get('stages', [])]}"
    )


@pytest.mark.parametrize("variant", VARIANTS)
def test_full_pipeline_passes_for_variant(pipeline_results, variant):
    result = pipeline_results[variant]

    assert result["status"] == "pass", json.dumps(result, indent=2)[-4000:]
    assert result["variant"] == variant
    assert result["outfit"] == OUTFIT
    assert [stage["stage"] for stage in result["stages"]] == EXPECTED_STAGES
    assert all(stage["status"] == "pass" for stage in result["stages"]), result["stages"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_stage_wrote_a_non_empty_checkpoint(pipeline_results, variant):
    for stage in pipeline_results[variant]["stages"]:
        path = stage["checkpoint"]
        assert path and os.path.exists(path), f"{stage['stage']} produced no checkpoint"
        assert os.path.getsize(path) > 100_000, f"{stage['stage']} checkpoint is suspiciously small"


@pytest.mark.parametrize("variant", VARIANTS)
def test_variants_do_not_share_an_output_directory(pipeline_results, variant):
    """male/jacket_urban_01 and female/jacket_urban_01 must not overwrite each other."""
    other = "female" if variant == "male" else "male"
    mine = pipeline_results[variant]["artifacts"]["glb"]
    theirs = pipeline_results[other]["artifacts"]["glb"]
    assert mine != theirs
    assert f"/{variant}/" in mine.replace(os.sep, "/")
    assert f"/{other}/" in theirs.replace(os.sep, "/")
    assert os.path.exists(mine) and os.path.exists(theirs)


@pytest.mark.parametrize("variant", VARIANTS)
def test_garment_is_a_real_fitted_shell(pipeline_results, variant):
    """The garment must not be a copy of the body, and it must stay outside it."""
    shell = _stage(pipeline_results[variant], "shell")["metrics"]
    fit = _stage(pipeline_results[variant], "shrinkwrap")["metrics"]

    garment = shell["garment_stats"]
    body = shell["body_stats"]
    assert garment["face_count"] >= 500, garment
    assert garment["face_count"] != body["face_count"], "garment is a duplicate of the body mesh"
    assert shell["garment_source"] == "body_region_cut"
    assert shell["patch"]["opening_faces_removed"] > 0, "the garment front was never opened"
    assert shell["pinned_vertices"] > 0

    fitted = fit["fit"]
    assert fitted["penetrating_vertices"] == 0
    assert fitted["min_clearance"] > 0, fitted
    assert fitted["edge_distortion"]["max"] <= 3.0, fitted["edge_distortion"]

    solidify = fit["shell"]
    assert solidify["solidify_applied"] is True
    assert solidify["non_manifold_edges"] == 0
    assert solidify["boundary_edges"] == 0
    assert solidify["volume_m3"] > 0
    assert solidify["degenerate_faces"] == 0
    assert 0.002 <= solidify["thickness"] <= 0.02, solidify["thickness"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_rig_is_attached_and_weights_are_valid(pipeline_results, variant):
    metrics = _stage(pipeline_results[variant], "weights")["metrics"]
    skin = metrics["skin"]

    assert skin["armature"], "no armature bound to the garment"
    assert len(skin["deform_bones"]) >= 15
    assert skin["verts_transferred"] > 500
    assert skin["max_influences"] <= 4, skin["influence_histogram"]
    assert skin["mean_influences"] > 0

    verification = skin["verification"]
    assert verification["ok"] is True, verification
    assert verification["unweighted_vertices"] == 0
    assert verification["unnormalised_vertices"] == 0
    assert verification["vertices_exceeding_limit"] == 0
    # Helper groups may exist, but never as deform bones.
    assert verification["helper_groups_colliding_with_bones"] == []
    assert "UN_SHRINK" in verification["helper_groups"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_uv_and_pbr_material_are_real(pipeline_results, variant):
    metrics = _stage(pipeline_results[variant], "uv_material")["metrics"]
    uv = metrics["uv"]

    assert uv["uv_layer_count"] == 1, "duplicate UV layers"
    assert uv["has_uv"] is True
    assert uv["coverage"] >= 0.05
    assert uv["nan_loops"] == 0
    assert uv["loops_outside_0_1"] == 0
    assert uv["degenerate_faces"] == 0
    assert uv["method"] in {"smart_project", "angle_based_with_seams"}

    material = metrics["material"]
    assert all(material["texture_nodes"].values()), material["texture_nodes"]

    textures = metrics["textures"]
    assert textures["ok"] is True, textures["problems"]
    for slot in ("base_color", "normal", "roughness", "metallic"):
        entry = textures["slots"][slot]
        assert entry["size"][0] >= 64 and entry["size"][1] >= 64
        assert entry["packed"] is True, f"{slot} texture is not packed into the .blend"
    # A blank base colour map is the classic "texture exists but the asset is black" bug.
    assert max(textures["slots"]["base_color"]["mean"]) > 0.01
    assert max(textures["slots"]["base_color"]["std"]) > 0.002
    assert textures["slots"]["normal"]["mean"][2] > 0.5


@pytest.mark.parametrize("variant", VARIANTS)
def test_exports_are_written_and_validated(pipeline_results, variant):
    metrics = _stage(pipeline_results[variant], "export")["metrics"]

    glb = metrics["glb_validation"]
    assert glb["ok"] is True, glb["errors"]
    stats = glb["stats"]
    assert (
        stats["has_normals"] and stats["has_uvs"] and stats["has_joints"] and stats["has_weights"]
    )
    assert stats["has_tangents"], "normal-mapped material must ship a tangent space"
    assert stats["skins"] == 1
    assert stats["joints"] and stats["joints"][0] >= 15
    assert stats["images"] >= 3, stats
    assert stats["weight_sum_violations"] == 0
    assert stats["influence_violations"] == 0
    assert stats["unweighted_vertices_sampled"] == 0
    assert stats["triangles"] > 1000

    fbx = metrics["fbx_validation"]
    assert fbx["ok"] is True, fbx["errors"]
    fbx_stats = fbx["stats"]
    assert fbx_stats["skin_deformers"] == 1
    assert fbx_stats["clusters"] >= 15
    assert fbx_stats["uv_layers"] >= 1
    assert fbx_stats["normal_layers"] >= 1
    assert fbx_stats["mesh_geometry_count"] == 1

    artifacts = metrics["artifacts"]
    for kind in ("fbx", "glb", "final_blend"):
        assert os.path.exists(artifacts[kind]), artifacts[kind]


@pytest.mark.parametrize("variant", VARIANTS)
def test_validate_stage_passes_on_the_built_checkpoint(pipeline_results, variant):
    from bpy_scripts import pipeline

    report = pipeline.run_validate("", "UPPER_BODY", {"variant": variant, "outfit": OUTFIT})

    assert report["status"] == "pass", json.dumps(report, indent=2)[-4000:]
    validators = report["metrics"]["validators"]
    assert set(validators) == {
        "preflight",
        "weights_check",
        "topology_check",
        "uv_check",
        "export_check",
    }
    assert all(v["status"] == "pass" for v in validators.values()), {
        k: v.get("metrics", {}).get("problems") for k, v in validators.items()
    }
    assert report["metrics"]["validated_source"] == "checkpoint"
