"""Pipeline orchestrator.

Stage graph
-----------
``shell -> shrinkwrap -> weights -> uv_material -> export``

Every stage is executed **once**. The old ``STAGE_ORDER`` listed both ``uv`` and
``material`` even though they pointed at the same module, so the unwrap and the
material assignment ran twice (stacking a second UV layer and a second material).

Aliases (``garment``, ``subdivide``, ``uv``, ``material``, ``export_fbx``) are
resolved to their canonical stage *before* execution, so an alias can never add
a second execution.

Checkpoints are only chained when the run index proves they belong to the
current variant/outfit/parameter set (:mod:`bpy_scripts.utils.checkpoints`), so a
stale ``outputs/`` directory can never be mistaken for a fresh build.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

# Allow ``python blender/bpy_scripts/pipeline.py`` and
# ``blender -b -P blender/run_pipeline.py`` (run_pipeline inserts ``blender/``).
_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER_DIR = os.path.dirname(_HERE)
if _BLENDER_DIR not in sys.path:
    sys.path.insert(0, _BLENDER_DIR)

from bpy_scripts.config import ConfigError, load_outfit, resolve_variant  # noqa: E402
from bpy_scripts.stages import (  # noqa: E402
    export_fbx,
    shell,
    shrinkwrap,
    uv_material,
    weights,
)
from bpy_scripts.utils.checkpoints import RunIndex, params_signature  # noqa: E402
from bpy_scripts.utils.logging import get_logger  # noqa: E402
from bpy_scripts.utils.paths import (  # noqa: E402
    PIPELINE_VERSION,
    ensure_dir,
    outfit_dir,
    validation_report_path,
)
from bpy_scripts.validators import (  # noqa: E402
    export_check,
    preflight,
    topology_check,
    uv_check,
    weights_check,
)

logger = get_logger("pipeline")

STAGE_ORDER: List[str] = ["shell", "shrinkwrap", "weights", "uv_material", "export"]

STAGE_REGISTRY: Dict[str, Any] = {
    "shell": shell,
    "shrinkwrap": shrinkwrap,
    "weights": weights,
    "uv_material": uv_material,
    "export": export_fbx,
}

# Aliases resolve to a canonical stage; none of them adds an execution.
STAGE_ALIASES: Dict[str, str] = {
    "garment": "shell",
    "generate_shell": "shell",
    "subdivide": "shell",
    "fit": "shrinkwrap",
    "skin": "weights",
    "skin_weights": "weights",
    "uv": "uv_material",
    "material": "uv_material",
    "materials": "uv_material",
    "export_fbx": "export",
    "glb": "export",
    "fbx": "export",
}

VALIDATOR_REGISTRY: Dict[str, Any] = {
    "preflight": preflight,
    "weights_check": weights_check,
    "topology_check": topology_check,
    "uv_check": uv_check,
    "export_check": export_check,
}


def canonical_stage(name: str) -> str | None:
    """Resolve a stage name or alias to its canonical stage."""
    if name in STAGE_REGISTRY:
        return name
    return STAGE_ALIASES.get(name)


def stage_names() -> List[str]:
    return list(STAGE_ORDER) + sorted(STAGE_ALIASES) + ["validate", "full", "all"]


def _outfit_from_blend(blend_path: str) -> str:
    return os.path.splitext(os.path.basename(blend_path))[0]


def _write_report(outfit: str, result: Dict[str, Any], variant: str | None = None) -> None:
    path = validation_report_path(outfit, variant)
    ensure_dir(os.path.dirname(path))
    with open(path, "w") as fh:
        json.dump(result, fh, indent=2, default=str)


def _context(params: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve variant + outfit once and pin them into the stage params."""
    variant = resolve_variant(params.get("variant"), params.get("_body_manifest"))
    outfit = str(params.get("outfit") or "")
    if not outfit:
        raise ConfigError("params['outfit'] is required (see assets/outfits/outfits.json)")
    settings = load_outfit(outfit, params.get("_outfit_manifest"))
    return {"variant": variant, "outfit": outfit, "settings": settings}


