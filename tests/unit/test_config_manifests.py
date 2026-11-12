"""Asset resolution: both body variants are first class, nothing is hard-coded."""

from __future__ import annotations

import json
import os

import pytest

from bpy_scripts import config


@pytest.mark.unit
def test_both_body_variants_are_declared_and_exist():
    names = config.variant_names()
    assert set(names) >= {"male", "female"}

    for name in ("male", "female"):
        variant = config.resolve_variant(name)
        assert variant.name == name
        assert os.path.exists(variant.path), variant.path
        assert variant.path.endswith(".blend")
        with open(variant.path, "rb") as handle:
            assert handle.read(7) == b"BLENDER", f"{variant.path} is not a real .blend"


@pytest.mark.unit
def test_variants_point_at_distinct_files():
    male = config.resolve_variant("male")
    female = config.resolve_variant("female")
    assert male.path != female.path
    assert os.path.basename(male.path) == "base_body_Male.blend"
    assert os.path.basename(female.path) == "base_body_Female.blend"


@pytest.mark.unit
def test_the_legacy_base_body_blend_is_not_required(tmp_path):
    """The old pipeline hard-coded assets/base_body/BASE_BODY.blend."""
    manifest = json.loads(open(config.BODY_MANIFEST).read())
    for name, entry in manifest["variants"].items():
        assert "BASE_BODY.blend" not in str(entry.get("file", "")), name
        assert entry.get("file"), name


@pytest.mark.unit
def test_unknown_variant_fails_loudly_with_the_available_names():
    with pytest.raises(config.ConfigError) as excinfo:
        config.resolve_variant("dragon")
    message = str(excinfo.value)
    assert "dragon" in message
    assert "male" in message and "female" in message


@pytest.mark.unit
def test_outfits_are_loadable_and_complete():
    names = config.outfit_names()
    assert "jacket_urban_01" in names

    settings = config.load_outfit("jacket_urban_01")
    assert settings["region"] in {"UPPER_BODY", "LOWER_BODY", "FULL_BODY"}
    assert settings["offset"]["default"] > 0
    assert 0.001 <= settings["thickness"] <= 0.05
    coverage = settings["coverage"]
    assert 0.0 < coverage["top"] < coverage["bottom"] < 1.0


@pytest.mark.unit
def test_unknown_outfit_fails_loudly():
    with pytest.raises(config.ConfigError):
        config.load_outfit("no_such_outfit")


@pytest.mark.unit
def test_placeholder_blend_files_are_detected(tmp_path):
    stub = tmp_path / "base_body_Male.blend"
    stub.write_text("# PLACEHOLDER\n")
    with pytest.raises(config.ConfigError) as excinfo:
        config.resolve_variant("male", _write_manifest(tmp_path, stub))
    assert "not a Blender file" in str(excinfo.value)


def _write_manifest(tmp_path, blend) -> str:
    manifest = {
        "default": "male",
        "variants": {
            "male": {
                "file": str(blend),
                "body_object": "SuperHero_Male",
                "armature_object": "RIG_Character",
                "up_axis": "+Z",
                "forward_axis": "-Y",
            }
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return str(path)
