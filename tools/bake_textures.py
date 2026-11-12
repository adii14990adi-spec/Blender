#!/usr/bin/env python3
"""Bake a tileable PBR texture set for an outfit — or derive one from your own map.

Two modes, both dependency-free (numpy + the standard library, no PIL/bpy):

**Synthesise** a fabric swatch and its maps:

    python tools/bake_textures.py --outfit tactical_tee_01 \
        --palette 0.115,0.125,0.070 --weave twill --cells 260 --roughness 0.92

**Derive** the other three maps from a base colour you supply (PNG, 8-bit
RGB/RGBA, non-interlaced — the format the pipeline itself writes):

    python tools/bake_textures.py --outfit tactical_tee_01 \
        --from assets/textures/tactical_tee_01/basecolor.png --derive-only

What it writes into ``assets/textures/<outfit>/``:

``basecolor.png``   sRGB-encoded albedo (unless ``--derive-only``)
``normal.png``      tangent-space normal map derived from the weave height field
``roughness.png``   roughness modulated by the same height field
``metallic.png``    flat metallic value (cloth is 0)
``textures.json``   provenance: palette, weave, seed, resolution, generator

Palette values are **linear** (the same numbers ``outfits.json`` uses).

``--encode`` picks how those values are stored in the PNG. The default,
``linear``, matches what the pipeline's own generator writes (byte payload holds
linear values, files are tagged sRGB and therefore render darker than the raw
number suggests) — use it for consistency with the committed outfits. Pass
``--encode srgb`` if you want a standards-compliant sRGB map for other tools, and
expect it to render brighter in the studio at the same palette.

Everything is periodic over the frame, so the maps tile seamlessly — important
because the UV stage uses a smart projection that lets islands land anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import zlib
from typing import Any, Dict, Tuple

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXTURE_ROOT = os.path.join(REPO_ROOT, "assets", "textures")
SLOTS = ("base_color", "normal", "roughness", "metallic")
WEAVES = ("twill", "plain", "ripstop")
GENERATOR = "tools/bake_textures.py"


# --------------------------------------------------------------------------- #
# PNG (8-bit RGB, no interlace) — read and write without third-party libraries
# --------------------------------------------------------------------------- #
def write_png(path: str, rgb: np.ndarray) -> str:
    """Write an ``H x W x 3`` float array in 0..1 as an 8-bit RGB PNG."""
    array = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"expected an H x W x 3 array, got {array.shape}")
    height, width = array.shape[:2]
    data = np.rint(array * 255.0).astype(np.uint8)

    raw = bytearray()
    for row in data:
        raw.append(0)  # filter type 0 (None) keeps the decoder trivial
        raw.extend(row.tobytes())

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    blob = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(blob)
    return path


def _png_chunks(blob: bytes) -> tuple[int, int, int, int, int, bytes]:
    """Return ``(width, height, bit_depth, colour_type, interlace, idat)``."""
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos = 8
    header: tuple[int, int, int, int, int] | None = None
    idat = bytearray()
    while pos + 8 <= len(blob):
        (length,) = struct.unpack(">I", blob[pos : pos + 4])
        kind = blob[pos + 4 : pos + 8]
        payload = blob[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bits, colour_type, _comp, _filter, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            header = (width, height, bits, colour_type, interlace)
        elif kind == b"IDAT":
            idat.extend(payload)
        elif kind == b"IEND":
            break
    if header is None:
        raise ValueError("PNG has no IHDR chunk")
    return (*header, bytes(idat))


def _predict(row: np.ndarray, previous: np.ndarray, filter_type: int, channels: int) -> np.ndarray:
    """Reverse one PNG scanline filter (all five defined filters)."""
    line = row.astype(np.int32)
    if filter_type == 0:
        return line.astype(np.uint8)
    if filter_type == 1:  # Sub
        for i in range(channels, line.size):
            line[i] = (line[i] + line[i - channels]) & 0xFF
    elif filter_type == 2:  # Up
        line = (line + previous.astype(np.int32)) & 0xFF
    elif filter_type == 3:  # Average
        for i in range(line.size):
            left = line[i - channels] if i >= channels else 0
            line[i] = (line[i] + ((int(left) + int(previous[i])) >> 1)) & 0xFF
    elif filter_type == 4:  # Paeth
        for i in range(line.size):
            left = int(line[i - channels]) if i >= channels else 0
            up = int(previous[i])
            up_left = int(previous[i - channels]) if i >= channels else 0
            estimate = left + up - up_left
            pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - up_left)
            predictor = left if (pa <= pb and pa <= pc) else (up if pb <= pc else up_left)
            line[i] = (line[i] + predictor) & 0xFF
    else:
        raise ValueError(f"unknown PNG filter type {filter_type}")
    return line.astype(np.uint8)


def read_png(path: str) -> np.ndarray:
    """Read an 8-bit non-interlaced RGB/RGBA/greyscale PNG into an H x W x 3 float array."""
    with open(path, "rb") as handle:
        blob = handle.read()
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG file")
    width, height, bits, colour_type, interlace, idat = _png_chunks(blob)
    if bits != 8 or interlace != 0 or colour_type not in (0, 2, 4, 6):
        raise ValueError(
            f"{path}: only 8-bit, non-interlaced RGB/RGBA/greyscale PNGs are supported "
            f"(bits={bits}, colour_type={colour_type}, interlace={interlace}). "
            "Re-export the image as a plain RGB PNG."
        )
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour_type]
    stride = width * channels
    raw = zlib.decompress(idat)
    out = np.zeros((height, stride), dtype=np.uint8)
    previous = np.zeros(stride, dtype=np.uint8)
    cursor = 0
    for row in range(height):
        filter_type = raw[cursor]
        cursor += 1
        line = np.frombuffer(raw[cursor : cursor + stride], dtype=np.uint8)
        cursor += stride
        out[row] = _predict(line, previous, filter_type, channels)
        previous = out[row]

    pixels = out.reshape(height, width, channels).astype(np.float64) / 255.0
    if channels == 1:
        return np.repeat(pixels, 3, axis=2)
    if channels == 2:
        return np.repeat(pixels[:, :, :1], 3, axis=2)
    return pixels[:, :, :3]


# --------------------------------------------------------------------------- #
# colour
# --------------------------------------------------------------------------- #
def srgb_encode(linear: np.ndarray) -> np.ndarray:
    array = np.clip(np.asarray(linear, dtype=np.float64), 0.0, 1.0)
    return np.where(array <= 0.0031308, 12.92 * array, 1.055 * array ** (1 / 2.4) - 0.055)


def srgb_decode(encoded: np.ndarray) -> np.ndarray:
    array = np.clip(np.asarray(encoded, dtype=np.float64), 0.0, 1.0)
    return np.where(array <= 0.04045, array / 12.92, ((array + 0.055) / 1.055) ** 2.4)


# --------------------------------------------------------------------------- #
# height field: periodic, so every map tiles
# --------------------------------------------------------------------------- #
def weave_height(
    resolution: int,
    cells: int,
    kind: str = "twill",
    seed: int = 7,
    ripstop_block: int = 32,
) -> np.ndarray:
    """Return a tileable ``H x W`` height field in 0..1 for a woven fabric."""
    if kind not in WEAVES:
        raise ValueError(f"unknown weave {kind!r}; expected one of {', '.join(WEAVES)}")
    if cells < 2:
        raise ValueError("cells must be >= 2")
    rng = np.random.default_rng(seed)
    grid = np.arange(resolution, dtype=np.float64) / resolution
    u, v = np.meshgrid(grid, grid, indexing="xy")
    two_pi = 2.0 * np.pi

    if kind == "twill":
        # Warp threads shifted by v produce the characteristic diagonal rib.
        warp = 0.5 + 0.5 * np.cos(two_pi * cells * (u + 0.5 * v))
        weft = 0.5 + 0.5 * np.cos(two_pi * cells * v)
        height = 0.62 * warp + 0.38 * weft
    else:
        warp = 0.5 + 0.5 * np.cos(two_pi * cells * u)
        weft = 0.5 + 0.5 * np.cos(two_pi * cells * v)
        height = 0.5 * np.minimum(warp, weft) + 0.5 * (warp * weft)

    if kind == "ripstop":
        # Reinforcing threads every ``ripstop_block`` pixels, both directions.
        block = float(ripstop_block)
        line_u = np.exp(-((((u * resolution) % block - block / 2) / (block * 0.06)) ** 2))
        line_v = np.exp(-((((v * resolution) % block - block / 2) / (block * 0.06)) ** 2))
        height = 0.72 * height + 0.28 * np.maximum(line_u, line_v)

    # Slubs: irregular yarn thickness. High frequencies keep this reading as
    # texture rather than as banding across a 1 m² garment panel.
    for frequency, weight in ((7, 0.020), (11, 0.014), (17, 0.010)):
        phase = rng.uniform(0, two_pi, size=2)
        height += (
            weight
            * np.sin(two_pi * (frequency * u + phase[0]))
            * np.cos(two_pi * (frequency * v + phase[1]))
        )

    height -= height.min()
    span = height.max() - height.min()
    return height / span if span > 0 else np.zeros_like(height)


def height_from_albedo(rgb: np.ndarray, radius: int = 2) -> np.ndarray:
    """Approximate a height field from a supplied albedo: luminance high-pass."""
    luminance = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    blurred = _box_blur(luminance, radius)
    detail = luminance - blurred
    detail -= detail.min()
    span = detail.max() - detail.min()
    if span <= 1e-9:
        return np.full_like(luminance, 0.5)
    return detail / span


def downsample(array: np.ndarray, target: int) -> np.ndarray:
    """Block-average ``array`` down to ``target`` pixels per side (no resampling lib)."""
    height, width = array.shape[:2]
    if target >= min(height, width):
        return array
    factor = max(1, round(min(height, width) / target))
    block_h = height // factor * factor
    block_w = width // factor * factor
    trimmed = array[:block_h, :block_w]
    shape = (block_h // factor, factor, block_w // factor, factor, *array.shape[2:])
    return trimmed.reshape(shape).mean(axis=(1, 3))


def _box_blur(array: np.ndarray, radius: int) -> np.ndarray:
    """Wrap-around box blur (tiling-safe)."""
    if radius <= 0:
        return array.copy()
    total = np.zeros_like(array)
    count = 2 * radius + 1
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            total += np.roll(np.roll(array, dy, axis=0), dx, axis=1)
    return total / (count * count)


# --------------------------------------------------------------------------- #
# maps from a height field
# --------------------------------------------------------------------------- #
def normal_from_height(height: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Tangent-space normal map (0.5, 0.5, 1.0 is flat) from a height field."""
    dy, dx = np.gradient(height * strength * 8.0)
    nx, ny, nz = -dx, -dy, np.ones_like(height)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    return np.stack(
        [
            np.clip(0.5 + 0.5 * nx / length, 0.0, 1.0),
            np.clip(0.5 + 0.5 * ny / length, 0.0, 1.0),
            np.clip(0.5 + 0.5 * nz / length, 0.0, 1.0),
        ],
        axis=-1,
    )


