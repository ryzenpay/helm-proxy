"""Discover charts in a git working tree and build a Helm repository from them.

Auto-detects two layouts:

* **Source charts** — directories containing a ``Chart.yaml`` (packaged with
  ``helm package``). Subcharts nested under a ``charts/`` directory are skipped so they
  are not published as top-level charts.
* **Packaged charts** — ``*.tgz`` files already committed to the repo.

Everything is collected into one output directory and indexed with
``helm repo index --url <base>`` so download URLs point back at the proxy.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

logger = logging.getLogger("helm_proxy.packager")


class PackagingError(RuntimeError):
    """Raised when chart discovery or packaging fails."""


async def _run(args: list[str], *, cwd: Path | None, timeout: int) -> str:
    """Run a helm command, returning stdout; raise on failure."""
    import os

    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
             "HOME": os.environ.get("HOME", "/tmp")},
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise PackagingError(f"helm command timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise PackagingError(
            f"{' '.join(args)} failed: {stderr.decode(errors='replace').strip()}"
        )
    return stdout.decode(errors="replace")


def discover_chart_dirs(root: Path) -> list[Path]:
    """Return top-level chart directories (those with a Chart.yaml).
    A chart nested inside another chart (any ancestor has Chart.yaml) is skipped.
    """
    all_charts = [
        cy.parent
        for cy in root.rglob("Chart.yaml")
        if ".git" not in cy.relative_to(root).parts
    ]
    chart_set = {p.resolve() for p in all_charts}

    top_level: list[Path] = []
    for chart_dir in all_charts:
        ancestor = chart_dir.resolve().parent
        root_resolved = root.resolve()
        nested = False
        while True:
            if ancestor in chart_set:
                nested = True
                break
            if ancestor == root_resolved or ancestor == ancestor.parent:
                break
            ancestor = ancestor.parent
        if not nested:
            top_level.append(chart_dir)
    return sorted(top_level)


def discover_tgz(root: Path) -> list[Path]:
    """Return committed ``*.tgz`` files (excluding anything under .git)."""
    return sorted(
        p for p in root.rglob("*.tgz") if ".git" not in p.relative_to(root).parts
    )


async def build_repository(
    repo_root: Path, output_dir: Path, base_url: str, *, timeout: int
) -> bytes:
    """Package all charts under ``repo_root`` into ``output_dir`` and index them.

    Returns the raw bytes of the generated ``index.yaml``.
    """
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    chart_dirs = discover_chart_dirs(repo_root)
    tgz_files = discover_tgz(repo_root)

    if not chart_dirs and not tgz_files:
        raise PackagingError(
            "no charts found: repository has no Chart.yaml directories or .tgz files"
        )

    packaged = 0
    for chart_dir in chart_dirs:
        # Best-effort dependency build; ignore failure (e.g. no dependencies / offline).
        try:
            await _run(
                ["helm", "dependency", "build", str(chart_dir)],
                cwd=None,
                timeout=timeout,
            )
        except PackagingError as exc:
            logger.debug("dependency build skipped for %s: %s", chart_dir, exc)
        try:
            await _run(
                ["helm", "package", str(chart_dir), "-d", str(output_dir)],
                cwd=None,
                timeout=timeout,
            )
            packaged += 1
        except PackagingError as exc:
            logger.warning("failed to package %s: %s", chart_dir, exc)

    for tgz in tgz_files:
        dest = output_dir / tgz.name
        if not dest.exists():
            shutil.copy2(tgz, dest)

    if not any(output_dir.glob("*.tgz")):
        raise PackagingError("no charts could be packaged from the repository")

    await _run(
        ["helm", "repo", "index", str(output_dir), "--url", base_url],
        cwd=None,
        timeout=timeout,
    )
    index_path = output_dir / "index.yaml"
    if not index_path.exists():
        raise PackagingError("helm repo index did not produce index.yaml")
    logger.info(
        "built repository: %d source chart(s), %d prepackaged, base=%s",
        packaged,
        len(tgz_files),
        base_url,
    )
    return index_path.read_bytes()
