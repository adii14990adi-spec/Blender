"""PBR texture resolution and the garment material graph.

Texture policy (never silently fake an asset):

* if a real image file exists at the configured path it is used as-is;
* otherwise, when the outfit enables ``material.procedural``, a deterministic
  woven-fabric texture set is generated on disk (base colour, normal, roughness,
  metallic) and flagged ``texture_source: "procedural"`` in the stage metrics;
* otherwise the stage fails — a material without its configured maps is not
  "good enough".

All maps are packed into the .blend and embedded in the GLB/FBX.
"""

from __future__ import annotations

import os
from typing import Any, Dict

try:  # pragma: no cover
    import bpy

    HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    HAS_BPY = False

from .logging import get_logger
from .paths import ensure_dir

logger = get_logger("utils.textures")

SLOTS = ("base_color", "normal", "roughness", "metallic")
MIN_VALID_IMAGE_BYTES = 512


def _is_usable_image(path: str) -> bool:
    if not path or not os.path.exists(path):
        return False
    if os.path.getsize(path) < MIN_VALID_IMAGE_BYTES:
        return False
    try:
        image = bpy.data.images.load(path, check_existing=False)  # type: ignore[union-attr]
    except Exception:
        return False
    usable = image.size[0] >= 8 and image.size[1] >= 8
    bpy.data.images.remove(image)  # type: ignore[union-attr]
    return usable


def _height_field(resolution: int, cells: int, amplitude: float):
    import numpy as np

    y, x = np.mgrid[0:resolution, 0:resolution].astype(np.float32)
    cell = resolution / max(cells, 1)
    weave_x = 0.5 + 0.5 * np.sin(2.0 * np.pi * x / cell)
    weave_y = 0.5 + 0.5 * np.sin(2.0 * np.pi * y / cell)
    # Basket weave: alternate warp/weft every other cell.
    checker = (np.floor(x / cell) + np.floor(y / cell)) % 2.0
    height = checker * weave_x + (1.0 - checker) * weave_y
    # Low-frequency slubs so large surfaces do not look perfectly uniform.
    slub = np.sin(2.0 * np.pi * x / (resolution / 3.0) + 0.7) * np.sin(
        2.0 * np.pi * y / (resolution / 5.0) + 1.3
    )
    height = height * amplitude + slub * 0.08
    return height.astype(np.float32)


def _write_image(name: str, path: str, pixels, colorspace: str) -> str:
    import numpy as np

    height, width = pixels.shape[0], pixels.shape[1]
    rgba = np.ones((height, width, 4), dtype=np.float32)
    if pixels.ndim == 2:
        rgba[:, :, 0] = pixels
        rgba[:, :, 1] = pixels
        rgba[:, :, 2] = pixels
    else:
        rgba[:, :, :3] = pixels[:, :, :3]
        if pixels.shape[2] == 4:
            rgba[:, :, 3] = pixels[:, :, 3]
    image = bpy.data.images.new(name, width=width, height=height, alpha=True)  # type: ignore[union-attr]
    # Set the colourspace *before* uploading pixels: assigning a colourspace
    # re-initialises the buffer and would zero the image we just wrote.
    image.colorspace_settings.name = colorspace
    image.pixels = rgba.ravel().tolist()
    ensure_dir(os.path.dirname(path))
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()
    image.pack()
    return path


