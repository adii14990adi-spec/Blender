"""Validators: preflight, weights_check, topology_check, uv_check.

Each validator exposes ``run(blend_path, region, params)`` and returns a
JSON-serializable result dict with ``{"status", "metrics"}``.
"""
