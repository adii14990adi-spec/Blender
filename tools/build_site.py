#!/usr/bin/env python3
"""Assemble the static web viewer that GitHub Pages serves.

The deployable site is built from three inputs and nothing else:

* ``web_viewer/``   - the frontend (HTML/CSS/JS + the vendored model-viewer build)
* ``outputs/``      - the exports the pipeline produced (``<variant>/<outfit>/*.glb``)
* ``assets/``       - the outfit definitions, used for display names and regions

Output layout (all paths inside the site are **relative**, so the same build
works at ``https://user.github.io/repo/`` and at the domain root)::

    <out>/index.html
    <out>/css/styles.css
    <out>/js/app.js
    <out>/vendor/model-viewer.min.js
    <out>/manifest.json
    <out>/outputs/<variant>/<outfit>/<outfit>_Modular.glb
    <out>/outputs/<variant>/<outfit>/<outfit>_Modular.fbx
    <out>/outputs/<variant>/<outfit>/preview.png
    <out>/outputs/<variant>/<outfit>/validation_report.json

The manifest is the single source of truth for the frontend: it lists every
character, every outfit, every export path, its size, and the validation verdict
that the pipeline recorded. Files that do not exist are never listed, so the
"download" buttons can only ever point at real artifacts.

Usage::

    python tools/build_site.py --out docs
    python tools/build_site.py --out docs --check-only   # validate an existing build
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Frontend files/directories copied verbatim into the site.
WEB_FILES = ("index.html", "favicon.svg", "css", "js", "vendor")

#: Extra files copied next to each export (only when they exist).
ASSET_SIDECARS = ("preview.png", "validation_report.json")

MANIFEST_NAME = "manifest.json"


class SiteError(RuntimeError):
    """Raised when the site cannot be assembled or fails validation."""


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def _load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _outfit_definitions(assets_root: str) -> Dict[str, Dict[str, Any]]:
    path = os.path.join(assets_root, "outfits", "outfits.json")
    if not os.path.exists(path):
        return {}
    try:
        data = _load_json(path)
    except json.JSONDecodeError:
        return {}
    return data.get("outfits") or {}


def _body_manifest(assets_root: str) -> Dict[str, Any]:
    path = os.path.join(assets_root, "base_body", "manifest.json")
    if not os.path.exists(path):
        return {}
    try:
        return _load_json(path)
    except json.JSONDecodeError:
        return {}


def _body_variants(assets_root: str) -> Dict[str, Dict[str, Any]]:
    return _body_manifest(assets_root).get("variants") or {}


def _variant_label(variant: str) -> str:
    return variant.replace("_", " ").title()


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _rel(*parts: str) -> str:
    return "/".join(part.strip("/") for part in parts if part)


def _export_entry(
    outputs_root: str,
    assets_root: str,
    variant: str,
    outfit: str,
    outfit_def: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Describe one ``<variant>/<outfit>`` build, or ``None`` when nothing exists."""
    folder = os.path.join(outputs_root, variant, outfit)
    glb_name = f"{outfit}_Modular.glb"
    fbx_name = f"{outfit}_Modular.fbx"
    glb_path = os.path.join(folder, glb_name)
    fbx_path = os.path.join(folder, fbx_name)
    if not (os.path.isfile(glb_path) or os.path.isfile(fbx_path)):
        return None

    rel_folder = _rel("outputs", variant, outfit)
    entry: Dict[str, Any] = {
        "variant": variant,
        "outfit": outfit,
        "label": f"{outfit_def.get('display_name') or outfit} ({_variant_label(variant)})",
        "kind": outfit_def.get("garment_type"),
        "region": outfit_def.get("region"),
        "glb": None,
        "fbx": None,
        "preview": None,
        "validation": None,
        "stats": {},
        "built_at": None,
    }

    if os.path.isfile(glb_path):
        entry["glb"] = {
            "path": _rel(rel_folder, glb_name),
            "bytes": os.path.getsize(glb_path),
            "size": _human_bytes(os.path.getsize(glb_path)),
        }
    if os.path.isfile(fbx_path):
        entry["fbx"] = {
            "path": _rel(rel_folder, fbx_name),
            "bytes": os.path.getsize(fbx_path),
            "size": _human_bytes(os.path.getsize(fbx_path)),
        }

    preview = os.path.join(folder, "preview.png")
    if os.path.isfile(preview):
        entry["preview"] = {"path": _rel(rel_folder, "preview.png")}

    report_path = os.path.join(folder, "validation_report.json")
    if os.path.isfile(report_path):
        stages: List[Dict[str, str]] = []
        status = "unknown"
        stats: Dict[str, Any] = {}
        pipeline_version = None
        try:
            report = _load_json(report_path)
            status = str(report.get("status") or "unknown")
            pipeline_version = report.get("pipeline_version")
            for stage in report.get("stages") or []:
                stages.append(
                    {
                        "stage": str(stage.get("stage")),
                        "status": str(stage.get("status")),
                        "error": stage.get("error"),
                    }
                )
            export_metrics = next(
                (s for s in report.get("stages") or [] if s.get("stage") == "export"), {}
            )
            glb_stats = ((export_metrics.get("metrics") or {}).get("glb_validation") or {}).get(
                "stats"
            ) or {}
            garment = (export_metrics.get("metrics") or {}).get("garment_stats") or {}
            stats = {
                "vertices": glb_stats.get("vertices"),
                "triangles": glb_stats.get("triangles"),
                "materials": glb_stats.get("materials"),
                "animations": glb_stats.get("animations"),
                "joints": (glb_stats.get("joints") or [None])[0],
                "faces": garment.get("face_count"),
            }
        except (json.JSONDecodeError, OSError) as exc:  # pragma: no cover - corrupt report
            status = f"unreadable ({exc.__class__.__name__})"
            pipeline_version = None
        entry["validation"] = {
            "path": _rel(rel_folder, "validation_report.json"),
            "status": status,
            "stages": stages,
            "pipeline_version": pipeline_version,
        }
        entry["stats"] = {k: v for k, v in stats.items() if v is not None}
        entry["built_at"] = datetime.fromtimestamp(
            os.path.getmtime(report_path), tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")

    return entry


def discover(outputs_root: str, assets_root: str) -> Dict[str, Any]:
    """Build the manifest dict from what actually exists on disk."""
    outfit_defs = _outfit_definitions(assets_root)
    body_manifest = _body_manifest(assets_root)
    body_variants = body_manifest.get("variants") or {}

    variants = (
        sorted(
            name
            for name in os.listdir(outputs_root)
            if os.path.isdir(os.path.join(outputs_root, name))
        )
        if os.path.isdir(outputs_root)
        else []
    )

    outfits: Dict[str, Dict[str, Any]] = {}
    for variant in variants:
        variant_dir = os.path.join(outputs_root, variant)
        for outfit in sorted(os.listdir(variant_dir)):
            outfit_def = outfit_defs.get(outfit) or {}
            entry = _export_entry(outputs_root, assets_root, variant, outfit, outfit_def)
            if entry is None:
                continue
            record = outfits.setdefault(
                outfit,
                {
                    "id": outfit,
                    "name": outfit_def.get("display_name") or outfit.replace("_", " ").title(),
                    "kind": outfit_def.get("garment_type"),
                    "region": outfit_def.get("region"),
                    "variants": {},
                    "compatibility": [],
                },
            )
            record["variants"][variant] = entry
            if variant not in record["compatibility"]:
                record["compatibility"].append(variant)

    characters = []
    for variant in variants:
        definition = body_variants.get(variant) or {}
        characters.append(
            {
                "id": variant,
                "label": _variant_label(variant),
                "body_object": definition.get("body_object"),
                "height_m": definition.get("expected_scale_metres"),
            }
        )

    outfit_list = []
    for record in outfits.values():
        record["compatibility"] = sorted(record["compatibility"])
        record["variants"] = dict(sorted(record["variants"].items()))
        outfit_list.append(record)
    outfit_list.sort(key=lambda item: item["id"])

    pipeline_versions = {
        entry["validation"]["pipeline_version"]
        for outfit in outfit_list
        for entry in outfit["variants"].values()
        if entry.get("validation") and entry["validation"].get("pipeline_version")
    }

    default_variant = body_manifest.get("default_variant")
    if default_variant not in {character["id"] for character in characters}:
        default_variant = characters[0]["id"] if characters else None

    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "tools/build_site.py",
        "pipeline_version": ",".join(sorted(pipeline_versions)) or None,
        "default_variant": default_variant,
        "characters": characters,
        "outfits": outfit_list,
    }


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def manifest_paths(manifest: Dict[str, Any]) -> Iterable[Tuple[str, str]]:
    """Yield ``(description, relative path)`` for every file the manifest lists."""
    for outfit in manifest.get("outfits") or []:
        for variant, entry in (outfit.get("variants") or {}).items():
            for key in ("glb", "fbx", "preview"):
                item = entry.get(key)
                if item and item.get("path"):
                    yield f"{outfit['id']}/{variant}/{key}", item["path"]
            validation = entry.get("validation") or {}
            if validation.get("path"):
                yield f"{outfit['id']}/{variant}/validation", validation["path"]


