"""Unit tests for ``tools/build_site.py`` — the Pages site/manifest builder.

The web studio is driven entirely by the generated ``manifest.json``, so these
tests pin down the contract the frontend relies on:

* only files that exist are listed, with their real byte sizes;
* every path is relative (GitHub project pages serve from ``/<repo>/``);
* an outfit may exist for one character and not the other;
* validation status and stage verdicts come from the pipeline's reports;
* a site is refused when the manifest would point at missing files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import build_site  # noqa: E402  (path bootstrap above)


def _write(path: Path, payload: str | dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, dict):
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path.write_text(payload, encoding="utf-8")
    return path


def _report(status: str = "pass", *, pipeline_version: str = "2.0.0", stages=None) -> dict:
    return {
        "outfit": "jacket_urban_01",
        "variant": "male",
        "status": status,
        "stages": stages
        or [
            {"stage": "shell", "status": "pass", "error": None},
            {"stage": "export", "status": "pass", "error": None},
        ],
        "pipeline_version": pipeline_version,
    }


def make_tree(tmp_path: Path, *, variants=("male", "female"), with_fbx=True) -> Path:
    """Create a miniature ``outputs/`` + ``assets/`` tree."""
    outputs = tmp_path / "outputs"
    for variant in variants:
        folder = outputs / variant / "jacket_urban_01"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "jacket_urban_01_Modular.glb").write_bytes(b"glTF" + b"\x00" * 1024)
        if with_fbx:
            (folder / "jacket_urban_01_Modular.fbx").write_bytes(b"Kaydara" + b"\x00" * 512)
        (folder / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 256)
        _write(folder / "validation_report.json", _report())

    assets = tmp_path / "assets"
    _write(
        assets / "outfits" / "outfits.json",
        {
            "outfits": {
                "jacket_urban_01": {
                    "display_name": "Urban Jacket 01",
                    "region": "UPPER_BODY",
                    "garment_type": "jacket",
                }
            }
        },
    )
    _write(
        assets / "base_body" / "manifest.json",
        {
            "default_variant": "male",
            "variants": {
                "male": {"file": "base_body_Male.blend", "body_object": "SuperHero_Male"},
                "female": {"file": "base_body_Female.blend", "body_object": "Superhero_Female"},
            },
        },
    )
    return outputs


def make_web(tmp_path: Path) -> Path:
    web = tmp_path / "web_viewer"
    _write(web / "index.html", "<!doctype html><title>studio</title>")
    _write(web / "css" / "styles.css", "body{background:#000}")
    _write(web / "js" / "app.js", "export const ok = true;")
    _write(web / "vendor" / "model-viewer.min.js", "/* vendored */")
    _write(web / "favicon.svg", "<svg/>")
    return web


@pytest.mark.unit
def test_manifest_lists_every_existing_export(tmp_path: Path) -> None:
    outputs = make_tree(tmp_path)
    manifest = build_site.discover(str(outputs), str(tmp_path / "assets"))

    assert [character["id"] for character in manifest["characters"]] == ["female", "male"]
    assert manifest["default_variant"] == "male"
    assert manifest["pipeline_version"] == "2.0.0"

    outfit = manifest["outfits"][0]
    assert outfit["id"] == "jacket_urban_01"
    assert outfit["name"] == "Urban Jacket 01"
    assert outfit["kind"] == "jacket"
    assert outfit["compatibility"] == ["female", "male"]

    entry = outfit["variants"]["male"]
    assert entry["glb"]["path"] == "outputs/male/jacket_urban_01/jacket_urban_01_Modular.glb"
    assert entry["glb"]["bytes"] == 1028
    assert entry["fbx"]["path"].endswith(".fbx")
    assert entry["preview"]["path"].endswith("preview.png")
    assert entry["validation"]["status"] == "pass"
    assert [stage["stage"] for stage in entry["validation"]["stages"]] == ["shell", "export"]


@pytest.mark.unit
def test_paths_are_relative_for_project_pages(tmp_path: Path) -> None:
    outputs = make_tree(tmp_path)
    manifest = build_site.discover(str(outputs), str(tmp_path / "assets"))
    for where, path in build_site.manifest_paths(manifest):
        assert not path.startswith("/"), f"{where} is root-relative: {path}"
        assert "://" not in path, f"{where} is an absolute URL: {path}"
        assert not Path(path).is_absolute()
        assert ".." not in path.split("/")


@pytest.mark.unit
def test_missing_character_is_reported_as_compatibility_gap(tmp_path: Path) -> None:
    outputs = make_tree(tmp_path, variants=("male",))
    manifest = build_site.discover(str(outputs), str(tmp_path / "assets"))
    outfit = manifest["outfits"][0]
    assert outfit["compatibility"] == ["male"]
    assert "female" not in outfit["variants"]
    assert [character["id"] for character in manifest["characters"]] == ["male"]


@pytest.mark.unit
def test_fbx_is_optional_but_never_invented(tmp_path: Path) -> None:
    outputs = make_tree(tmp_path, with_fbx=False)
    manifest = build_site.discover(str(outputs), str(tmp_path / "assets"))
    entry = manifest["outfits"][0]["variants"]["male"]
    assert entry["glb"] is not None
    assert entry["fbx"] is None


@pytest.mark.unit
def test_failed_validation_is_surfaced_verbatim(tmp_path: Path) -> None:
    outputs = make_tree(tmp_path)
    folder = outputs / "male" / "jacket_urban_01"
    _write(
        folder / "validation_report.json",
        _report(
            status="fail",
            stages=[{"stage": "shrinkwrap", "status": "fail", "error": "bad fit"}],
        ),
    )
    manifest = build_site.discover(str(outputs), str(tmp_path / "assets"))
    entry = manifest["outfits"][0]["variants"]["male"]
    assert entry["validation"]["status"] == "fail"
    assert entry["validation"]["stages"][0]["error"] == "bad fit"


@pytest.mark.unit
def test_directories_without_exports_are_ignored(tmp_path: Path) -> None:
    outputs = make_tree(tmp_path)
    (outputs / "male" / "empty_outfit").mkdir(parents=True)
    manifest = build_site.discover(str(outputs), str(tmp_path / "assets"))
    assert [outfit["id"] for outfit in manifest["outfits"]] == ["jacket_urban_01"]


@pytest.mark.unit
def test_build_copies_frontend_and_assets(tmp_path: Path) -> None:
    outputs = make_tree(tmp_path)
    web = make_web(tmp_path)
    site = tmp_path / "docs"

    manifest = build_site.build(
        str(site),
        web_root=str(web),
        outputs_root=str(outputs),
        assets_root=str(tmp_path / "assets"),
    )

    for relative in (
        "index.html",
        "manifest.json",
        "css/styles.css",
        "js/app.js",
        "vendor/model-viewer.min.js",
        "favicon.svg",
        "outputs/male/jacket_urban_01/jacket_urban_01_Modular.glb",
        "outputs/female/jacket_urban_01/preview.png",
        "outputs/male/jacket_urban_01/validation_report.json",
    ):
        assert (site / relative).is_file(), f"{relative} was not assembled"

    written = json.loads((site / "manifest.json").read_text(encoding="utf-8"))
    assert (
        written["outfits"][0]["variants"]["male"]["glb"]["bytes"]
        == manifest["outfits"][0]["variants"]["male"]["glb"]["bytes"]
    )


@pytest.mark.unit
def test_build_refuses_an_empty_export_tree(tmp_path: Path) -> None:
    web = make_web(tmp_path)
    (tmp_path / "assets").mkdir()
    with pytest.raises(build_site.SiteError, match="no exports"):
        build_site.build(
            str(tmp_path / "docs"),
            web_root=str(web),
            outputs_root=str(tmp_path / "outputs"),
            assets_root=str(tmp_path / "assets"),
        )


@pytest.mark.unit
def test_validation_rejects_root_relative_and_missing_paths(tmp_path: Path) -> None:
    site = tmp_path / "site"
    (site / "outputs").mkdir(parents=True)
    (site / "outputs" / "a.glb").write_bytes(b"glb")

    manifest = {
        "characters": [{"id": "male"}],
        "outfits": [
            {
                "id": "o",
                "variants": {
                    "male": {
                        "glb": {"path": "/outputs/a.glb"},
                        "fbx": {"path": "outputs/missing.fbx"},
                    }
                },
            }
        ],
    }
    problems = build_site.validate_manifest(manifest, str(site))
    joined = "\n".join(problems)
    assert "root-relative" in joined
    assert "does not exist" in joined


@pytest.mark.unit
def test_corrupt_report_does_not_break_the_build(tmp_path: Path) -> None:
    outputs = make_tree(tmp_path)
    (outputs / "male" / "jacket_urban_01" / "validation_report.json").write_text("{not json")
    manifest = build_site.discover(str(outputs), str(tmp_path / "assets"))
    validation = manifest["outfits"][0]["variants"]["male"]["validation"]
    assert validation["status"].startswith("unreadable")
    assert validation["stages"] == []


@pytest.mark.unit
def test_check_only_mode_validates_an_existing_site(tmp_path: Path, capsys) -> None:
    outputs = make_tree(tmp_path)
    web = make_web(tmp_path)
    site = tmp_path / "docs"
    assert (
        build_site.main(
            [
                "--out",
                str(site),
                "--web-root",
                str(web),
                "--outputs-root",
                str(outputs),
                "--assets-root",
                str(tmp_path / "assets"),
            ]
        )
        == 0
    )

    assert build_site.main(["--out", str(site), "--check-only"]) == 0

    # removing an asset the manifest points at must fail the check
    (site / "outputs" / "male" / "jacket_urban_01" / "jacket_urban_01_Modular.glb").unlink()
    assert build_site.main(["--out", str(site), "--check-only"]) == 1
    assert "does not exist" in capsys.readouterr().err
