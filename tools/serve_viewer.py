#!/usr/bin/env python3
"""Serve the assembled web viewer locally, exactly as GitHub Pages serves it.

This is the same layout the deploy job publishes (``docs/``), including the
``outputs/<variant>/<outfit>/…`` asset tree and the generated ``manifest.json``,
so a local preview reproduces the deployed site - relative paths, MIME types and
all.

    python tools/serve_viewer.py                 # build + serve on :8099
    python tools/serve_viewer.py --no-build      # serve an existing docs/
    python tools/serve_viewer.py --out site      # different staging directory
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver
import sys
from typing import ClassVar, Dict, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import build_site  # noqa: E402  (path bootstrap above)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".fbx": "application/octet-stream",
    ".bin": "application/octet-stream",
    ".md": "text/markdown; charset=utf-8",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}


class StudioHandler(http.server.SimpleHTTPRequestHandler):
    """Static handler with the MIME types and caching rules Pages uses."""

    extensions_map: ClassVar[Dict[str, str]] = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        **CONTENT_TYPES,
    }

    def end_headers(self) -> None:
        # Deploys overwrite assets in place; never let a browser cache an old GLB.
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(directory: str, port: int, bind: str) -> None:
    handler = functools.partial(StudioHandler, directory=directory)
    with ThreadedServer((bind, port), handler) as httpd:
        print(f"serving {directory} on http://{bind}:{port}/")
        print("  ctrl-c to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--out", default=os.path.join(REPO_ROOT, "docs"), help="staging directory")
    parser.add_argument("--outputs-root", default=os.path.join(REPO_ROOT, "outputs"))
    parser.add_argument(
        "--no-build", action="store_true", help="serve without re-assembling the site"
    )
    args = parser.parse_args(argv)

    if not args.no_build:
        try:
            manifest = build_site.build(
                args.out,
                web_root=os.path.join(REPO_ROOT, "web_viewer"),
                outputs_root=args.outputs_root,
                assets_root=os.path.join(REPO_ROOT, "assets"),
            )
        except build_site.SiteError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        outfits = ", ".join(outfit["id"] for outfit in manifest["outfits"])
        print(f"site assembled at {args.out} ({len(manifest['outfits'])} outfit(s): {outfits})")

    if not os.path.isdir(args.out):
        print(f"error: {args.out} does not exist (drop --no-build to build it)", file=sys.stderr)
        return 1

    serve(args.out, args.port, args.bind)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
