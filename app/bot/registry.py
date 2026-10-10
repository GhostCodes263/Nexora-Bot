from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.roles import Role

if TYPE_CHECKING:
    from app.bot.context import Ctx

Handler = Callable[["Ctx"], Awaitable[None]]
_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")


@dataclass(frozen=True)
class CommandSpec:
    name: str
    handler: Handler
    description: str
    category: str
    usage: str = ""
    aliases: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    permission: Role = Role.USER
    cooldown: int = 0
    enabled_by_default: bool = True
    scope: str = "any"  # any | group | private
    hidden: bool = False


class Registry:
    def __init__(self) -> None:
        self._by_name: dict[str, CommandSpec] = {}
        self._lookup: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        for n in (spec.name, *spec.aliases):
            if not _NAME_RE.match(n):
                raise ValueError(f"Invalid command name: {n!r}")
            if n in self._lookup:
                raise ValueError(f"Duplicate command or alias: {n!r}")
        self._by_name[spec.name] = spec
        for n in (spec.name, *spec.aliases):
            self._lookup[n] = spec

    def get(self, name: str) -> CommandSpec | None:
        return self._lookup.get(name.lower())

    def all(self) -> list[CommandSpec]:
        return sorted(self._by_name.values(), key=lambda s: (s.category, s.name))

    def by_category(self) -> dict[str, list[CommandSpec]]:
        out: dict[str, list[CommandSpec]] = {}
        for s in self.all():
            out.setdefault(s.category, []).append(s)
        return out

    def search(self, query: str) -> list[CommandSpec]:
        q = query.lower().strip().lstrip("/")
        if not q:
            return []
        hits = []
        for s in self.all():
            hay = [s.name, *s.aliases]
            if any(q in h for h in hay):
                hits.append((0, s))
            elif q in s.description.lower():
                hits.append((1, s))
        return [s for _, s in sorted(hits, key=lambda t: (t[0], t[1].name))]

    def clear(self) -> None:  # used by tests
        self._by_name.clear()
        self._lookup.clear()


REGISTRY = Registry()


def command(
    name: str,
    *,
    description: str,
    category: str,
    usage: str = "",
    aliases: tuple[str, ...] = (),
    examples: tuple[str, ...] = (),
    permission: Role = Role.USER,
    cooldown: int = 0,
    enabled_by_default: bool = True,
    scope: str = "any",
    hidden: bool = False,
) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        REGISTRY.register(
            CommandSpec(
                name=name, handler=fn, description=description, category=category,
                usage=usage or f"/{name}", aliases=aliases, examples=examples,
                permission=permission, cooldown=cooldown, enabled_by_default=enabled_by_default,
                scope=scope, hidden=hidden,
            )
        )
        return fn

    return deco