def roughness_map(height: np.ndarray, base: float = 0.9, variation: float = 0.08) -> np.ndarray:
    value = np.clip(base + variation * (height - height.mean()), 0.0, 1.0)
    return np.repeat(value[:, :, None], 3, axis=2)


def metallic_map(height: np.ndarray, value: float = 0.0) -> np.ndarray:
    return np.full_like(np.repeat(height[:, :, None], 3, axis=2), float(value))


def albedo_from_height(
    height: np.ndarray,
    palette: Tuple[float, float, float],
    encode: str = "linear",
) -> np.ndarray:
    """Albedo: the palette shaded by the weave (threads catch less light).

    ``encode="linear"`` stores the linear values directly, which is what the
    pipeline's internal generator does — the whole outfit set therefore renders
    consistently. ``encode="srgb"`` writes a standards-compliant sRGB map.
    """
    shade = 0.82 + 0.36 * (height - height.mean())
    linear = np.clip(np.asarray(palette, dtype=np.float64)[None, None, :] * shade[:, :, None], 0, 1)
    return srgb_encode(linear) if encode == "srgb" else linear


# --------------------------------------------------------------------------- #
# top level
# --------------------------------------------------------------------------- #
def _relative(path: str) -> str:
    """Repo-relative when the file lives in the repo, absolute otherwise."""
    absolute = os.path.abspath(path)
    if absolute == REPO_ROOT or absolute.startswith(REPO_ROOT + os.sep):
        return os.path.relpath(absolute, REPO_ROOT)
    return absolute