def run_stage(
    stage: str,
    blend: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run one stage (or ``validate``) and return a JSON-serialisable result."""
    params = dict(params or {})
    result: Dict[str, Any] = {
        "status": "fail",
        "stage": stage,
        "region": region,
        "metrics": {},
        "checkpoint": None,
        "error": None,
    }

    if stage == "validate":
        return run_validate(blend, region, params)

    canonical = canonical_stage(stage)
    if canonical is None:
        result["error"] = f"unknown stage {stage!r}; expected one of {stage_names()}"
        result["metrics"]["error"] = result["error"]
        return result

    module = STAGE_REGISTRY[canonical]
    outfit = params.get("outfit") or _outfit_from_blend(blend)
    params["outfit"] = outfit
    params["stage"] = canonical

    try:
        context = _context(params)
    except ConfigError as exc:
        result["error"] = str(exc)
        result["metrics"]["error"] = str(exc)
        return result

    if not blend:
        # An empty blend path means "use the variant's base body from the manifest".
        blend = context["variant"].path
        result["input"] = {"input_source": "variant_manifest", "input_path": blend}

    if region and region != context["settings"].get("region"):
        logger.info(
            "region %s overridden by outfit %s definition (%s)",
            region,
            outfit,
            context["settings"].get("region"),
        )
        region = context["settings"].get("region", region)
    params["variant"] = context["variant"].name
    result["region"] = region
    result["variant"] = context["variant"].name
    result["outfit"] = outfit
    result["canonical_stage"] = canonical

    # Resolve the input: an explicit upstream checkpoint (full run) or the
    # newest *fresh* checkpoint for this stage (standalone/resumed run).
    signature = params_signature(context["variant"].name, outfit, params)
    index = RunIndex(outfit, signature, context["variant"].name)
    if params.get("_upstream"):
        upstream = str(params["_upstream"])
        input_info = {"input_source": "explicit", "input_path": upstream}
    else:
        upstream, input_info = index.upstream_input(canonical, STAGE_ORDER, blend)
    params["_input_info"] = input_info
    result["input"] = input_info

    try:
        stage_result = module.run(blend_path=upstream, region=region, params=params, outfit=outfit)
    except Exception as exc:  # stage boundary must never crash the orchestrator
        detail = f"{exc.__class__.__name__}: {exc}"
        logger.exception("stage %s raised", canonical)
        result["error"] = detail
        result["metrics"]["error"] = detail
        return result

    result.update(
        {k: v for k, v in stage_result.items() if k in {"status", "metrics", "checkpoint", "error"}}
    )
    result.setdefault("metrics", {})
    result["metrics"]["stage"] = canonical
    result["metrics"]["pipeline_version"] = PIPELINE_VERSION

    if result.get("status") == "pass":
        checkpoint = result.get("checkpoint")
        if checkpoint:
            index.record(
                canonical,
                checkpoint,
                {
                    "variant": context["variant"].name,
                    "params": {k: v for k, v in sorted(params.items()) if not k.startswith("_")},
                },
            )
            # Everything downstream was derived from an older input.
            index.invalidate_after(canonical, STAGE_ORDER)
        _write_report(outfit, result, context["variant"].name)
        logger.info("stage %s PASS (checkpoint=%s)", canonical, result.get("checkpoint"))
    else:
        logger.error(
            "stage %s FAIL: %s", canonical, result.get("error") or result["metrics"].get("error")
        )
    return result


def run_validate(blend: str, region: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Dry-run validation of the newest checkpoint (or the base blend)."""
    outfit = params.get("outfit") or _outfit_from_blend(blend)
    try:
        context = _context({**params, "outfit": outfit})
    except ConfigError as exc:
        return {
            "outfit": outfit,
            "stage": "validate",
            "status": "fail",
            "metrics": {"error": str(exc)},
            "checkpoint": None,
            "error": str(exc),
        }

    if not blend:
        blend = context["variant"].path

    signature = params_signature(context["variant"].name, outfit, params)
    index = RunIndex(outfit, signature, context["variant"].name)
    checkpoint = None
    for stage in reversed(STAGE_ORDER[:-1]):
        checkpoint = index.fresh_checkpoint(stage)
        if checkpoint:
            break
    target = checkpoint or blend

    results: Dict[str, Any] = {}
    overall = "pass"
    for name, module in VALIDATOR_REGISTRY.items():
        try:
            res = module.run(
                blend_path=target,
                region=region,
                params={**params, "outfit": outfit, "variant": context["variant"].name},
            )
        except Exception as exc:
            res = {"status": "fail", "metrics": {"error": f"{exc.__class__.__name__}: {exc}"}}
        results[name] = res
        if res.get("status") != "pass":
            overall = "fail"

    failures = {
        name: res.get("metrics", {}).get("error")
        or res.get("metrics", {}).get("problems")
        or "failed"
        for name, res in results.items()
        if res.get("status") != "pass"
    }
    report = {
        "outfit": outfit,
        "variant": context["variant"].name,
        "stage": "validate",
        "status": overall,
        "metrics": {
            "validators": results,
            "validated_path": target,
            "validated_source": "checkpoint" if checkpoint else "base_blend",
        },
        "checkpoint": checkpoint,
        "error": "; ".join(f"{name}: {detail}" for name, detail in failures.items()) or None,
    }
    _write_report(outfit, report, context["variant"].name)
    return report


def run_full_pipeline(
    blend: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run every stage once, chaining fresh checkpoints and stopping on failure."""
    params = dict(params or {})
    try:
        context = _context(params)
    except ConfigError as exc:
        return {
            "status": "fail",
            "stage": "pipeline",
            "metrics": {"error": str(exc)},
            "checkpoint": None,
            "error": str(exc),
            "outfit": params.get("outfit"),
        }

    outfit = context["outfit"]
    params["outfit"] = outfit
    params["variant"] = context["variant"].name
    region = context["settings"].get("region", region)
    if not blend:
        blend = context["variant"].path
    signature = params_signature(context["variant"].name, outfit, params)

    index = RunIndex(outfit, signature, context["variant"].name)
    index.clear()  # a full run always starts from the body, never from stale output
    ensure_dir(outfit_dir(outfit, context["variant"].name))

    current = blend
    stages: List[Dict[str, Any]] = []
    last: Dict[str, Any] = {"status": "pass", "metrics": {}, "checkpoint": None}
    for stage in STAGE_ORDER:
        stage_params = dict(params)
        stage_params["_upstream"] = current
        stage_result = run_stage(stage, current, region, stage_params)
        stage_result.setdefault("metrics", {})
        stage_result["metrics"]["input_blend"] = current
        stage_result["outfit"] = outfit
        stages.append(
            {
                "stage": stage,
                "status": stage_result.get("status"),
                "checkpoint": stage_result.get("checkpoint"),
                "error": stage_result.get("error"),
                # Full per-stage metrics so the build report can be generated
                # from the run JSON alone.
                "metrics": stage_result.get("metrics", {}),
            }
        )
        last = stage_result
        if last.get("status") != "pass":
            break
        checkpoint = last.get("checkpoint")
        if not checkpoint or not os.path.exists(checkpoint):
            last["status"] = "fail"
            last["error"] = f"stage {stage} reported pass without a checkpoint on disk"
            last["metrics"]["error"] = last["error"]
            break
        current = checkpoint
        logger.info("chaining %s -> %s", stage, current)

    summary = {
        "outfit": outfit,
        "variant": context["variant"].name,
        "status": last.get("status", "fail"),
        "stage": last.get("stage"),
        "metrics": last.get("metrics", {}),
        "checkpoint": last.get("checkpoint"),
        "error": last.get("error") or last.get("metrics", {}).get("error"),
        "stages": stages,
        "pipeline_version": PIPELINE_VERSION,
    }
    summary["artifacts"] = last.get("metrics", {}).get("artifacts", {})
    _write_report(outfit, summary, context["variant"].name)
    return summary


# Backwards-compatible private alias (older entry points imported it directly).
_run_validate = run_validate
