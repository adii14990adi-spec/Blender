"""Headless Blender entry point for the modular clothing pipeline.

    blender -b --python-exit-code 1 --python blender/run_pipeline.py -- \\
        --variant female --outfit jacket_urban_01 --stage full --out /tmp/build.json

Body variant and outfit are resolved through the manifests
(``assets/base_body/manifest.json``, ``assets/outfits/outfits.json``); the old
hard-coded ``assets/base_body/BASE_BODY.blend`` is gone.

Exit codes
----------
0   every requested stage passed **and** the exported artifacts validated
1   a stage failed, an input was unusable, or an export failed validation

The failure detail is always written to ``--out`` as JSON before exiting. A
placeholder GLB is never written: a broken build must look broken.
"""

from __future__ import annotations

# ruff: noqa: PLC0415  (bpy_scripts imports stay lazy: the module must import
# outside Blender so argument parsing and --list-assets work without bpy.)
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

BLENDER_DIR = Path(__file__).resolve().parent
REPO_ROOT = BLENDER_DIR.parent
if str(BLENDER_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_DIR))

FULL_RUN_ALIASES = ("full", "all")
BOOLEAN_FLAGS = {
    "--allow-fallback-glb": ("allow_fallback_glb", True),  # accepted, then rejected
    "--list-assets": ("list_assets", True),
}
VALUE_FLAGS = {
    "--stage": "stage",
    "--region": "region",
    "--blend": "blend",
    "--variant": "variant",
    "--outfit": "outfit",
    "--params": "params",
    "--out": "out",
    "--body-manifest": "body_manifest",
    "--outfit-manifest": "outfit_manifest",
}


def _parse_args(argv: List[str]) -> Dict[str, Any]:
    args: Dict[str, Any] = {
        "stage": "validate",
        "region": "",
        "blend": "",
        "variant": "",
        "outfit": "",
        "params": "{}",
        "out": "/tmp/result.json",
        "body_manifest": "",
        "outfit_manifest": "",
        "list_assets": False,
        "allow_fallback_glb": False,
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in BOOLEAN_FLAGS:
            key, value = BOOLEAN_FLAGS[arg]
            args[key] = value
            index += 1
        elif arg in VALUE_FLAGS and index + 1 < len(argv):
            args[VALUE_FLAGS[arg]] = argv[index + 1]
            index += 2
        else:
            index += 1
    return args


def _resolve_inputs(args: Dict[str, Any]) -> "tuple[Any, Dict[str, Any], str]":
    """Resolve manifest paths, variant and outfit into validated objects."""
    from bpy_scripts import config

    body_manifest = args["body_manifest"] or None
    outfit_manifest = args["outfit_manifest"] or None
    if body_manifest:
        config.BODY_MANIFEST = os.path.abspath(body_manifest)
    if outfit_manifest:
        config.OUTFIT_MANIFEST = os.path.abspath(outfit_manifest)

    variant = config.resolve_variant(args["variant"], body_manifest)
    outfit = args["outfit"] or config.outfit_names(outfit_manifest)[0]
    settings = config.load_outfit(outfit, outfit_manifest)
    return variant, settings, outfit


def _blend_from_variant(variant, explicit: str) -> str:
    if explicit:
        return os.path.abspath(explicit)
    return variant.path


def _resolve_stage(stage: str) -> str:
    from bpy_scripts.pipeline import STAGE_ORDER, canonical_stage

    if stage in FULL_RUN_ALIASES or stage == "validate":
        return stage
    canonical = canonical_stage(stage)
    if canonical is None:
        raise ValueError(
            f"unknown stage {stage!r}; expected one of: full, validate, "
            + ", ".join(STAGE_ORDER)
            + " (aliases: garment, subdivide, uv, material, export_fbx)"
        )
    return canonical


def _run(stage: str, blend: str, region: str, params: Dict[str, Any]) -> Dict[str, Any]:
    from bpy_scripts import pipeline

    if stage in FULL_RUN_ALIASES:
        return pipeline.run_full_pipeline(blend, region, params)
    return pipeline.run_stage(stage, blend, region, params)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if args["list_assets"]:
        from bpy_scripts import config

        print("variants:", ", ".join(config.variant_names(args["body_manifest"] or None)))
        print("outfits:", ", ".join(config.outfit_names(args["outfit_manifest"] or None)))
        return 0

    result: Dict[str, Any] = {
        "status": "fail",
        "stage": args["stage"],
        "region": args["region"],
        "outfit": args["outfit"],
        "variant": args["variant"],
        "metrics": {},
        "checkpoint": None,
        "error": None,
    }
    out = args["out"]
    started = time.time()

    try:
        stage = _resolve_stage(args["stage"])
        variant, settings, outfit = _resolve_inputs(args)
        blend = _blend_from_variant(variant, args["blend"])
        region = args["region"] or settings.get("region") or "UPPER_BODY"

        try:
            params = json.loads(args["params"] or "{}")
            if not isinstance(params, dict):
                raise TypeError("--params must be a JSON object")
        except Exception as exc:
            raise ValueError(f"invalid --params: {exc}") from exc

        params.setdefault("outfit", outfit)
        params.setdefault("variant", variant.name)
        params.update(
            {
                "outfit": outfit,
                "variant": variant.name,
                "_body_manifest": args["body_manifest"] or None,
                "_outfit_manifest": args["outfit_manifest"] or None,
            }
        )
        if args["allow_fallback_glb"]:
            raise ValueError(
                "--allow-fallback-glb was removed on purpose: a build must never publish a "
                "placeholder GLB. Fix the export instead."
            )

        result.update({"outfit": outfit, "variant": variant.name, "region": region})
        result["inputs"] = {
            "blend": blend,
            "variant": variant.name,
            "variant_file": variant.path,
            "outfit": outfit,
            "region": region,
        }
        outcome = _run(stage, blend, region, params)
        result.update(
            {
                "status": outcome.get("status", "fail"),
                "stage": outcome.get("stage", stage),
                "metrics": outcome.get("metrics", {}),
                "checkpoint": outcome.get("checkpoint"),
                "error": outcome.get("error") or outcome.get("metrics", {}).get("error"),
                "outfit": outcome.get("outfit", outfit),
                "variant": outcome.get("variant", variant.name),
            }
        )
        if outcome.get("stages"):
            result["stages"] = outcome["stages"]
        if outcome.get("artifacts"):
            result["artifacts"] = outcome["artifacts"]
    except Exception as exc:  # configuration/usage error: report, never trace-crash
        result["error"] = f"{exc.__class__.__name__}: {exc}"
        result["metrics"] = {"error": result["error"]}
        if not isinstance(exc, (ValueError, RuntimeError)):
            result["metrics"]["traceback"] = traceback.format_exc()

    result["metrics"].setdefault("duration_seconds", round(time.time() - started, 3))
    out_dir = os.path.dirname(os.path.abspath(out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out, "w") as fh:
        json.dump(result, fh, indent=2, default=str)

    if result["status"] == "pass":
        print(f"[run_pipeline] {result['stage']}: PASS ({result['variant']}/{result['outfit']})")
        artifacts = result.get("artifacts") or {}
        for name, path in artifacts.items():
            print(f"[run_pipeline]   {name}: {path}")
        print(f"[run_pipeline]   result: {out}")
        return 0

    print(f"[run_pipeline] {result['stage']}: FAIL — {result.get('error')}", file=sys.stderr)
    print(json.dumps(result.get("metrics", {}), indent=2, default=str)[:6000], file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # last-resort guard: Blender must never exit 0 on a crash
        traceback.print_exc()
        sys.exit(1)
