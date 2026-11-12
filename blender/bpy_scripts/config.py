"""Asset + outfit configuration for the modular clothing pipeline.

Everything the pipeline needs to know about a body variant or an outfit lives in
JSON manifests under ``assets/``. This module is the only place that resolves
those files, so:

* there is no hard-coded ``assets/base_body/BASE_BODY.blend`` assumption,
* both ``base_body_Male.blend`` and ``base_body_Female.blend`` are first-class,
* a missing or malformed manifest fails loudly with an actionable message.

The module is import-safe outside Blender (no ``bpy`` import).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .utils.paths import ASSETS_ROOT

BODY_MANIFEST = os.path.join(ASSETS_ROOT, "base_body", "manifest.json")
OUTFIT_MANIFEST = os.path.join(ASSETS_ROOT, "outfits", "outfits.json")

BLEND_MAGIC = b"BLENDER"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
MIN_BLEND_BYTES = 4096

AXES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")


class ConfigError(RuntimeError):
    """Raised when an asset/outfit manifest is missing, stale or invalid."""


def _load_json(path: str, what: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise ConfigError(f"{what} manifest not found: {path}")
    try:
        with open(path) as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{what} manifest is not valid JSON ({path}): {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{what} manifest must contain a JSON object: {path}")
    return data


def inspect_blend_file(path: str) -> Dict[str, Any]:
    """Return ``{"ok", "reason", "size"}`` for a .blend path.

    Detects Git-LFS pointers, truncated placeholders and plain text stubs before
    Blender is asked to open the file, which used to fail with an opaque error.
    """
    if not os.path.exists(path):
        return {"ok": False, "reason": f"file not found: {path}", "size": 0}
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(64)
    if head.startswith(b"version https://git-lfs"):
        return {
            "ok": False,
            "reason": f"{path} is a Git LFS pointer, run `git lfs pull`",
            "size": size,
        }
    if head.startswith(BLEND_MAGIC) or head.startswith(ZSTD_MAGIC):
        if size < MIN_BLEND_BYTES:
            return {
                "ok": False,
                "reason": f"{path} is only {size} bytes (truncated stub)",
                "size": size,
            }
        return {"ok": True, "reason": None, "size": size}
    return {
        "ok": False,
        "reason": (
            f"{path} is not a Blender file (header {head[:16]!r}, {size} bytes). "
            "Commit the real asset or regenerate it with tools/build_body_assets.py."
        ),
        "size": size,
    }


@dataclass(frozen=True)
class Variant:
    """One body variant resolved from ``assets/base_body/manifest.json``."""

    name: str
    file: str
    body_object: str
    auxiliary_objects: List[str] = field(default_factory=list)
    armature_object: str | None = None
    up_axis: str = "+Z"
    forward_axis: str = "-Y"
    expected_scale_metres: float | None = None
    notes: str = ""

    @property
    def path(self) -> str:
        base = os.path.dirname(BODY_MANIFEST)
        return self.file if os.path.isabs(self.file) else os.path.join(base, self.file)


def _manifest_path(path: str | None = None) -> str:
    return path or BODY_MANIFEST


def load_body_manifest(path: str | None = None) -> Dict[str, Any]:
    manifest_path = _manifest_path(path)
    data = _load_json(manifest_path, "body")
    variants = data.get("variants")
    if not isinstance(variants, dict) or not variants:
        raise ConfigError(f"body manifest has no `variants` mapping: {manifest_path}")
    return data


def variant_names(path: str | None = None) -> List[str]:
    return sorted(load_body_manifest(path)["variants"])


def default_variant(path: str | None = None) -> str:
    data = load_body_manifest(path)
    default = data.get("default_variant")
    if default in data["variants"]:
        return default
    return sorted(data["variants"])[0]


def resolve_variant(name: str | None = None, path: str | None = None) -> Variant:
    """Resolve a variant name (or the manifest default) into a validated record."""
    data = load_body_manifest(path)
    variants = data["variants"]
    if name in (None, "", "auto", "default"):
        name = default_variant(path)
    if name not in variants:
        raise ConfigError(
            f"unknown body variant {name!r}; available variants: {', '.join(sorted(variants))} "
            f"(see {_manifest_path(path)})"
        )
    raw = variants[name]
    if not isinstance(raw, dict) or "file" not in raw:
        raise ConfigError(f"variant {name!r} must be an object with a `file` key")
    if not raw.get("body_object"):
        raise ConfigError(
            f"variant {name!r} must declare `body_object` (the mesh object holding the body); "
            f"there is no implicit BASE_BODY fallback. See {_manifest_path(path)}"
        )
    up = str(raw.get("up_axis", "+Z"))
    forward = str(raw.get("forward_axis", "-Y"))
    if up not in AXES:
        raise ConfigError(f"variant {name!r}: up_axis {up!r} must be one of {', '.join(AXES)}")
    if forward not in AXES:
        raise ConfigError(
            f"variant {name!r}: forward_axis {forward!r} must be one of {', '.join(AXES)}"
        )
    if up.replace("+", "").replace("-", "") == forward.replace("+", "").replace("-", ""):
        raise ConfigError(f"variant {name!r}: up_axis and forward_axis must be different axes")

    variant = Variant(
        name=name,
        file=str(raw["file"]),
        body_object=str(raw.get("body_object") or ""),
        auxiliary_objects=[str(x) for x in raw.get("auxiliary_objects", [])],
        armature_object=raw.get("armature_object"),
        up_axis=up,
        forward_axis=forward,
        expected_scale_metres=raw.get("expected_scale_metres"),
        notes=str(raw.get("notes", "")),
    )

    check = inspect_blend_file(variant.path)
    if not check["ok"]:
        raise ConfigError(f"body variant {name!r} is unusable: {check['reason']}")
    return variant


def load_outfit_manifest(path: str | None = None) -> Dict[str, Any]:
    manifest_path = path or OUTFIT_MANIFEST
    data = _load_json(manifest_path, "outfit")
    outfits = data.get("outfits")
    if not isinstance(outfits, dict) or not outfits:
        raise ConfigError(f"outfit manifest has no `outfits` mapping: {manifest_path}")
    return data


def outfit_names(path: str | None = None) -> List[str]:
    return sorted(load_outfit_manifest(path)["outfits"])


DEFAULT_OUTFIT_SETTINGS: Dict[str, Any] = {
    "display_name": "Outfit",
    "region": "UPPER_BODY",
    "garment_type": "jacket",
    "coverage": {"top": 0.19, "bottom": 0.58},
    "sleeves": {"length": 0.9, "cuff_inset": 0.98},
    "opening": "front",
    "opening_width": 0.05,
    "offset": {"default": 0.014, "hem_extra": 0.012, "collar_extra": 0.004, "sleeve_extra": 0.008},
    "thickness": 0.006,
    "fit": "regular",
    "material": {
        "name": None,
        "base_color": [0.16, 0.18, 0.22, 1.0],
        "roughness": 0.8,
        "metallic": 0.0,
        "normal_strength": 1.0,
        "textures": {},
        "procedural": {
            "enabled": True,
            "resolution": 512,
            "weave_cells": 48,
            "height_amplitude": 0.35,
        },
    },
    "reference_images": [],
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_outfit(name: str, path: str | None = None) -> Dict[str, Any]:
    """Return the fully-defaulted settings for ``name``."""
    data = load_outfit_manifest(path)
    outfits = data["outfits"]
    if name not in outfits:
        raise ConfigError(
            f"unknown outfit {name!r}; available outfits: {', '.join(sorted(outfits))} "
            f"(see {path or OUTFIT_MANIFEST})"
        )
    settings = _deep_merge(DEFAULT_OUTFIT_SETTINGS, outfits[name])
    settings["id"] = name
    settings.setdefault("region", "UPPER_BODY")
    material = settings.setdefault("material", {})
    if not material.get("name"):
        material["name"] = f"MAT_Cloth_{name}"
    return settings


def axis_vector(axis: str) -> tuple[float, float, float]:
    sign = -1.0 if axis.startswith("-") else 1.0
    index = {"X": 0, "Y": 1, "Z": 2}[axis[-1]]
    vec = [0.0, 0.0, 0.0]
    vec[index] = sign
    return tuple(vec)  # type: ignore[return-value]
