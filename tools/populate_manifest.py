#!/usr/bin/env python3
"""
Dump the real mesh / bone / material / shape-key names of a base asset into
bases/<asset_id>/manifest.json.

    blender --background --python tools/populate_manifest.py -- female_base_v3

Existing manifest values are preserved; only the discovered arrays (and
armature/skinning when they are still empty) are filled in.
"""

import json
import os
import re
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg):
    print("[manifest] %s" % msg, flush=True)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    asset_id = argv[0] if argv else "female_base_v3"
    base_dir = os.path.join(ROOT, "bases", asset_id)
    manifest_path = os.path.join(base_dir, "manifest.json")

    model = None
    for name in ("model.gltf", "model.glb"):
        candidate = os.path.join(base_dir, name)
        if os.path.isfile(candidate):
            model = candidate
            break
    if model is None:
        raise SystemExit("no model.gltf / model.glb in %s" % base_dir)

    manifest = {}
    if os.path.isfile(manifest_path):
        try:
            manifest = json.load(open(manifest_path, "r", encoding="utf-8"))
        except Exception as exc:
            log("existing manifest unreadable (%s), starting fresh" % exc)

    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    except Exception:
        pass
    bpy.ops.import_scene.gltf(filepath=model)

    objects = [o for o in bpy.context.scene.objects]
    meshes = [o for o in objects if o.type == "MESH"]
    arms = [o for o in objects if o.type == "ARMATURE"]

    manifest["asset_id"] = manifest.get("asset_id") or asset_id
    manifest.setdefault("version", 1)
    manifest.setdefault("model", os.path.basename(model))
    manifest.setdefault("license_file", "LICENSE")
    manifest.setdefault("unit_scale", 1.0)
    manifest.setdefault("forward_axis", "-Y")

    manifest["meshes"] = sorted(o.name for o in meshes)
    manifest["materials"] = sorted(m.name for m in bpy.data.materials if m.users > 0)

    bones = []
    if arms:
        if not manifest.get("armature"):
            manifest["armature"] = arms[0].name
        arm = arms[0]
        if manifest.get("armature"):
            for candidate in arms:
                if candidate.name == manifest["armature"]:
                    arm = candidate
        bones = sorted(b.name for b in arm.data.bones)
    manifest["bones"] = bones
    manifest["skinning"] = bool(bones) and manifest.get("skinning", True) is not False

    keys = []
    for obj in meshes:
        if obj.data.shape_keys:
            keys.extend(k.name for k in obj.data.shape_keys.key_blocks)
    manifest["shape_keys"] = sorted(set(keys))

    hair = manifest.setdefault("hair", {})
    hair.setdefault("head_bone", "head")
    hair.setdefault("forward_axis", "-Y")
    hair.setdefault("styles", {})
    if not hair.get("meshes"):
        hair["meshes"] = sorted(o.name for o in meshes if re.search(r"hair", o.name, re.I))
    hair.pop("notes", None)

    manifest.pop("notes", None)
    manifest["notes"] = "Auto-populated by tools/populate_manifest.py — trim anything the AI should not touch."

    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    log("wrote %s" % manifest_path)
    log("meshes=%d bones=%d materials=%d" % (len(manifest["meshes"]), len(bones), len(manifest["materials"])))


if __name__ == "__main__":
    main()
