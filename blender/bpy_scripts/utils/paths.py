"""Filesystem path helpers for the pipeline.

All paths are resolved from this file's location (…/blender/bpy_scripts/utils/)
so the package works whether invoked from the repo root or from Blender.
"""

from __future__ import annotations

import os

UTILS_DIR = os.path.dirname(os.path.abspath(__file__))  # …/blender/bpy_scripts/utils
BPY_SCRIPTS_DIR = os.path.dirname(UTILS_DIR)  # …/blender/bpy_scripts
BLENDER_DIR = os.path.dirname(BPY_SCRIPTS_DIR)  # …/blender
REPO_ROOT = os.path.dirname(BLENDER_DIR)  # repo root

OUTPUTS_ROOT = os.path.join(REPO_ROOT, "outputs")
ASSETS_ROOT = os.path.join(REPO_ROOT, "assets")
BASE_BODY_BLEND = os.path.join(ASSETS_ROOT, "base_body", "BASE_BODY.blend")
BASE_BODY_FBX = os.path.join(ASSETS_ROOT, "base_body", "BASE_BODY.fbx")


def outfit_dir(outfit: str) -> str:
    return os.path.join(OUTPUTS_ROOT, outfit)


def checkpoint_path(outfit: str, index: int, name: str) -> str:
    return os.path.join(outfit_dir(outfit), f"{index:02d}_{name}.blend")


# On-disk checkpoint for every stage, in pipeline order. Each stage reads the
# newest checkpoint written before it instead of re-opening the base body, so
# the work of earlier stages is never thrown away.
STAGE_CHECKPOINTS = (
    (0, "shell"),
    (1, "shrinkwrapped"),
    (2, "weighted"),
    (3, "uv_material"),
)


def latest_checkpoint(outfit: str, before_index: int | None = None) -> str | None:
    """Return the newest existing checkpoint, optionally older than ``before_index``."""
    for index, name in reversed(STAGE_CHECKPOINTS):
        if before_index is not None and index >= before_index:
            continue
        path = checkpoint_path(outfit, index, name)
        if os.path.exists(path):
            return path
    return None


def input_blend_path(blend_path: str, outfit: str, stage_index: int) -> "tuple[str, dict]":
    """Pick the file a stage should open: previous checkpoint, else base body.

    Returns ``(path, info)`` where ``info`` is JSON-safe and meant to be merged
    into stage metrics so a run log shows which artifact was consumed.
    """
    checkpoint = latest_checkpoint(outfit, before_index=stage_index)
    if checkpoint:
        return checkpoint, {
            "input_source": "checkpoint",
            "input_path": checkpoint,
            "input_stage_index": stage_index - 1,
        }
    return blend_path, {"input_source": "base_blend", "input_path": blend_path}


def validation_report_path(outfit: str) -> str:
    return os.path.join(outfit_dir(outfit), "validation_report.json")


def fbx_export_path(outfit: str) -> str:
    return os.path.join(outfit_dir(outfit), f"{outfit}_Modular.fbx")


def glb_export_path(outfit: str) -> str:
    return os.path.join(outfit_dir(outfit), f"{outfit}_Modular.glb")


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path
