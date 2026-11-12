"""Pipeline stages: shell, shrinkwrap, weights, uv_material, export_fbx.

Each stage exposes ``run(blend_path, region, params, outfit)`` and returns a
JSON-serializable result dict: ``{"status", "stage", "metrics", "checkpoint"}``.
"""
