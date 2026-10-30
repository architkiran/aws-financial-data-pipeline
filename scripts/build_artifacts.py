"""Build deployment artifacts into build/.

  build/finpipe.zip   the finpipe package, for the Glue job (--extra-py-files)
  build/lambda/       Lambda handlers + the dependency-free finpipe.schema module

Zip entries use a fixed timestamp so the file hash only changes when the code changes,
which keeps `terraform plan` quiet when nothing really changed.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
LAMBDA_FINPIPE_MODULES = ("__init__.py", "schema.py")


def build(root: Path = ROOT) -> Path:
    build_dir = root / "build"
    shutil.rmtree(build_dir, ignore_errors=True)
    lambda_dir = build_dir / "lambda"
    (lambda_dir / "finpipe").mkdir(parents=True)

    for handler in sorted((root / "lambdas").glob("*.py")):
        shutil.copy(handler, lambda_dir / handler.name)
    for module in LAMBDA_FINPIPE_MODULES:
        shutil.copy(root / "src" / "finpipe" / module, lambda_dir / "finpipe" / module)

    archive = build_dir / "finpipe.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((root / "src" / "finpipe").rglob("*.py")):
            info = zipfile.ZipInfo(path.relative_to(root / "src").as_posix(), FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return archive


if __name__ == "__main__":
    out = build()
    print(f"built {out.relative_to(ROOT)} and build/lambda/")