def generate_texture_set(
    outfit: str,
    settings: Dict[str, Any],
    texture_paths: Dict[str, str],
) -> Dict[str, str]:
    """Generate a deterministic woven-fabric PBR set for every missing slot."""
    import numpy as np

    procedural = settings.get("procedural") or {}
    resolution = int(procedural.get("resolution", 512))
    cells = int(procedural.get("weave_cells", 48))
    amplitude = float(procedural.get("height_amplitude", 0.35))
    base_color = settings.get("base_color") or [0.16, 0.18, 0.22, 1.0]
    roughness = float(settings.get("roughness", 0.8))
    metallic = float(settings.get("metallic", 0.0))
    normal_strength = float(settings.get("normal_strength", 1.0))

    height = _height_field(resolution, cells, amplitude)
    shade = 0.82 + 0.18 * height

    written: Dict[str, str] = {}
    targets = {
        "base_color": np.stack(
            [
                np.clip(base_color[0] * shade, 0.0, 1.0),
                np.clip(base_color[1] * shade, 0.0, 1.0),
                np.clip(base_color[2] * shade, 0.0, 1.0),
            ],
            axis=-1,
        ),
        "roughness": np.clip(roughness + 0.08 * (height - height.mean()), 0.0, 1.0),
        "metallic": np.full_like(height, metallic),
        "normal": None,  # computed below
    }

    # Tangent-space normal map from the height field (Sobel style gradient).
    dy, dx = np.gradient(height)
    scale = 4.0 * normal_strength
    nx = -dx * scale
    ny = -dy * scale
    nz = np.ones_like(height)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    normal_rgb = np.stack(
        [
            np.clip(0.5 + 0.5 * nx / length, 0.0, 1.0),
            np.clip(0.5 + 0.5 * ny / length, 0.0, 1.0),
            np.clip(0.5 + 0.5 * nz / length, 0.0, 1.0),
        ],
        axis=-1,
    )
    targets["normal"] = normal_rgb

    colorspaces = {
        "base_color": "sRGB",
        "normal": "Non-Color",
        "roughness": "Non-Color",
        "metallic": "Non-Color",
    }
    for slot in SLOTS:
        path = texture_paths.get(slot)
        if not path:
            raise ValueError(f"no texture path configured for slot {slot!r}")
        written[slot] = _write_image(
            f"TEX_{outfit}_{slot}",
            path,
            targets[slot],
            colorspaces[slot],
        )
        logger.info("generated %s texture -> %s", slot, path)
    return written