def bake(  # noqa: PLR0913, PLR0917 - one explicit keyword per texture knob
    outfit: str,
    palette: Tuple[float, float, float] = (0.115, 0.125, 0.070),
    weave: str = "twill",
    cells: int = 240,
    resolution: int = 1024,
    roughness: float = 0.9,
    metallic: float = 0.0,
    seed: int = 7,
    output_dir: str | None = None,
    detail_resolution: int = 512,
    source: str | None = None,
    derive_only: bool = False,
    normal_strength: float = 1.0,
    encode: str = "linear",
) -> Dict[str, Any]:
    """Write the four maps (and ``textures.json``) for one outfit."""
    folder = output_dir or os.path.join(TEXTURE_ROOT, outfit)
    os.makedirs(folder, exist_ok=True)
    paths = {slot: os.path.join(folder, f"{slot}.png") for slot in SLOTS}
    mode = "derived" if source else "synthesised"

    if source:
        albedo = read_png(source)
        if derive_only:
            paths["base_color"] = source
        height = height_from_albedo(albedo)
        used_resolution = albedo.shape[0]
        used_weave = "n/a (from supplied base colour)"
        supplied_mean = srgb_decode(albedo.reshape(-1, 3).mean(0))
        provenance_palette = [round(float(v), 4) for v in supplied_mean]
    else:
        height = weave_height(resolution, cells, weave, seed)
        albedo = albedo_from_height(height, palette, encode)
        used_resolution = resolution
        used_weave = weave
        provenance_palette = [round(float(v), 4) for v in palette]

    # Detail maps carry far less information than albedo, and an embedded
    # 1024 normal map alone doubles the GLB. Bake them smaller.
    detail_height = downsample(height, min(detail_resolution, height.shape[0]))

    if not source or not derive_only:
        write_png(paths["base_color"], albedo)
    write_png(paths["normal"], normal_from_height(detail_height, normal_strength))
    write_png(paths["roughness"], roughness_map(detail_height, roughness))
    write_png(paths["metallic"], metallic_map(detail_height, metallic))

    manifest = {
        "outfit": outfit,
        "generator": GENERATOR,
        "mode": mode,
        "weave": used_weave,
        "cells": cells if not source else None,
        "seed": seed if not source else None,
        "resolution": int(used_resolution),
        "detail_resolution": int(detail_height.shape[0]),
        "palette_linear": provenance_palette,
        "roughness": round(float(roughness), 4),
        "metallic": round(float(metallic), 4),
        "normal_strength": round(float(normal_strength), 4),
        "tileable": bool(not source),
        "encode": encode if not source else "as supplied",
        "source": _relative(source) if source else None,
        "slots": {slot: _relative(path) for slot, path in paths.items()},
    }
    with open(os.path.join(folder, "textures.json"), "w") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    manifest["folder"] = folder
    return manifest


