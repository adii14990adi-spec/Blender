#!/usr/bin/env python3
"""Serve the asset review page the same way GitHub Pages will.

The viewer loads ``./outputs/<variant>/<outfit>/<outfit>_Modular.glb`` relative
to itself, which is how ``blender-build.yml`` assembles the published site
(``site/index.html`` next to ``site/outputs/...``). Serving the repository root
does not reproduce that layout, so this tool builds it in ``site/`` and serves
it:

    python tools/serve_viewer.py --port 8099
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import shutil
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO_ROOT, "site")
VIEWER = os.path.join(REPO_ROOT, "web_viewer", "index.html")
OUTPUTS = os.path.join(REPO_ROOT, "outputs")


def build_site(outfit: str = "jacket_urban_01") -> list[str]:
    if os.path.isdir(SITE):
        shutil.rmtree(SITE)
    os.makedirs(SITE, exist_ok=True)
    shutil.copyfile(VIEWER, os.path.join(SITE, "index.html"))

    copied: list[str] = []
    if not os.path.isdir(OUTPUTS):
        return copied
    for variant in sorted(os.listdir(OUTPUTS)):
        variant_dir = os.path.join(OUTPUTS, variant)
        if not os.path.isdir(variant_dir):
            continue
        # Support both layouts: outputs/<variant>/<outfit>/ and outputs/<outfit>/.
        candidates = (
            [os.path.join(variant_dir, outfit)]
            if os.path.isdir(os.path.join(variant_dir, outfit))
            else [variant_dir]
        )
        for source in candidates:
            destination = os.path.join(SITE, "outputs", variant, outfit)
            os.makedirs(destination, exist_ok=True)
            for name in sorted(os.listdir(source)):
                if not (name.endswith(".glb") or name.endswith(".json")):
                    continue
                shutil.copyfile(os.path.join(source, name), os.path.join(destination, name))
                copied.append(os.path.join("outputs", variant, outfit, name))
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--outfit", default="jacket_urban_01")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args(argv)

    copied = build_site(args.outfit)
    if not copied:
        print(
            f"no exported assets found under {OUTPUTS}; run the pipeline first "
            f"(python tools/run_headless.py --variant male --outfit {args.outfit} --stage full)",
            file=sys.stderr,
        )
    for rel in copied:
        print("  ", rel)

    if args.build_only:
        print(f"site assembled at {SITE}")
        return 0

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=SITE)
    with http.server.ThreadingHTTPServer((args.bind, args.port), handler) as server:
        print(f"serving {SITE} on http://{args.bind}:{args.port}/ (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
