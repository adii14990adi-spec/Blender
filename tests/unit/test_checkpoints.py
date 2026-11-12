"""Checkpoint index: signatures must not depend on single-stage parameters."""

from __future__ import annotations

import json

import pytest

from bpy_scripts.utils.checkpoints import RunIndex, params_signature


@pytest.mark.unit
def test_signature_ignores_stage_local_params():
    """``--stage shrinkwrap --params {"offset": 0.02}`` must still see the shell checkpoint."""
    a = params_signature("male", "jacket_urban_01", {"offset": 0.01})
    b = params_signature("male", "jacket_urban_01", {"offset": 0.05, "relax_iterations": 9})
    assert a == b


@pytest.mark.unit
def test_signature_separates_variants_and_outfits():
    assert params_signature("male", "jacket_urban_01") != params_signature(
        "female", "jacket_urban_01"
    )
    assert params_signature("male", "jacket_urban_01") != params_signature("male", "other_outfit")


@pytest.mark.unit
def test_index_records_and_returns_a_fresh_checkpoint(tmp_path, monkeypatch):
    from bpy_scripts.utils import paths

    monkeypatch.setattr(paths, "OUTPUTS_ROOT", str(tmp_path))
    checkpoint = tmp_path / "male" / "jacket_urban_01" / "00_garment.blend"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"BLENDER" + b"\x00" * 1024)

    signature = params_signature("male", "jacket_urban_01")
    index = RunIndex("jacket_urban_01", signature, "male")
    index.record("shell", str(checkpoint))
    index.save()

    reloaded = RunIndex("jacket_urban_01", signature, "male")
    assert reloaded.fresh_checkpoint("shell") == str(checkpoint)


@pytest.mark.unit
def test_missing_checkpoint_is_never_reported_as_fresh(tmp_path, monkeypatch):
    from bpy_scripts.utils import paths

    monkeypatch.setattr(paths, "OUTPUTS_ROOT", str(tmp_path))
    signature = params_signature("male", "jacket_urban_01")
    index = RunIndex("jacket_urban_01", signature, "male")
    index.record("shell", str(tmp_path / "male" / "jacket_urban_01" / "00_garment.blend"))
    index.save()

    # Recorded but not on disk -> stale, must not be returned.
    assert RunIndex("jacket_urban_01", signature, "male").fresh_checkpoint("shell") is None


@pytest.mark.unit
def test_invalidate_after_drops_downstream_checkpoints(tmp_path, monkeypatch):
    from bpy_scripts.utils import paths

    monkeypatch.setattr(paths, "OUTPUTS_ROOT", str(tmp_path))
    base = tmp_path / "male" / "jacket_urban_01"
    base.mkdir(parents=True)
    signature = params_signature("male", "jacket_urban_01")
    index = RunIndex("jacket_urban_01", signature, "male")
    order = ["shell", "shrinkwrap", "weights", "uv_material", "export"]
    for name in order[:-1]:
        path = base / f"{name}.blend"
        path.write_bytes(b"BLENDER" + b"\x00" * 1024)
        index.record(name, str(path))
    index.save()

    index.invalidate_after("shrinkwrap", order)
    index.save()

    reloaded = RunIndex("jacket_urban_01", signature, "male")
    assert reloaded.fresh_checkpoint("shell")
    assert reloaded.fresh_checkpoint("shrinkwrap")
    assert reloaded.fresh_checkpoint("weights") is None
    assert reloaded.fresh_checkpoint("uv_material") is None


@pytest.mark.unit
def test_changing_variant_invalidates_the_whole_index(tmp_path, monkeypatch):
    from bpy_scripts.utils import paths

    monkeypatch.setattr(paths, "OUTPUTS_ROOT", str(tmp_path))
    male_dir = tmp_path / "jacket_urban_01"
    male_dir.mkdir(parents=True)
    index_file = male_dir / "run_index.json"
    index_file.write_text(
        json.dumps({"signature": "different", "stages": {"shell": {"path": "x"}}})
    )

    index = RunIndex("jacket_urban_01", params_signature("male", "jacket_urban_01"))
    assert index.fresh_checkpoint("shell") is None
