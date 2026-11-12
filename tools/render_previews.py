#!/usr/bin/env python3
"""Render the outfit preview thumbnails the web viewer shows.

Runs inside Blender (``blender -b --python tools/render_previews.py -- ...``) or
through the ``bpy`` module, and writes ``preview.png`` next to each export::

    outputs/<variant>/<outfit>/preview.png

The shot is a three-quarter view framed on the garment itself (not on the whole
T-posed body), lit with a simple three-point studio rig against the studio's own
background colour, so the thumbnails sit naturally in the dark UI.

Usage::

    python tools/render_previews.py                       # every export
    python tools/render_previews.py --variant male
    python tools/render_previews.py --force               # re-render existing files
    blender -b --python tools/render_previews.py -- --outfit jacket_urban_01
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Dict, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

GARMENT_OBJECT = "CLOTH_Shell"
FINAL_BLEND = "04_final.blend"
PREVIEW_NAME = "preview.png"

#: Studio background, matched to --bg in web_viewer/css/styles.css.
BACKGROUND = (0.045, 0.052, 0.066, 1.0)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    if argv is None:
        argv = sys.argv[1:]
        # Under `blender -b --python tool.py` everything after "--" is ours.
        argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser(description="Render garment preview thumbnails")
    parser.add_argument("--variant", action="append", help="body variant (repeatable)")
    parser.add_argument("--outfit", action="append", help="outfit id (repeatable)")
    parser.add_argument("--outputs-root", default=os.path.join(REPO_ROOT, "outputs"))
    parser.add_argument("--size", type=int, nargs=2, default=[640, 800], metavar=("W", "H"))
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--azimuth", type=float, default=30.0, help="camera rotation in degrees")
    parser.add_argument("--force", action="store_true", help="re-render existing previews")
    parser.add_argument("--json", help="write a summary of what was rendered here")
    return parser.parse_args(argv)


def discover(outputs_root: str, variants: Optional[List[str]], outfits: Optional[List[str]]):
    jobs = []
    if not os.path.isdir(outputs_root):
        return jobs
    for variant in sorted(os.listdir(outputs_root)):
        if variants and variant not in variants:
            continue
        variant_dir = os.path.join(outputs_root, variant)
        if not os.path.isdir(variant_dir):
            continue
        for outfit in sorted(os.listdir(variant_dir)):
            if outfits and outfit not in outfits:
                continue
            blend = os.path.join(variant_dir, outfit, FINAL_BLEND)
            if os.path.isfile(blend):
                jobs.append((variant, outfit, blend))
    return jobs


def _bounds(objects):
    from mathutils import Vector

    points = [obj.matrix_world @ vertex.co for obj in objects for vertex in obj.data.vertices]
    low = Vector((min(point[i] for point in points) for i in range(3)))
    high = Vector((max(point[i] for point in points) for i in range(3)))
    return low, high


def _apply_mannequin_material(objects) -> None:
    """Paint the body in neutral studio clay for the thumbnail.

    The pipeline's base body ships with a generic skin material. A garment
    catalogue shot wants the *garment* to read, and a neutral mannequin keeps the
    thumbnails tasteful on a public page. Only the preview render is touched -
    the exported GLB/FBX keep their real materials.
    """
    import bpy

    material = bpy.data.materials.get("PreviewMannequin")
    if material is None:
        material = bpy.data.materials.new("PreviewMannequin")
        material.use_nodes = True
        shader = material.node_tree.nodes.get("Principled BSDF") or next(
            (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
            None,
        )
        if shader:
            shader.inputs["Base Color"].default_value = (0.075, 0.08, 0.092, 1.0)
            shader.inputs["Roughness"].default_value = 0.78
            shader.inputs["Metallic"].default_value = 0.0
    for obj in objects:
        if obj.name == GARMENT_OBJECT:
            continue
        if not obj.material_slots:
            obj.data.materials.append(material)
        for slot in obj.material_slots:
            slot.link = "DATA"
            slot.material = material


def render(  # noqa: PLR0915 - one linear scene setup, top to bottom
    blend_path: str, out_path: str, *, size, samples: int, azimuth: float
) -> Dict[str, str]:
    import bpy
    from mathutils import Vector

    bpy.ops.wm.open_mainfile(filepath=blend_path)
    scene = bpy.context.scene

    garment = bpy.data.objects.get(GARMENT_OBJECT)
    subject = [garment] if garment else [o for o in bpy.data.objects if o.type == "MESH"]
    if not subject:
        raise RuntimeError(f"{blend_path} has no mesh to frame")
    _apply_mannequin_material([o for o in bpy.data.objects if o.type == "MESH"])
    low, high = _bounds(subject)
    center = (low + high) / 2
    extent = high - low

    camera_data = bpy.data.cameras.new("PreviewCam")
    camera_data.lens = 50
    camera_data.sensor_fit = "VERTICAL"
    camera_data.sensor_height = 24
    camera = bpy.data.objects.new("PreviewCam", camera_data)
    scene.collection.objects.link(camera)

    width, height = size
    fov_v = 2 * math.atan(camera_data.sensor_height / 2 / camera_data.lens)
    fov_h = 2 * math.atan(math.tan(fov_v / 2) * (width / height))
    distance = max(
        (extent.z * 1.30) / 2 / math.tan(fov_v / 2),
        (extent.x * 1.34) / 2 / math.tan(fov_h / 2),
    )
    angle = math.radians(azimuth)
    camera.location = center + Vector(
        (math.sin(angle) * distance, -math.cos(angle) * distance, extent.z * 0.02)
    )
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera

    world = bpy.data.worlds.new("PreviewWorld")
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs[0].default_value = BACKGROUND
    background.inputs[1].default_value = 0.65
    scene.world = world

    def area_light(name: str, offset, energy: float, scale: float) -> None:
        data = bpy.data.lights.new(name, type="AREA")
        data.energy = energy
        data.size = max(extent.z * scale, 0.2)
        obj = bpy.data.objects.new(name, data)
        obj.location = center + Vector(offset) * extent.z
        obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
        scene.collection.objects.link(obj)

    area_light("Key", (0.9, -1.2, 0.9), 85, 1.0)
    area_light("Fill", (-1.3, -0.9, 0.1), 28, 1.4)
    area_light("Rim", (-0.5, 1.3, 1.1), 62, 0.8)

    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = out_path
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "AgX"
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:  # pragma: no cover - other OCIO configs
        pass
    scene.view_settings.exposure = 0.3

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    bpy.ops.render.render(write_still=True)
    return {
        "blend": blend_path,
        "preview": out_path,
        "bytes": str(os.path.getsize(out_path)),
        "garment_faces": str(len(garment.data.polygons)) if garment else "0",
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    jobs = discover(args.outputs_root, args.variant, args.outfit)
    if not jobs:
        print(f"[previews] no {FINAL_BLEND} checkpoints under {args.outputs_root}", file=sys.stderr)
        return 1

    rendered: List[Dict[str, str]] = []
    skipped = 0
    for variant, outfit, blend in jobs:
        out_path = os.path.join(os.path.dirname(blend), PREVIEW_NAME)
        if os.path.exists(out_path) and not args.force:
            print(f"[previews] {variant}/{outfit}: up to date")
            skipped += 1
            continue
        print(f"[previews] {variant}/{outfit}: rendering {args.size[0]}x{args.size[1]}")
        rendered.append(
            render(blend, out_path, size=args.size, samples=args.samples, azimuth=args.azimuth)
        )

    print(f"[previews] rendered {len(rendered)}, skipped {skipped}")
    if args.json:
        import json

        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"rendered": rendered, "skipped": skipped}, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