def validate_manifest(  # noqa: PLR0912 - a manifest has many independent invariants
    manifest: Dict[str, Any],
    site_root: str,
    *,
    require_glb: bool = True,
) -> List[str]:
    """Return a list of problems (empty when the site is publishable)."""
    problems: List[str] = []
    root = os.path.abspath(site_root)

    for where, path in manifest_paths(manifest):
        if path.startswith("/"):
            problems.append(f"{where}: root-relative path {path!r} breaks GitHub project pages")
            continue
        if "://" in path:
            problems.append(f"{where}: absolute URL {path!r} is not allowed in the manifest")
            continue
        if ".." in path.split("/"):
            problems.append(f"{where}: path {path!r} escapes the site root")
            continue
        absolute = os.path.join(root, path)
        if not os.path.isfile(absolute):
            problems.append(f"{where}: {path} does not exist in {site_root}")
        elif os.path.getsize(absolute) == 0:
            problems.append(f"{where}: {path} is empty")

    if not manifest.get("outfits"):
        problems.append("manifest lists no outfits - nothing to show in the viewer")
    if require_glb:
        for outfit in manifest.get("outfits") or []:
            for variant, entry in (outfit.get("variants") or {}).items():
                if not (entry.get("glb") or {}).get("path"):
                    problems.append(f"{outfit['id']}/{variant}: no GLB - the viewer cannot load it")
    for character in manifest.get("characters") or []:
        if not character.get("id"):
            problems.append("character entry without an id")
    return problems


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def _copy_web(web_root: str, site_root: str) -> List[str]:
    copied = []
    for name in WEB_FILES:
        src = os.path.join(web_root, name)
        dst = os.path.join(site_root, name)
        if not os.path.exists(src):
            if name in {"index.html"}:
                raise SiteError(f"frontend file missing: {src}")
            continue
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        copied.append(name)
    return copied


