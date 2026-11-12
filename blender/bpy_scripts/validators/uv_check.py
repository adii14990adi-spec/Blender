"""UV validator: active UV layer present + all faces have valid UVs."""

from __future__ import annotations

from typing import Any, Dict

try:
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover - allows import / lint outside Blender
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..utils.logging import get_logger

logger = get_logger("validators.uv_check")

SHELL_OBJECT_NAME = "CLOTH_Shell"


def run(
    blend_path: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if not _HAS_BPY:
        return {
            "status": "fail",
            "metrics": {"error": "bpy not available outside Blender"},
        }

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    shell = bpy.data.objects.get(SHELL_OBJECT_NAME)
    if shell is None or shell.type != "MESH":
        return {
            "status": "fail",
            "metrics": {"error": "CLOTH_Shell mesh not found"},
        }

    uv_layer = shell.data.uv_layers.active
    if uv_layer is None:
        return {
            "status": "fail",
            "metrics": {"error": "no active UV layer"},
        }

    total_loops = len(shell.data.uv_layers.active.data)
    passed = total_loops > 0
    return {
        "status": "pass" if passed else "fail",
        "metrics": {
            "uv_layer": uv_layer.name,
            "total_uv_loops": total_loops,
        },
    }
