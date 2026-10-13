"""Build the Windows source bundle without credentials, caches or environments."""

import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "README.md", ".gitignore", ".gitattributes", "app.py", "pyproject.toml",
    "requirements.lock", "setup_windows.cmd", "start_windows.cmd",
)
DIRECTORIES = ("quant_trading", "tests", "docs", ".github", "scripts")
SUFFIXES = {".py", ".md", ".yml", ".yaml"}


def build() -> Path:
    sources = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        sources.extend(path for path in (ROOT / directory).rglob("*")
                       if path.is_file() and path.suffix in SUFFIXES
                       and "__pycache__" not in path.parts)
    output = ROOT / "downloads" / "quant-trading-windows.zip"
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(set(sources)):
            if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
                raise ValueError(f"Unsafe source path: {source.name}")
            data = source.read_bytes()
            if source.suffix == ".cmd":
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            name = "quant-trading/" + source.relative_to(ROOT).as_posix()
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compresslevel=9)
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive integrity check failed")
        print(f"Windows source bundle: {len(archive.namelist())} files")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(
        f"{digest}  {output.name}\n", encoding="ascii")
    print(f"SHA256: {digest}")
    return output


if __name__ == "__main__":
    build()
