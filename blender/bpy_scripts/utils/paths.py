"""Filesystem paths for the pipeline.

All paths resolve from this file's location so the package works whether it is
invoked from the repo root, from Blender, or from an installed wheel. The
``PIPELINE_OUTPUTS_ROOT`` / ``PIPELINE_ASSETS_ROOT`` environment variables let CI
and the test suite redirect writes without touching the source tree.
"""

from __future__ import annotations

import os

UTILS_DIR = os.path.dirname(os.path.abspath(__file__))
BPY_SCRIPTS_DIR = os.path.dirname(UTILS_DIR)
BLENDER_DIR = os.path.dirname(BPY_SCRIPTS_DIR)
REPO_ROOT = os.path.dirname(BLENDER_DIR)

OUTPUTS_ROOT = os.path.abspath(
    os.environ.get("PIPELINE_OUTPUTS_ROOT") or os.path.join(REPO_ROOT, "outputs")
)
ASSETS_ROOT = os.path.abspath(
    os.environ.get("PIPELINE_ASSETS_ROOT") or os.path.join(REPO_ROOT, "assets")
)

TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
DEFAULT_TEXTURE_DIR = os.path.join(ASSETS_ROOT, "textures")
PIPELINE_VERSION = "2.0.0"

# Canonical stage -> checkpoint filename. ``latest_checkpoint`` walks this list
# backwards, so the order here *is* the pipeline order.
STAGE_CHECKPOINTS = (
    (0, "shell", "00_garment.blend"),
    (1, "shrinkwrap", "01_fitted.blend"),
    (2, "weights", "02_skinned.blend"),
    (3, "uv_material", "03_material.blend"),
)

STAGE_INDEX = {name: index for index, name, _ in STAGE_CHECKPOINTS}


def outfit_dir(outfit: str, variant: str | None = None) -> str:
    """``outputs/<outfit>`` or ``outputs/<variant>/<outfit>``.

    Male and female builds of the same outfit must never share a directory:
    otherwise the second variant silently overwrites the first one's FBX/GLB.
    """
    if variant:
        return os.path.join(OUTPUTS_ROOT, variant, outfit)
    return os.path.join(OUTPUTS_ROOT, outfit)


def checkpoint_path(outfit: str, index: int, name: str, variant: str | None = None) -> str:
    for stage_index, stage_name, filename in STAGE_CHECKPOINTS:
        if stage_index == index and stage_name == name:
            return os.path.join(outfit_dir(outfit, variant), filename)
    # Unknown stage: still deterministic, never collides with a known stage.
    return os.path.join(outfit_dir(outfit, variant), f"{index:02d}_{name}.blend")


def checkpoint_for_stage(outfit: str, stage: str, variant: str | None = None) -> str | None:
    for _index, name, filename in STAGE_CHECKPOINTS:
        if name == stage:
            return os.path.join(outfit_dir(outfit, variant), filename)
    return None


def latest_checkpoint(
    outfit: str, before_index: int | None = None, variant: str | None = None
) -> str | None:
    """Newest existing checkpoint, optionally older than ``before_index``."""
    for index, _name, filename in reversed(STAGE_CHECKPOINTS):
        if before_index is not None and index >= before_index:
            continue
        path = os.path.join(outfit_dir(outfit, variant), filename)
        if os.path.exists(path):
            return path
    return None


def input_blend_path(
    blend_path: str, outfit: str, stage_index: int, variant: str | None = None
) -> "tuple[str, dict]":
    """Legacy helper: previous checkpoint if present, else the given blend."""
    checkpoint = latest_checkpoint(outfit, before_index=stage_index, variant=variant)
    if checkpoint:
        return checkpoint, {
            "input_source": "checkpoint",
            "input_path": checkpoint,
            "input_stage_index": stage_index - 1,
        }
    return blend_path, {"input_source": "base_blend", "input_path": blend_path}


def outputs_index_path(outfit: str, variant: str | None = None) -> str:
    return os.path.join(outfit_dir(outfit, variant), "run_index.json")


def validation_report_path(outfit: str, variant: str | None = None) -> str:
    return os.path.join(outfit_dir(outfit, variant), "validation_report.json")


def stage_report_path(outfit: str, stage: str, variant: str | None = None) -> str:
    return os.path.join(outfit_dir(outfit, variant), f"{stage}_report.json")


def fbx_export_path(outfit: str, variant: str | None = None) -> str:
    return os.path.join(outfit_dir(outfit, variant), f"{outfit}_Modular.fbx")


def glb_export_path(outfit: str, variant: str | None = None) -> str:
    return os.path.join(outfit_dir(outfit, variant), f"{outfit}_Modular.glb")


def export_report_path(outfit: str, variant: str | None = None) -> str:
    return os.path.join(outfit_dir(outfit, variant), "export_report.json")


def texture_dir(outfit: str) -> str:
    return os.path.join(DEFAULT_TEXTURE_DIR, outfit)


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def ensure_parent(path: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def relpath(path: str) -> str:
    """Pretty path for reports: relative to the repo root when possible."""
    try:
        return os.path.relpath(path, REPO_ROOT)
    except ValueError:  # different drive on Windows
        return path
