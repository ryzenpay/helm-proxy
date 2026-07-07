"""Tests for chart discovery and (if helm is present) repository building."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from app.packager import build_repository, discover_chart_dirs, discover_tgz

HELM = shutil.which("helm")

# The repo's own Helm chart, packaged and served by these tests.
CHARTS_DIR = Path(__file__).resolve().parents[1] / "charts"
LOCAL_CHART = CHARTS_DIR / "helm-proxy"


def _chart_meta(chart_dir: Path) -> tuple[str, str]:
    """Return (name, version) parsed from a chart's Chart.yaml."""
    name = version = ""
    for line in (chart_dir / "Chart.yaml").read_text().splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("version:"):
            version = line.split(":", 1)[1].strip().strip("'\"")
    return name, version


def _make_chart(root: Path, name: str, version: str) -> None:
    d = root / "charts" / name
    d.mkdir(parents=True)
    (d / "Chart.yaml").write_text(
        f"apiVersion: v2\nname: {name}\nversion: {version}\n"
    )
    (d / "values.yaml").write_text("replicas: 1\n")
    tpl = d / "templates"
    tpl.mkdir()
    (tpl / "cm.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: "
        f"{name}\n"
    )


def test_discover_skips_subcharts(tmp_path: Path):
    _make_chart(tmp_path, "parent", "1.0.0")
    # A nested subchart under the parent's charts/ dir must be ignored.
    sub = tmp_path / "charts" / "parent" / "charts" / "child"
    sub.mkdir(parents=True)
    (sub / "Chart.yaml").write_text("apiVersion: v2\nname: child\nversion: 0.1.0\n")

    found = discover_chart_dirs(tmp_path)
    names = {p.name for p in found}
    assert "parent" in names
    assert "child" not in names


def test_discover_tgz(tmp_path: Path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "redis-1.2.3.tgz").write_bytes(b"fake")
    tgz = discover_tgz(tmp_path)
    assert [p.name for p in tgz] == ["redis-1.2.3.tgz"]


def test_discovers_local_chart():
    """The repo's own charts/helm-proxy chart is found as a top-level chart."""
    found = {p.resolve() for p in discover_chart_dirs(CHARTS_DIR)}
    assert LOCAL_CHART.resolve() in found


@pytest.mark.skipif(HELM is None, reason="helm CLI not installed")
def test_build_repository_from_local_chart(tmp_path: Path):
    """Package and index the local charts/helm-proxy chart end-to-end."""
    name, version = _chart_meta(LOCAL_CHART)
    out = tmp_path / "out"

    index = asyncio.run(
        build_repository(CHARTS_DIR, out, "https://proxy/git/x@main", timeout=60)
    )
    text = index.decode()
    assert name in text
    assert f"https://proxy/git/x@main/{name}-{version}.tgz" in text
    assert (out / f"{name}-{version}.tgz").is_file()
