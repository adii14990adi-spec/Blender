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


def validation_report_path(outfit: str) -> str:
    return os.path.join(outfit_dir(outfit), "validation_report.json")


def fbx_export_path(outfit: str) -> str:
    return os.path.join(outfit_dir(outfit), f"{outfit}_Modular.fbx")


def glb_export_path(outfit: str) -> str:
    return os.path.join(outfit_dir(outfit), f"{outfit}_Modular.glb")


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path
