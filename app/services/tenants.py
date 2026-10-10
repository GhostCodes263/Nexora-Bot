from __future__ import annotations

from app.bot import categories
from app.bot.registry import CommandSpec
from app.database.models import Tenant


def is_enabled(tenant: Tenant | None, spec: CommandSpec) -> bool:
    if tenant is None:
        return True
    _, _, can_toggle = categories.info(spec.category)
    if not can_toggle:
        return True
    override = (tenant.module_overrides or {}).get(spec.category)
    if override is not None:
        return bool(override)
    return spec.enabled_by_default


def module_enabled(tenant: Tenant, category: str) -> bool:
    override = (tenant.module_overrides or {}).get(category)
    return True if override is None else bool(override)