def _copy_asset_sidecars(outputs_root: str, site_root: str, manifest: Dict[str, Any]) -> None:
    for outfit in manifest.get("outfits") or []:
        for variant, entry in (outfit.get("variants") or {}).items():
            for key in ("glb", "fbx"):
                item = entry.get(key)
                if not item:
                    continue
                name = os.path.basename(item["path"])
                src = os.path.join(outputs_root, variant, outfit["id"], name)
                dst = os.path.join(site_root, item["path"])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if not os.path.isfile(src):
                    raise SiteError(f"asset vanished while assembling the site: {src}")
                shutil.copy2(src, dst)
            for name in ASSET_SIDECARS:
                src = os.path.join(outputs_root, variant, outfit["id"], name)
                if os.path.isfile(src):
                    dst = os.path.join(site_root, "outputs", variant, outfit["id"], name)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)


def build(  # noqa: PLR0913 - explicit, named inputs beat a config object here
    out: str,
    *,
    web_root: str,
    outputs_root: str,
    assets_root: str,
    clean: bool = True,
    require_glb: bool = True,
) -> Dict[str, Any]:
    """Assemble the site into ``out`` and return the manifest that was written."""
    out = os.path.abspath(out)
    manifest = discover(outputs_root, assets_root)
    if not manifest["outfits"]:
        raise SiteError(f"no exports found under {outputs_root}; run the Blender pipeline first")

    if clean:
        for name in ("css", "js", "vendor", "outputs"):
            shutil.rmtree(os.path.join(out, name), ignore_errors=True)
        for name in ("index.html", "favicon.svg", MANIFEST_NAME):
            try:
                os.remove(os.path.join(out, name))
            except FileNotFoundError:
                pass
    os.makedirs(out, exist_ok=True)

    _copy_web(web_root, out)
    _copy_asset_sidecars(outputs_root, out, manifest)

    with open(os.path.join(out, MANIFEST_NAME), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=False)
        fh.write("\n")

    problems = validate_manifest(manifest, out, require_glb=require_glb)
    if problems:
        raise SiteError("site validation failed:\n  - " + "\n  - ".join(problems))
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="docs", help="site output directory (default: docs)")
    parser.add_argument("--web-root", default=os.path.join(REPO_ROOT, "web_viewer"))
    parser.add_argument("--outputs-root", default=os.path.join(REPO_ROOT, "outputs"))
    parser.add_argument("--assets-root", default=os.path.join(REPO_ROOT, "assets"))
    parser.add_argument("--check-only", action="store_true", help="validate an existing build")
    parser.add_argument("--allow-missing-glb", action="store_true")
    parser.add_argument("--json", help="write a build summary here")
    args = parser.parse_args(argv)

    summary: Dict[str, Any] = {"out": os.path.abspath(args.out)}
    try:
        if args.check_only:
            manifest_path = os.path.join(args.out, MANIFEST_NAME)
            if not os.path.exists(manifest_path):
                raise SiteError(f"{manifest_path} does not exist")
            manifest = _load_json(manifest_path)
        else:
            manifest = build(
                args.out,
                web_root=args.web_root,
                outputs_root=args.outputs_root,
                assets_root=args.assets_root,
                require_glb=not args.allow_missing_glb,
            )
        problems = validate_manifest(manifest, args.out, require_glb=not args.allow_missing_glb)
        summary.update(
            {
                "ok": not problems,
                "problems": problems,
                "outfits": [o["id"] for o in manifest.get("outfits") or []],
                "characters": [c["id"] for c in manifest.get("characters") or []],
                "files": list(manifest_paths(manifest)),
            }
        )
        print(
            f"manifest: {len(manifest.get('outfits') or [])} outfit(s), "
            f"{len(manifest.get('characters') or [])} character(s)"
        )
        for outfit in manifest.get("outfits") or []:
            for variant, entry in (outfit.get("variants") or {}).items():
                glb = (entry.get("glb") or {}).get("size", "-")
                fbx = (entry.get("fbx") or {}).get("size", "-")
                status = (entry.get("validation") or {}).get("status", "-")
                print(
                    f"  {outfit['id']:<20} {variant:<7} glb {glb:>9}  fbx {fbx:>9}  status {status}"
                )
        if problems:
            print("SITE VALIDATION FAILED", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            summary["ok"] = False
        else:
            print(f"site OK: {args.out}")
    except SiteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        summary.update({"ok": False, "problems": [str(exc)]})
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2)
        return 1

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