def image_stats(array: np.ndarray) -> Dict[str, Any]:
    """Byte-space and decoded statistics, for eyeballing and for the texture gate."""
    flat = np.asarray(array, dtype=np.float64).reshape(-1, 3)
    linear = srgb_decode(flat)
    return {
        "mean": [round(float(v), 4) for v in flat.mean(axis=0)],
        "std": [round(float(v), 4) for v in flat.std(axis=0)],
        "linear_mean": [round(float(v), 4) for v in linear.mean(axis=0)],
        "linear_std": [round(float(v), 4) for v in linear.std(axis=0)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--outfit", required=True, help="outfit id (becomes assets/textures/<outfit>/)"
    )
    parser.add_argument("--out", help="override the output folder")
    parser.add_argument(
        "--palette",
        default="0.115,0.125,0.070",
        help="linear R,G,B (0..1) of the fabric, e.g. 0.30,0.232,0.12 for tan",
    )
    parser.add_argument("--weave", default="twill", choices=WEAVES)
    parser.add_argument("--cells", type=int, default=240, help="thread crossings across the map")
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument(
        "--detail-resolution",
        type=int,
        default=512,
        help="resolution of the normal/roughness/metallic maps (default 512)",
    )
    parser.add_argument("--roughness", type=float, default=0.9)
    parser.add_argument("--metallic", type=float, default=0.0)
    parser.add_argument("--normal-strength", type=float, default=1.0)
    parser.add_argument(
        "--encode",
        default="linear",
        choices=("linear", "srgb"),
        help="how the base colour is stored: pipeline convention (linear) or true sRGB",
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--from", dest="source", help="derive the maps from your own base colour PNG"
    )
    parser.add_argument(
        "--derive-only",
        action="store_true",
        help="keep the supplied base colour file as-is and only rewrite normal/roughness/metallic",
    )
    parser.add_argument("--json", dest="json_path", help="write the provenance manifest here too")
    args = parser.parse_args(argv)

    try:
        palette = tuple(float(part) for part in args.palette.split(","))
    except ValueError:
        parser.error("--palette must be three comma-separated numbers, e.g. 0.115,0.125,0.07")
    if len(palette) != 3:
        parser.error("--palette must have exactly three components (R,G,B)")
    if args.derive_only and not args.source:
        parser.error("--derive-only needs --from <basecolor.png>")

    manifest = bake(
        outfit=args.outfit,
        palette=palette,  # type: ignore[arg-type]
        weave=args.weave,
        cells=args.cells,
        resolution=args.resolution,
        detail_resolution=args.detail_resolution,
        roughness=args.roughness,
        metallic=args.metallic,
        seed=args.seed,
        output_dir=args.out,
        source=args.source,
        derive_only=args.derive_only,
        normal_strength=args.normal_strength,
        encode=args.encode,
    )
    if args.json_path:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_path)), exist_ok=True)
        with open(args.json_path, "w") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")

    print(
        f"[bake_textures] {args.outfit}: {manifest['mode']} "
        f"{manifest['resolution']}px {manifest['weave']}"
    )
    for slot, path in manifest["slots"].items():
        print(f"  {slot:<11} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
