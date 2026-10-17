"""The image copies its modules one by one, so a new module has to be named
in the Dockerfile too. Forgotten, the container dies on the import at start
while every test here still passes against the source tree.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _copied_modules() -> set[str]:
    copied = set()
    for line in (ROOT / "Dockerfile").read_text().splitlines():
        m = re.match(r"\s*COPY\s+(.+?)\s+\S+\s*$", line)
        if m:
            copied.update(p for p in m.group(1).split() if p.endswith(".py"))
    return copied


def _local_imports(module: str, seen: set[str]) -> None:
    if module in seen:
        return
    seen.add(module)
    tree = ast.parse((ROOT / module).read_text())
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            local = name.split(".")[0] + ".py"
            if (ROOT / local).is_file():
                _local_imports(local, seen)


def test_every_module_the_app_imports_is_in_the_image():
    needed: set[str] = set()
    _local_imports("app.py", needed)
    missing = needed - _copied_modules()
    assert not missing, f"Dockerfile does not COPY {sorted(missing)}"
