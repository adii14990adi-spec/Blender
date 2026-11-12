"""Path layout: male and female builds must never share or clobber a directory."""

from __future__ import annotations

import os

import pytest

from bpy_scripts.utils import paths


@pytest.mark.unit
def test_variant_scoped_output_directories_differ():
    male = paths.outfit_dir("jacket_urban_01", "male")
    female = paths.outfit_dir("jacket_urban_01", "female")
    assert male != female
    assert male.endswith(os.path.join("male", "jacket_urban_01"))
    assert female.endswith(os.path.join("female", "jacket_urban_01"))


@pytest.mark.unit
def test_every_artifact_path_is_variant_scoped():
    for variant in ("male", "female"):
        for path in (
            paths.checkpoint_path("jacket_urban_01", 0, "shell", variant),
            paths.checkpoint_for_stage("jacket_urban_01", "shrinkwrap", variant),
            paths.latest_checkpoint("jacket_urban_01", variant=variant),
            paths.outputs_index_path("jacket_urban_01", variant),
            paths.validation_report_path("jacket_urban_01", variant),
            paths.fbx_export_path("jacket_urban_01", variant),
            paths.glb_export_path("jacket_urban_01", variant),
            paths.export_report_path("jacket_urban_01", variant),
            paths.stage_report_path("jacket_urban_01", "weights", variant),
        ):
            assert path is None or f"{os.sep}{variant}{os.sep}" in path


@pytest.mark.unit
def test_checkpoint_names_follow_the_stage_order():
    expected = {
        "shell": "00_garment.blend",
        "shrinkwrap": "01_fitted.blend",
        "weights": "02_skinned.blend",
        "uv_material": "03_material.blend",
    }
    for stage, filename in expected.items():
        path = paths.checkpoint_for_stage("jacket_urban_01", stage, "male")
        assert path is not None
        assert path.endswith(filename)


@pytest.mark.unit
def test_outputs_root_is_overridable_for_tests_and_ci(monkeypatch, tmp_path):
    """CI redirects writes with PIPELINE_OUTPUTS_ROOT instead of touching the tree."""
    import importlib

    original = os.environ.get("PIPELINE_OUTPUTS_ROOT")
    monkeypatch.setenv("PIPELINE_OUTPUTS_ROOT", str(tmp_path / "ci-outputs"))
    try:
        reloaded = importlib.reload(paths)
        assert reloaded.OUTPUTS_ROOT == str(tmp_path / "ci-outputs")
        assert reloaded.glb_export_path("jacket_urban_01", "male").startswith(
            str(tmp_path / "ci-outputs")
        )
    finally:
        # Restoring the module matters: every later test in the session shares it.
        if original is None:
            monkeypatch.delenv("PIPELINE_OUTPUTS_ROOT", raising=False)
        else:
            monkeypatch.setenv("PIPELINE_OUTPUTS_ROOT", original)
        importlib.reload(paths)
        assert paths.OUTPUTS_ROOT == (original or paths.OUTPUTS_ROOT)


@pytest.mark.unit
def test_texture_directory_is_shared_per_outfit_not_per_variant():
    """Both variants may reuse the same fabric maps; the maps are outfit data."""
    assert paths.texture_dir("jacket_urban_01").endswith(
        os.path.join("textures", "jacket_urban_01")
    )
