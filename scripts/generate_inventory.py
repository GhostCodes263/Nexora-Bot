"""Generate COMMANDS.md by reading the source (no dependencies needed).

Run:  python scripts/generate_inventory.py
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "app" / "modules"


def _const(node: ast.AST, names: dict[str, str]) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Tuple):
        return tuple(_const(e, names) for e in node.elts)
    return None


def collect() -> list[dict]:
    rows = []
    for path in sorted(MODULES.glob("*/commands.py")):
        tree = ast.parse(path.read_text())
        names = {
            t.id: n.value.value
            for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
            for t in n.targets if isinstance(t, ast.Name)
        }
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for dec in fn.decorator_list:
                if isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "command":
                    kw = {k.arg: _const(k.value, names) for k in dec.keywords}
                    rows.append({
                        "name": _const(dec.args[0], names),
                        "category": kw.get("category"),
                        "description": kw.get("description", ""),
                        "permission": kw.get("permission", "USER"),
                        "aliases": kw.get("aliases") or (),
                        "cooldown": kw.get("cooldown", 0),
                    })
    return rows


def main() -> None:
    rows = collect()
    out = ["# Command inventory", "", f"**{len(rows)} commands** (aliases not counted).", ""]
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(str(r["category"]), []).append(r)
    for cat, items in by_cat.items():
        out += [f"## {cat} ({len(items)})", "", "| Command | Description | Permission | Aliases | Cooldown |", "|---|---|---|---|---|"]
        for r in sorted(items, key=lambda x: x["name"]):
            aliases = ", ".join(f"/{a}" for a in r["aliases"]) or "—"
            perm = str(r["permission"]).replace("_", " ").title()
            out.append(f"| /{r['name']} | {r['description']} | {perm} | {aliases} | {r['cooldown']}s |")
        out.append("")
    (ROOT / "COMMANDS.md").write_text("\n".join(out))
    print(f"Wrote COMMANDS.md with {len(rows)} commands")


if __name__ == "__main__":
    main()
