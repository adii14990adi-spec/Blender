#!/usr/bin/env python3
"""Turn pipeline run JSONs into a human + machine readable build report.

    python tools/build_report.py \
        --run male=/tmp/build_male.json \
        --run female=/tmp/build_female.json \
        --out docs/BUILD_REPORT.md \
        --json outputs/build_report.json

The report is generated from the data the pipeline actually produced: stage
statuses, garment topology, fit clearance, skin weights, UV/material checks and
the GLB/FBX validation results. Nothing is inferred from "the command exited 0".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLENDER_DIR = os.path.join(REPO_ROOT, "blender")
if BLENDER_DIR not in sys.path:
    sys.path.insert(0, BLENDER_DIR)

STAGE_LABELS = {
    "shell": "Garment shell",
    "shrinkwrap": "Surface fit + thickness",
    "weights": "Skin weights",
    "uv_material": "UV unwrap + PBR material",
    "export": "FBX / GLB export",
}


def _stage(run: Dict[str, Any], name: str) -> Dict[str, Any]:
    for stage in run.get("stages", []):
        if stage.get("stage") == name:
            return stage
    return {}


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "**no**"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def summarise(run: Dict[str, Any]) -> Dict[str, Any]:
    shell = _stage(run, "shell").get("metrics", {})
    fit = _stage(run, "shrinkwrap").get("metrics", {})
    weights = _stage(run, "weights").get("metrics", {})
    material = _stage(run, "uv_material").get("metrics", {})
    export = _stage(run, "export").get("metrics", {})

    fitted = fit.get("fit", {})
    solidify = fit.get("shell", {})
    skin = weights.get("skin", {})
    verification = skin.get("verification", {})
    uv = material.get("uv", {})
    textures = material.get("textures", {})
    glb = export.get("glb_validation", {})
    fbx = export.get("fbx_validation", {})
    khronos = export.get("khronos_validator", {})

    return {
        "variant": run.get("variant"),
        "outfit": run.get("outfit"),
        "status": run.get("status"),
        "error": run.get("error"),
        "stages": {stage["stage"]: stage.get("status") for stage in run.get("stages", [])},
        "garment": {
            "panel_faces": shell.get("garment_stats", {}).get("face_count"),
            "body_faces": shell.get("body_stats", {}).get("face_count"),
            "opening_faces_removed": shell.get("patch", {}).get("opening_faces_removed"),
            "final_vertices": solidify.get("vertex_count"),
            "final_faces": solidify.get("face_count"),
            "thickness_m": solidify.get("thickness"),
            "volume_m3": solidify.get("volume_m3"),
            "watertight": solidify.get("boundary_edges") == 0
            and solidify.get("non_manifold_edges") == 0,
            "degenerate_faces": solidify.get("degenerate_faces"),
        },
        "fit": {
            "min_clearance_m": fitted.get("min_clearance"),
            "mean_clearance_m": fitted.get("mean_clearance"),
            "max_clearance_m": fitted.get("max_clearance"),
            "penetrating_vertices": fitted.get("penetrating_vertices"),
            "max_edge_distortion": (fitted.get("edge_distortion") or {}).get("max"),
            "p99_edge_distortion": (fitted.get("edge_distortion") or {}).get("p99"),
        },
        "skin": {
            "armature": skin.get("armature"),
            "bones": len(skin.get("deform_bones") or []),
            "vertices": verification.get("vertices"),
            "max_influences": verification.get("max_influences"),
            "unweighted": verification.get("unweighted_vertices"),
            "unnormalised": verification.get("unnormalised_vertices"),
            "transfer": skin.get("transfer_method"),
        },
        "uv": {
            "layers": uv.get("uv_layer_count"),
            "method": uv.get("method"),
            "coverage": uv.get("coverage"),
            "degenerate_faces": uv.get("degenerate_faces"),
        },
        "textures": {
            "ok": textures.get("ok"),
            "slots": {
                slot: {
                    "size": data.get("size"),
                    "packed": data.get("packed"),
                    "mean": data.get("mean"),
                }
                for slot, data in (textures.get("slots") or {}).items()
            },
        },
        "glb": {
            "ok": glb.get("ok"),
            "errors": glb.get("errors") or [],
            "vertices": glb.get("stats", {}).get("vertices"),
            "triangles": glb.get("stats", {}).get("triangles"),
            "skins": glb.get("stats", {}).get("skins"),
            "joints": glb.get("stats", {}).get("joints"),
            "images": glb.get("stats", {}).get("images"),
            "has_uvs": glb.get("stats", {}).get("has_uvs"),
            "has_tangents": glb.get("stats", {}).get("has_tangents"),
            "bytes": glb.get("stats", {}).get("file_size_bytes"),
            "khronos_errors": khronos.get("numErrors"),
            "khronos_warnings": khronos.get("numWarnings"),
        },
        "fbx": {
            "ok": fbx.get("ok"),
            "errors": fbx.get("errors") or [],
            "clusters": fbx.get("stats", {}).get("clusters"),
            "bone_models": fbx.get("stats", {}).get("bone_models"),
            "uv_layers": fbx.get("stats", {}).get("uv_layers"),
            "bytes": fbx.get("stats", {}).get("file_size_bytes"),
        },
        "pieces": [
            {
                "key": piece.get("key"),
                "region": piece.get("region"),
                "fit_mode": piece.get("fit_mode") or (piece.get("fit") or {}).get("fit_mode"),
                "faces": (piece.get("shell") or {}).get("face_count"),
                "min_clearance_m": (piece.get("fit") or {}).get("min_clearance"),
                "penetrating": (piece.get("fit") or {}).get("penetrating_vertices"),
                "watertight": (piece.get("shell") or {}).get("boundary_edges") == 0
                and (piece.get("shell") or {}).get("non_manifold_edges") == 0,
            }
            for piece in fit.get("pieces") or []
        ],
        "detail_count": len(shell.get("details") or []),
        "material_count": material.get("material_count") or 1,
        "material_names": [entry.get("name") for entry in material.get("materials") or []],
        "artifacts": {
            kind: (
                os.path.relpath(path, REPO_ROOT)
                if path and os.path.abspath(path).startswith(REPO_ROOT)
                else path
            )
            for kind, path in (run.get("artifacts") or {}).items()
        },
    }


def _status_icon(ok: Any) -> str:
    return "PASS" if ok in (True, "pass") else "FAIL"


def to_markdown(summaries: List[Dict[str, Any]], generated: str) -> str:  # noqa: PLR0915
    lines: List[str] = []
    lines.append("# Build report")
    lines.append("")
    lines.append(f"_Generated {generated} by `tools/build_report.py`._")
    lines.append("")
    overall = all(summary["status"] == "pass" for summary in summaries)
    lines.append(f"**Overall: {'PASS' if overall else 'FAIL'}**")
    lines.append("")

    for summary in summaries:
        icon = _status_icon(summary["status"] == "pass")
        title = f"## {summary['variant']} · {summary['outfit']} — {icon}"
        lines.append(title)
        lines.append("")
        if summary.get("error"):
            lines.append(f"> **Error:** {summary['error']}")
            lines.append("")

        lines.append("| Stage | Result |")
        lines.append("| --- | --- |")
        for stage, state in summary["stages"].items():
            lines.append(f"| {STAGE_LABELS.get(stage, stage)} | {_status_icon(state)} |")
        lines.append("")

        garment = summary["garment"]
        lines.append("| Garment | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Body faces (source) | {_fmt(garment['body_faces'])} |")
        lines.append(f"| Panel faces (cut from body) | {_fmt(garment['panel_faces'])} |")
        lines.append(f"| Front-opening faces removed | {_fmt(garment['opening_faces_removed'])} |")
        lines.append(
            f"| Final vertices / faces | {_fmt(garment['final_vertices'])} / "
            f"{_fmt(garment['final_faces'])} |"
        )
        lines.append(f"| Thickness | {_fmt(garment['thickness_m'])} m |")
        lines.append(f"| Volume | {_fmt(garment['volume_m3'], 6)} m³ |")
        lines.append(f"| Watertight (0 boundary/non-manifold) | {_fmt(garment['watertight'])} |")
        lines.append(f"| Degenerate faces | {_fmt(garment['degenerate_faces'])} |")
        lines.append("")

        pieces = summary.get("pieces") or []
        if pieces:
            lines.append(
                f"Multi-piece outfit — {len(pieces)} pieces, "
                f"{summary.get('detail_count', 0)} hard details, "
                f"{summary.get('material_count', 1)} materials"
            )
            lines.append("")
            lines.append(
                "| Piece | Region | Fit mode | Faces | Min clearance | Penetrating | Watertight |"
            )
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for piece in pieces:
                lines.append(
                    f"| `{piece['key']}` | {_fmt(piece['region'])} | "
                    f"{_fmt(piece['fit_mode'])} | {_fmt(piece['faces'])} | "
                    f"{_fmt(piece['min_clearance_m'], 4)} m | {_fmt(piece['penetrating'])} | "
                    f"{_fmt(piece['watertight'])} |"
                )
            lines.append("")
            if summary.get("material_names"):
                lines.append(
                    "Materials: " + ", ".join(f"`{name}`" for name in summary["material_names"])
                )
                lines.append("")

        fit = summary["fit"]
        lines.append("| Fit | Value |")
        lines.append("| --- | --- |")
        clearances = (
            f"{_fmt(fit['min_clearance_m'], 4)} / {_fmt(fit['mean_clearance_m'], 4)}"
            f" / {_fmt(fit['max_clearance_m'], 4)} m"
        )
        lines.append(f"| Min / mean / max clearance | {clearances} |")
        lines.append(f"| Penetrating vertices | {_fmt(fit['penetrating_vertices'])} |")
        distortion = (
            f"{_fmt(fit['max_edge_distortion'], 2)} / {_fmt(fit['p99_edge_distortion'], 3)}"
        )
        lines.append(f"| Edge distortion max / p99 | {distortion} |")
        lines.append("")

        skin = summary["skin"]
        lines.append("| Skinning | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Armature | {_fmt(skin['armature'])} |")
        lines.append(f"| Deform bones | {_fmt(skin['bones'])} |")
        lines.append(f"| Transferred vertices | {_fmt(skin['vertices'])} |")
        lines.append(f"| Max influences per vertex | {_fmt(skin['max_influences'])} |")
        lines.append(
            f"| Unweighted / unnormalised | {_fmt(skin['unweighted'])} / "
            f"{_fmt(skin['unnormalised'])} |"
        )
        lines.append(f"| Transfer method | {_fmt(skin['transfer'])} |")
        lines.append("")

        uv = summary["uv"]
        lines.append("| UV / Material | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| UV layers | {_fmt(uv['layers'])} |")
        lines.append(f"| Unwrap method | {_fmt(uv['method'])} |")
        lines.append(f"| UV coverage | {_fmt(uv['coverage'])} |")
        lines.append(f"| Degenerate UV faces | {_fmt(uv['degenerate_faces'])} |")
        lines.append(f"| Material slots | {_fmt(summary.get('material_count', 1))} |")
        texture_ok = summary["textures"]["ok"]
        lines.append(f"| Texture set valid | {_fmt(texture_ok)} |")
        for slot, data in summary["textures"]["slots"].items():
            size = data.get("size") or [None, None]
            mean = (data.get("mean") or [None])[0]
            lines.append(
                f"| {slot} map | {size[0]}x{size[1]}, packed={_fmt(data.get('packed'))}, "
                f"mean={_fmt(mean, 3)} |"
            )
        lines.append("")

        glb = summary["glb"]
        lines.append("| GLB | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Valid | {_fmt(glb['ok'])} |")
        lines.append(
            f"| Vertices / triangles | {_fmt(glb['vertices'])} / {_fmt(glb['triangles'])} |"
        )
        lines.append(f"| Skin / joints | {_fmt(glb['skins'])} / {_fmt(glb['joints'])} |")
        lines.append(
            f"| UVs / tangents / images | {_fmt(glb['has_uvs'])} / "
            f"{_fmt(glb['has_tangents'])} / {_fmt(glb['images'])} |"
        )
        lines.append(
            f"| Khronos errors / warnings | {_fmt(glb['khronos_errors'])} / "
            f"{_fmt(glb['khronos_warnings'])} |"
        )
        lines.append(f"| Size | {_fmt(glb['bytes'])} bytes |")
        lines.append("")

        fbx = summary["fbx"]
        lines.append("| FBX | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Valid | {_fmt(fbx['ok'])} |")
        lines.append(
            f"| Skin clusters / bone models | {_fmt(fbx['clusters'])} / "
            f"{_fmt(fbx['bone_models'])} |"
        )
        lines.append(f"| UV layers | {_fmt(fbx['uv_layers'])} |")
        lines.append(f"| Size | {_fmt(fbx['bytes'])} bytes |")
        lines.append("")

        artifacts = summary.get("artifacts") or {}
        if artifacts:
            lines.append("Artifacts:")
            for kind, path in artifacts.items():
                lines.append(f"- `{kind}`: `{path}`")
            lines.append("")

    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="VARIANT=PATH",
        help="pipeline run JSON (repeatable)",
    )
    parser.add_argument("--out", help="markdown report path")
    parser.add_argument("--json", dest="json_path", help="machine-readable report path")
    parser.add_argument("--timestamp", default=None, help="override the generation timestamp")
    args = parser.parse_args(argv)

    if not args.run:
        parser.error("at least one --run VARIANT=PATH is required")

    summaries: List[Dict[str, Any]] = []
    failures = 0
    for spec in args.run:
        if "=" not in spec:
            parser.error(f"--run expects VARIANT=PATH, got {spec!r}")
        variant, path = spec.split("=", 1)
        with open(path) as handle:
            run = json.load(handle)
        summary = summarise(run)
        summary["variant"] = summary.get("variant") or variant
        summaries.append(summary)
        if summary["status"] != "pass":
            failures += 1

    from datetime import datetime, timezone

    generated = args.timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    markdown = to_markdown(summaries, generated)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as handle:
            handle.write(markdown)
        print(f"wrote {args.out}")

    if args.json_path:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_path)), exist_ok=True)
        with open(args.json_path, "w") as handle:
            json.dump(
                {
                    "generated": generated,
                    "status": "pass" if not failures else "fail",
                    "builds": summaries,
                },
                handle,
                indent=2,
            )
        print(f"wrote {args.json_path}")

    if not args.out and not args.json_path:
        print(markdown)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
