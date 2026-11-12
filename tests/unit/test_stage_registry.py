"""Orchestration contract: no duplicated stages, a stable order, resolvable aliases."""

from __future__ import annotations

import pytest

from bpy_scripts import pipeline


@pytest.mark.unit
def test_stage_order_is_unique_and_matches_the_registry():
    assert pipeline.STAGE_ORDER == ["shell", "shrinkwrap", "weights", "uv_material", "export"]
    assert len(set(pipeline.STAGE_ORDER)) == len(pipeline.STAGE_ORDER)
    assert set(pipeline.STAGE_REGISTRY) == set(pipeline.STAGE_ORDER)


@pytest.mark.unit
def test_uv_and_material_share_one_stage():
    """The old pipeline ran ``uv`` and ``material`` as two stages over one module."""
    assert pipeline.canonical_stage("uv") == "uv_material"
    assert pipeline.canonical_stage("material") == "uv_material"
    assert pipeline.canonical_stage("materials") == "uv_material"
    assert pipeline.STAGE_ORDER.count("uv_material") == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("garment", "shell"),
        ("generate_shell", "shell"),
        ("fit", "shrinkwrap"),
        ("skin", "weights"),
        ("skin_weights", "weights"),
        ("export_fbx", "export"),
        ("glb", "export"),
        ("fbx", "export"),
    ],
)
def test_aliases_resolve_to_canonical_stages(alias, canonical):
    assert pipeline.canonical_stage(alias) == canonical


@pytest.mark.unit
def test_aliases_never_add_pipeline_steps():
    for alias, canonical in pipeline.STAGE_ALIASES.items():
        assert canonical in pipeline.STAGE_ORDER, alias
        assert pipeline.STAGE_REGISTRY[canonical] is not None


@pytest.mark.unit
def test_unknown_stage_is_rejected():
    assert pipeline.canonical_stage("not-a-stage") is None


@pytest.mark.unit
def test_stage_names_exposes_the_public_cli_surface():
    names = pipeline.stage_names()
    for expected in (*pipeline.STAGE_ORDER, "validate", "full", "all"):
        assert expected in names


@pytest.mark.unit
def test_validate_is_not_in_the_execution_order():
    """Validation inspects an existing checkpoint; it must not be chained as a stage."""
    assert "validate" not in pipeline.STAGE_ORDER
