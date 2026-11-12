"""Multi-piece outfits: the schema that turns one cut into a whole loadout.

A single-garment outfit (``jacket_urban_01``) must resolve exactly as it did
before the ``pieces`` key existed; a loadout declares several pieces, each with
its own region, standoff, thickness and material, plus optional hard details
(pouches, pads) and standalone materials.
"""

from __future__ import annotations

import json

import pytest

from bpy_scripts import config

LOADOUT = "tactical_loadout_01"


def _write_manifest(tmp_path, outfits):
    path = tmp_path / "outfits.json"
    path.write_text(json.dumps({"schema_version": 3, "outfits": outfits}))
    return str(path)


@pytest.mark.unit
def test_single_garment_outfit_still_resolves_to_one_piece():
    settings = config.load_outfit("jacket_urban_01")
    pieces = config.outfit_pieces(settings)

    assert len(pieces) == 1
    piece = pieces[0]
    assert piece["key"] == "main"
    assert piece["legacy_single_piece"] is True
    assert piece["region"] == "UPPER_BODY"
    assert piece["coverage"] == settings["coverage"]
    assert piece["offset"] == settings["offset"]
    assert piece["material"]["name"] == "MAT_Cloth_jacket_urban_01"
    # The historical texture layout must not move: existing builds/reports point at it.
    assert piece["material"]["textures"]["base_color"].endswith(
        "assets/textures/jacket_urban_01/basecolor.png"
    )


@pytest.mark.unit
def test_every_garment_outfit_resolves():
    for name in config.outfit_names():
        settings = config.load_outfit(name)
        pieces = config.outfit_pieces(settings)
        assert pieces, name
        for piece in pieces:
            assert piece["region"] in config.PIECE_REGIONS
            assert float(piece["thickness"]) > 0
            assert piece["material"]["name"]


@pytest.mark.unit
def test_loadout_pieces_carry_their_own_region_and_material():
    settings = config.load_outfit(LOADOUT)
    pieces = {piece["key"]: piece for piece in config.outfit_pieces(settings)}

    assert set(pieces) == {"tee", "cargo", "belt", "boots"}
    assert pieces["tee"]["region"] == "UPPER_BODY"
    assert pieces["cargo"]["region"] == "LOWER_BODY"
    assert pieces["belt"]["region"] == "ACCESSORY"
    assert pieces["boots"]["region"] == "FOOTWEAR"

    # Distinct pieces must not share a material name by accident.
    names = [piece["material"]["name"] for piece in pieces.values()]
    assert len(set(names)) == len(names)

    cargo = pieces["cargo"]["coverage"]
    assert cargo["top"] < cargo["bottom"], "coverage runs from the head downwards"
    boots = pieces["boots"]["coverage"]
    assert boots["bottom"] > cargo["bottom"], "boots must end below the trousers"


@pytest.mark.unit
def test_piece_texture_paths_follow_the_piece_name():
    settings = config.load_outfit(LOADOUT)
    pieces = {piece["key"]: piece for piece in config.outfit_pieces(settings)}
    textures = pieces["cargo"]["material"]["textures"]

    assert textures["base_color"].endswith("tactical_loadout_01/cargo_basecolor.png")
    assert textures["normal"].endswith("tactical_loadout_01/cargo_normal.png")
    assert set(textures) == set(config.TEXTURE_SLOTS)


@pytest.mark.unit
def test_details_share_materials_instead_of_cloning_texture_sets():
    settings = config.load_outfit(LOADOUT)
    pieces = config.outfit_pieces(settings)
    details = config.outfit_details(settings, pieces)

    assert details, "the loadout declares hard details"
    by_key = {piece["key"]: piece["material"]["name"] for piece in pieces}
    webbing = next(detail for detail in details if detail["id"] == "belt_buckle")
    assert webbing["material"]["name"] == by_key["belt"]

    gear = next(detail for detail in details if detail["id"].startswith("pouch"))
    assert gear["material"]["name"] == settings["materials"]["gear"]["name"]
    # A shared material means one texture set on disk, not four duplicates.
    shared = [
        detail for detail in details if detail["material"]["name"] == gear["material"]["name"]
    ]
    assert len(shared) >= 2
    assert len({detail["material"]["textures"]["base_color"] for detail in shared}) == 1


@pytest.mark.unit
def test_detail_anchors_and_faces_are_validated(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        {
            "broken": {
                "pieces": {"main": {"region": "UPPER_BODY"}},
                "details": [
                    {
                        "id": "nonsense",
                        "anchor": "tail",
                        "size": [0.1, 0.1, 0.1],
                        "material": "main",
                    }
                ],
            }
        },
    )
    settings = config.load_outfit("broken", manifest)
    with pytest.raises(config.ConfigError, match="anchor"):
        config.outfit_details(settings, config.outfit_pieces(settings))


@pytest.mark.unit
def test_detail_size_and_material_references_are_validated(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        {
            "bad_size": {
                "pieces": {"main": {"region": "UPPER_BODY"}},
                "details": [{"id": "x", "anchor": "waist", "size": [0.1, 0.0, 0.1]}],
            },
            "bad_material": {
                "pieces": {"main": {"region": "UPPER_BODY"}},
                "details": [{"id": "x", "anchor": "waist", "material": "ghost"}],
            },
            "bad_face": {
                "pieces": {"main": {"region": "UPPER_BODY"}},
                "details": [{"id": "x", "anchor": "waist", "face": "sideways"}],
            },
        },
    )
    for name, match in (
        ("bad_size", "positive"),
        ("bad_material", "unknown piece material"),
        ("bad_face", "face"),
    ):
        settings = config.load_outfit(name, manifest)
        with pytest.raises(config.ConfigError, match=match):
            config.outfit_details(settings, config.outfit_pieces(settings))


@pytest.mark.unit
def test_piece_region_is_validated(tmp_path):
    manifest = _write_manifest(
        tmp_path, {"weird": {"pieces": {"main": {"region": "KNEE_CAP_ONLY"}}}}
    )
    settings = config.load_outfit("weird", manifest)
    with pytest.raises(config.ConfigError, match="region"):
        config.outfit_pieces(settings)


@pytest.mark.unit
def test_outfit_defaults_are_inherited_by_every_piece(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        {
            "layered": {
                "defaults": {"offset": {"default": 0.02}, "thickness": 0.004},
                "pieces": {
                    "a": {"region": "LOWER_BODY", "coverage": {"top": 0.5, "bottom": 0.9}},
                    "b": {
                        "region": "ACCESSORY",
                        "coverage": {"top": 0.45, "bottom": 0.5},
                        "thickness": 0.009,
                    },
                },
            }
        },
    )
    settings = config.load_outfit("layered", manifest)
    pieces = {piece["key"]: piece for piece in config.outfit_pieces(settings)}

    assert pieces["a"]["offset"]["default"] == 0.02
    assert pieces["a"]["thickness"] == 0.004
    assert pieces["b"]["thickness"] == 0.009, "a piece overrides the shared default"
    assert pieces["a"]["material"]["name"] == "MAT_Cloth_layered_a"
