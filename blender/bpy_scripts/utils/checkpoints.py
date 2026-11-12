"""Checkpoint index: proves a checkpoint was produced by *this* run.

The old pipeline reused any ``outputs/<outfit>/*.blend`` it found on disk. A
checkpoint left over from an earlier outfit, an earlier variant or an earlier
parameter set would be silently consumed, so a "green" export could contain a
male garment fitted to a female body. Every stage now writes an entry into
``outputs/<outfit>/run_index.json`` keyed by stage, and a checkpoint is only
treated as an input when its recorded signature matches the current run.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict

from .logging import get_logger
from .paths import PIPELINE_VERSION, outputs_index_path

logger = get_logger("utils.checkpoints")


def params_signature(variant: str, outfit: str, params: Dict[str, Any] | None = None) -> str:
    """Identity of a run: pipeline version + body variant + outfit.

    Stage-local parameters (``offset``, ``weight_limit``, …) are deliberately
    *not* part of the signature: they do not invalidate an upstream checkpoint,
    they simply make the stage that consumes them produce a new file. Including
    them would make ``--stage shrinkwrap --params {"offset": 0.02}`` reject the
    perfectly valid garment checkpoint from the previous run.
    """
    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "variant": variant,
        "outfit": outfit,
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


class RunIndex:
    """Reads/writes the per-outfit checkpoint index."""

    def __init__(self, outfit: str, signature: str, variant: str | None = None):
        self.outfit = outfit
        self.signature = signature
        self.path = outputs_index_path(outfit, variant)
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {"outfit": self.outfit, "signature": self.signature, "stages": {}}
        try:
            with open(self.path) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("ignoring unreadable run index %s (%s)", self.path, exc)
            return {"outfit": self.outfit, "signature": self.signature, "stages": {}}
        if not isinstance(data, dict):
            return {"outfit": self.outfit, "signature": self.signature, "stages": {}}
        data.setdefault("stages", {})
        if data.get("signature") != self.signature:
            # A different variant/param set: everything on disk is stale.
            logger.info(
                "run index signature changed (%s -> %s); previous checkpoints invalidated",
                data.get("signature"),
                self.signature,
            )
            data = {"outfit": self.outfit, "signature": self.signature, "stages": {}}
        return data

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)

    def record(self, stage: str, path: str, extra: Dict[str, Any] | None = None) -> None:
        try:
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
        except OSError:
            size = 0
            mtime = 0.0
        self.data.setdefault("stages", {})[stage] = {
            "path": os.path.abspath(path),
            "size": size,
            "mtime": mtime,
            "recorded_at": time.time(),
            "signature": self.signature,
            **(extra or {}),
        }
        self.save()

    def invalidate_from(self, stage: str) -> None:
        stages = self.data.setdefault("stages", {})
        stages.pop(stage, None)
        self.save()

    def invalidate_after(self, stage: str, order: "list[str]") -> None:
        """Drop every checkpoint downstream of ``stage`` (it is now outdated)."""
        stages = self.data.setdefault("stages", {})
        try:
            index = order.index(stage)
        except ValueError:
            return
        for downstream in order[index + 1 :]:
            stages.pop(downstream, None)
        self.save()

    def clear(self) -> None:
        self.data = {"outfit": self.outfit, "signature": self.signature, "stages": {}}
        self.save()

    def entry(self, stage: str) -> Dict[str, Any] | None:
        entry = self.data.get("stages", {}).get(stage)
        return entry if isinstance(entry, dict) else None

    def fresh_checkpoint(self, stage: str) -> str | None:
        """Path of ``stage``'s checkpoint if it is valid for this run, else None."""
        entry = self.entry(stage)
        if not entry:
            return None
        path = entry.get("path")
        if not path or not os.path.exists(path):
            logger.info("checkpoint for %s is missing on disk: %s", stage, path)
            return None
        if entry.get("signature") != self.signature:
            logger.info("checkpoint for %s belongs to another run signature", stage)
            return None
        try:
            stat = os.stat(path)
        except OSError:
            return None
        if (
            int(stat.st_size) != int(entry.get("size", -1))
            or abs(stat.st_mtime - float(entry.get("mtime", -1))) > 1e-6
        ):
            logger.info("checkpoint for %s changed on disk since it was recorded", stage)
            return None
        return path

    def upstream_input(self, stage: str, order: list[str], base_blend: str) -> "tuple[str, dict]":
        """Input file for ``stage``: previous stage's fresh checkpoint or the base blend."""
        index = order.index(stage)
        if index == 0:
            return base_blend, {"input_source": "base_blend", "input_path": base_blend}
        previous = order[index - 1]
        path = self.fresh_checkpoint(previous)
        if path:
            return path, {
                "input_source": "checkpoint",
                "input_stage": previous,
                "input_path": path,
            }
        return base_blend, {
            "input_source": "base_blend",
            "input_path": base_blend,
            "stale_checkpoint_ignored": True,
        }