def resolve_textures(outfit: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``{slot: path}`` plus provenance for the material stage."""
    configured = dict(settings.get("textures") or {})
    from .paths import REPO_ROOT  # local import avoids a cycle at module load

    resolved: Dict[str, str] = {}
    for slot in SLOTS:
        path = configured.get(slot)
        if path and not os.path.isabs(path):
            path = os.path.join(REPO_ROOT, path)
        if path:
            resolved[slot] = path

    missing = [slot for slot in SLOTS if not _is_usable_image(resolved.get(slot, ""))]
    provenance = {slot: ("existing" if slot not in missing else "missing") for slot in SLOTS}

    if missing:
        procedural_enabled = bool((settings.get("procedural") or {}).get("enabled", True))
        if not procedural_enabled:
            raise FileNotFoundError(
                "missing texture maps for "
                + ", ".join(sorted(missing))
                + " and procedural generation is disabled; commit real textures to "
                + ", ".join(resolved.get(slot, "<unset>") for slot in missing)
            )
        generated = generate_texture_set(
            outfit, settings, {slot: resolved[slot] for slot in missing}
        )
        resolved.update(generated)
        for slot in missing:
            provenance[slot] = "procedural"

    return {"paths": resolved, "provenance": provenance}


def build_material(
    shell,
    material_name: str,
    texture_paths: Dict[str, str],
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    """Create/refresh the PBR material and assign it to ``shell``."""
    material = bpy.data.materials.get(material_name)  # type: ignore[union-attr]
    if material is None:
        material = bpy.data.materials.new(material_name)  # type: ignore[union-attr]
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (600, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (250, 0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    def add_image(slot: str, non_color: bool):
        path = texture_paths.get(slot)
        if not path:
            return None
        image = bpy.data.images.load(path, check_existing=True)  # type: ignore[union-attr]
        if non_color:
            image.colorspace_settings.name = "Non-Color"
        node = nodes.new("ShaderNodeTexImage")
        node.image = image
        node.name = f"TEX_{slot}"
        node.label = slot
        node.location = (-350, 300 - 200 * SLOTS.index(slot))
        return node

    built: Dict[str, Any] = {}
    base = add_image("base_color", non_color=False)
    built["base_color"] = base
    roughness = add_image("roughness", non_color=True)
    metallic = add_image("metallic", non_color=True)
    normal = add_image("normal", non_color=True)
    built.update({"roughness": roughness, "metallic": metallic, "normal": normal})

    if base:
        links.new(base.outputs["Color"], bsdf.inputs["Base Color"])
    if roughness:
        links.new(roughness.outputs["Color"], bsdf.inputs["Roughness"])
    if metallic:
        links.new(metallic.outputs["Color"], bsdf.inputs["Metallic"])
    if normal:
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.location = (0, -260)
        normal_map.inputs["Strength"].default_value = float(settings.get("normal_strength", 1.0))
        links.new(normal.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

    if not base:
        bsdf.inputs["Base Color"].default_value = tuple(
            settings.get("base_color") or (0.8, 0.8, 0.8, 1.0)
        )
    bsdf.inputs["Roughness"].default_value = float(settings.get("roughness", 0.8))
    bsdf.inputs["Metallic"].default_value = float(settings.get("metallic", 0.0))

    # Assign as the only material slot so the export cannot pick a stale one.
    shell.data.materials.clear()
    shell.data.materials.append(material)
    for polygon in shell.data.polygons:
        polygon.material_index = 0

    return {
        "material": material,
        "images_by_slot": {slot: node.image for slot, node in built.items() if node.image},
        "nodes": [node.name for node in nodes],
        "texture_nodes": {
            "base_color": bool(base),
            "normal": bool(normal),
            "roughness": bool(roughness),
            "metallic": bool(metallic),
        },
        "images": sorted({node.image.name for node in nodes if getattr(node, "image", None)}),
    }


def image_stats(image) -> Dict[str, Any]:
    """Mean/standard deviation per channel plus a usability verdict.

    Guards the failure mode where a texture *file* exists but is blank: the
    material would look flat black while every "is there a texture?" check
    passes.
    """
    import numpy as np

    width, height = image.size[:]
    if not width or not height:
        return {"name": image.name, "ok": False, "error": "zero-sized image"}
    buffer = np.array(image.pixels[:], dtype=np.float32).reshape(height, width, 4)
    rgb = buffer[:, :, :3]
    means = [float(v) for v in rgb.reshape(-1, 3).mean(axis=0)]
    stds = [float(v) for v in rgb.reshape(-1, 3).std(axis=0)]
    return {
        "name": image.name,
        "size": [int(width), int(height)],
        "mean": [round(v, 4) for v in means],
        "std": [round(v, 4) for v in stds],
        "packed": image.packed_file is not None,
        "source": image.filepath,
    }


def verify_texture_set(
    texture_paths: Dict[str, str],
    images: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Validate that every slot actually carries usable pixels.

    ``images`` lets the caller pass the images already wired into the material
    (so the ``packed`` flag reflects the scene, not a temporary reload).
    """
    report: Dict[str, Any] = {"slots": {}, "ok": True, "problems": []}
    for slot in SLOTS:
        path = texture_paths.get(slot, "")
        if not path or not os.path.exists(path):
            report["slots"][slot] = {"ok": False, "error": "missing file"}
            report["ok"] = False
            report["problems"].append(f"{slot}: missing file {path!r}")
            continue
        loaded = (images or {}).get(slot)
        owned = loaded is None
        image = loaded or bpy.data.images.load(path, check_existing=False)  # type: ignore[union-attr]
        try:
            stats = image_stats(image)
        finally:
            if owned:
                bpy.data.images.remove(image)  # type: ignore[union-attr]
        mean = stats.get("mean") or [0.0, 0.0, 0.0]
        std = stats.get("std") or [0.0, 0.0, 0.0]
        ok = True
        if slot == "base_color":
            ok = max(mean) > 0.01 and max(std) > 0.002
        elif slot == "normal":
            # A tangent-space normal map is ~ (0.5, 0.5, 1.0); a blank black
            # image would flatten every surface.
            ok = mean[2] > 0.5 and mean[0] > 0.2 and mean[1] > 0.2
        elif slot == "roughness":
            ok = 0.0 <= mean[0] <= 1.0 and mean[0] > 0.01
        elif slot == "metallic":
            ok = 0.0 <= mean[0] <= 1.0  # constant 0 is valid for cloth
        stats["ok"] = ok
        report["slots"][slot] = stats
        if not ok:
            report["ok"] = False
            report["problems"].append(
                f"{slot}: texture is blank or invalid (mean={mean}, std={std})"
            )
    return report


def pack_images() -> int:
    """Pack every image with a file source so the .blend stays self-contained."""
    packed = 0
    for image in bpy.data.images:  # type: ignore[union-attr]
        if image.source == "FILE" and image.packed_file is None and image.filepath:
            try:
                image.pack()
                packed += 1
            except Exception as exc:  # pragma: no cover
                logger.warning("could not pack %s: %s", image.name, exc)
    return packed
