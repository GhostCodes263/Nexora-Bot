from __future__ import annotations

import importlib
import pkgutil


def load_modules() -> None:
    """Import every app/modules/*/commands.py so @command decorators register themselves."""
    import app.modules as pkg

    for info in pkgutil.walk_packages(pkg.__path__, prefix="app.modules."):
        if info.name.endswith(".commands"):
            importlib.import_module(info.name)
