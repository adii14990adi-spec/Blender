"""Unit tests for ``tools/bake_textures.py`` — the committed texture sets.

Two contracts matter:

* the maps it writes must satisfy the pipeline's texture gate (a blank or flat
  map is rejected by ``uv_material``), and
* the sets actually committed for ``tactical_tee_01`` / ``tactical_cargo_01``
  must be those maps — an outfit with ``procedural.enabled: false`` hard-fails
  when a file is missing, so a forgotten file would break the build.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "blender") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "blender"))

from tools import bake_textures  # noqa: E402  (path bootstrap above)

COMMITTED_OUTFITS = ("tactical_tee_01", "tactical_cargo_01")
TEXTURE_ROOT = REPO_ROOT / "assets" / "textures"


@pytest.mark.unit
def test_png_round_trip_is_lossless_to_one_byte(tmp_path):
    rng = np.random.default_rng(3)
    image = rng.random((16, 24, 3))
    path = tmp_path / "round_trip.png"
    bake_textures.write_png(str(path), image)

    decoded = bake_textures.read_png(str(path))
    assert decoded.shape == image.shape
    assert np.abs(decoded - image).max() <= 1.0 / 255.0 + 1e-9


@pytest.mark.unit
def test_reader_rejects_a_file_that_is_not_a_png(tmp_path):
    path = tmp_path / "not_an_image.png"
    path.write_text("version https://git-lfs.github.com/spec/v1\n")
    with pytest.raises(ValueError, match="not a PNG"):
        bake_textures.read_png(str(path))


@pytest.mark.unit
def test_weave_height_is_tileable():
    """A seam must not be visible: the wrap-around step is a normal neighbour step."""
    height = bake_textures.weave_height(128, cells=24, kind="twill", seed=5)
    assert height.min() >= 0.0 and height.max() <= 1.0 + 1e-9

    seam = np.abs(height[:, 0] - height[:, -1]).mean()
    vertical_seam = np.abs(height[0, :] - height[-1, :]).mean()
    interior = np.abs(np.diff(height, axis=1)).mean()
    interior_rows = np.abs(np.diff(height, axis=0)).mean()

    # The wrap step is of the same order as an interior step (no hard edge).
    assert seam < 6 * interior + 1e-6, (seam, interior)
    assert vertical_seam < 6 * interior_rows + 1e-6, (vertical_seam, interior_rows)


@pytest.mark.unit
def test_flat_height_gives_a_flat_normal_map():
    flat = np.full((8, 8), 0.5)
    normal = bake_textures.normal_from_height(flat)
    assert np.allclose(normal, np.array([0.5, 0.5, 1.0]), atol=1e-6)


@pytest.mark.unit
def test_bake_writes_a_full_set_that_passes_the_texture_gate(tmp_path):
    manifest = bake_textures.bake(
        outfit="unit_test_fabric",
        palette=(0.17, 0.185, 0.10),
        weave="twill",
        cells=64,
        resolution=128,
        output_dir=str(tmp_path / "unit_test_fabric"),
    )

    for slot in ("base_color", "normal", "roughness", "metallic"):
        path = Path(manifest["slots"][slot])
        assert path.exists() and path.stat().st_size > 64, slot

    base = bake_textures.read_png(manifest["slots"]["base_color"]).reshape(-1, 3)
    # The gate: base_color mean > 0.01 and std > 0.002 (in the values the
    # pipeline reads back from the image).
    assert base.mean(axis=0).max() > 0.01
    assert base.std(axis=0).max() > 0.002

    normal = bake_textures.read_png(manifest["slots"]["normal"]).reshape(-1, 3)
    mean = normal.mean(axis=0)
    assert mean[2] > 0.5, mean  # a normal map is ~(0.5, 0.5, 1.0)
    assert mean[0] > 0.2 and mean[1] > 0.2, mean

    roughness = bake_textures.read_png(manifest["slots"]["roughness"])
    assert 0.0 < roughness.mean() <= 1.0
    metallic = bake_textures.read_png(manifest["slots"]["metallic"])
    assert metallic.max() == 0.0, "cloth must not be metallic by default"

    provenance = json.loads((Path(manifest["folder"]) / "textures.json").read_text())
    assert provenance["mode"] == "synthesised"
    assert provenance["tileable"] is True
    assert provenance["palette_linear"] == [0.17, 0.185, 0.1]


@pytest.mark.unit
def test_deriving_from_a_supplied_map_keeps_the_original_bytes(tmp_path):
    folder = tmp_path / "supplied"
    folder.mkdir()
    supplied = folder / "my_fabric.png"
    bake_textures.write_png(str(supplied), np.full((32, 32, 3), 0.4))

    before = supplied.read_bytes()
    manifest = bake_textures.bake(
        outfit="supplied",
        output_dir=str(folder),
        source=str(supplied),
        derive_only=True,
        resolution=32,
    )

    assert supplied.read_bytes() == before, "--derive-only must not touch the user's map"
    assert manifest["slots"]["base_color"] == str(supplied)
    assert manifest["mode"] == "derived"
    normal = bake_textures.read_png(manifest["slots"]["normal"]).reshape(-1, 3)
    assert normal.mean(axis=0)[2] > 0.5


@pytest.mark.unit
@pytest.mark.parametrize("outfit", COMMITTED_OUTFITS)
def test_committed_texture_sets_are_complete_and_usable(outfit):
    folder = TEXTURE_ROOT / outfit
    assert folder.is_dir(), f"{outfit} has no committed texture set"

    for slot in ("base_color", "normal", "roughness", "metallic"):
        path = folder / f"{slot}.png"
        assert path.exists(), f"{path} is missing — commit it or drop it in"
        assert path.stat().st_size > 64, f"{path} is a placeholder"

    base = bake_textures.read_png(str(folder / "base_color.png")).reshape(-1, 3)
    assert base.mean(axis=0).max() > 0.01
    assert base.std(axis=0).max() > 0.002

    normal = bake_textures.read_png(str(folder / "normal.png")).reshape(-1, 3)
    assert normal.mean(axis=0)[2] > 0.5


@pytest.mark.unit
@pytest.mark.parametrize("outfit", COMMITTED_OUTFITS)
def test_a_strict_outfit_references_textures_that_exist(outfit):
    """``procedural.enabled: false`` means a missing map fails the build, so the
    manifest must point at files that are actually committed."""
    from bpy_scripts import config

    settings = config.load_outfit(outfit)
    pieces = config.outfit_pieces(settings)
    materials = [piece["material"] for piece in pieces]
    materials += [detail["material"] for detail in config.outfit_details(settings, pieces)]

    for material in materials:
        procedural = material.get("procedural") or {}
        if procedural.get("enabled", True):
            continue
        for slot, path in material["textures"].items():
            resolved = Path(path) if Path(path).is_absolute() else REPO_ROOT / path
            assert resolved.exists(), f"{material['name']} is missing {slot}: {path}"
            assert resolved.stat().st_size > 64, f"{resolved} looks like a placeholder"
