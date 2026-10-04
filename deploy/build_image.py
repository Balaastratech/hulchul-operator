"""Build from an allowlisted temporary context inside this worktree; no secrets."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Copy runtime sources only, excluding databases, evidence and bytecode."""
    scratch = ROOT / "deploy" / "scratch"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch, prefix="image-") as temporary:
        context = Path(temporary)
        for name in ("control_plane", "src/operator/contracts"):
            shutil.copytree(ROOT / name, context / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".state", "tests", "spikes"))
        for name in ("Dockerfile", ".env.example", "src/__init__.py", "src/operator/__init__.py",
                     "deploy/requirements-control-plane.txt"):
            target = context / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        subprocess.run(["docker", "build", "-t", "hulchul-control-plane:t033", str(context)], check=True)


if __name__ == "__main__":
    main()
