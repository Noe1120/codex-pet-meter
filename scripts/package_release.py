"""Build an explicit allowlisted source release; exclude all local data."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.0.0-beta.3"


def package():
    files = [ROOT / name for name in ("README.md", ".gitignore")]
    # User guide only; maintainer upload instructions stay outside the release.
    files.append(ROOT / "docs/其他中转站接入.md")
    # Reviewed, public README screenshots only; do not include raw captures.
    files.extend(ROOT / "docs/images" / name for name in (
        "pet-bubble.png", "panel-tokens.png", "panel-cost-tooltip.png", "panel-models.png"))
    for directory in (".agents", "plugins", "tests", "scripts"):
        for path in (ROOT / directory).rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".json", ".md", ".yaml", ".html", ".cjs"):
                continue
            relative = path.relative_to(ROOT)
            if any(part in (".local", ".research", "__pycache__") for part in relative.parts):
                continue
            files.append(path)
    # Only the explicitly simulated preview is publishable.
    preview = ROOT / "previews/demo.html"
    if preview.exists():
        files.append(preview)
    output = ROOT / "releases" / f"codex-usage-pet-{VERSION}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(f"{output} ({len(set(files))} files)")
    return output


if __name__ == "__main__":
    package()
