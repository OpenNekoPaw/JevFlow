#!/usr/bin/env python3
"""Export a clean Codex plugin, excluding environments, traces and git history."""
import argparse
import shutil
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("destination", help="A new directory named jevflow")
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
destination = Path(args.destination).expanduser().absolute()
if destination.name != "jevflow" or destination.exists() or destination.is_symlink():
    parser.error("destination must be a new directory named jevflow")
destination.mkdir(parents=True)
for name in (".codex-plugin", "jevflow", "skills", "scripts", "examples", "adapters"):
    shutil.copytree(source / name, destination / name,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules"))
for name in ("pyproject.toml", "README.md", "README.en.md"):
    shutil.copy2(source / name, destination / name)
print(destination)
