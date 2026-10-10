from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditLog, GlobalSetting, Tenant, TenantRole


async def get_by_chat_id(session: AsyncSession, chat_id: int) -> Tenant | None:
    res = await session.execute(select(Tenant).where(Tenant.chat_id == chat_id))
    return res.scalars().first()


async def get_or_create(
    session: AsyncSession, chat_id: int, chat_type: str, title: str, added_by: int | None = None
) -> Tenant:
    tenant = await get_by_chat_id(session, chat_id)
    if tenant is not None:
        if title and tenant.title != title:
            tenant.title = title
        return tenant
    tenant = Tenant(
        chat_id=chat_id, chat_type=chat_type, title=title, added_by=added_by,
        module_overrides={}, settings={},
    )
    try:
        async with session.begin_nested():
            session.add(tenant)
            await session.flush()
    except IntegrityError:
        tenant = await get_by_chat_id(session, chat_id)
        assert tenant is not None
    return tenant


async def list_tenants(session: AsyncSession, offset: int = 0, limit: int = 8) -> list[Tenant]:
    res = await session.execute(select(Tenant).order_by(Tenant.id).offset(offset).limit(limit))
    return list(res.scalars())


async def count_tenants(session: AsyncSession, chat_types: tuple[str, ...] | None = None) -> int:
    q = select(func.count()).select_from(Tenant)
    if chat_types:
        q = q.where(Tenant.chat_type.in_(chat_types))
    return int((await session.execute(q)).scalar_one())


def set_module(tenant: Tenant, module: str, enabled: bool) -> None:
    # Reassign (not mutate) so SQLAlchemy notices the JSON change.
    tenant.module_overrides = {**(tenant.module_overrides or {}), module: enabled}


def set_prefix(tenant: Tenant, prefix: str) -> None:
    tenant.prefix = prefix


# --- tenant-scoped roles: every function REQUIRES tenant_id -----------------

async def get_role(session: AsyncSession, tenant_id: int, user_id: int) -> int | None:
    res = await session.execute(
        select(TenantRole.role).where(TenantRole.tenant_id == tenant_id, TenantRole.user_id == user_id)
    )
    return res.scalars().first()


async def set_role(session: AsyncSession, tenant_id: int, user_id: int, role: int, granted_by: int) -> None:
    res = await session.execute(
        select(TenantRole).where(TenantRole.tenant_id == tenant_id, TenantRole.user_id == user_id)
    )
    row = res.scalars().first()
    if row is None:
        session.add(TenantRole(tenant_id=tenant_id, user_id=user_id, role=role, granted_by=granted_by))
    else:
        row.role = role
        row.granted_by = granted_by


async def remove_role(session: AsyncSession, tenant_id: int, user_id: int) -> bool:
    res = await session.execute(
        select(TenantRole).where(TenantRole.tenant_id == tenant_id, TenantRole.user_id == user_id)
    )
    row = res.scalars().first()
    if row is None:
        return False
    await session.delete(row)
    return True


async def list_roles(session: AsyncSession, tenant_id: int) -> list[TenantRole]:
    res = await session.execute(
        select(TenantRole).where(TenantRole.tenant_id == tenant_id).order_by(TenantRole.role.desc())
    )
    return list(res.scalars())


# --- audit -----------------------------------------------------------------

async def add_audit(
    session: AsyncSession, action: str, actor_id: int | None, tenant_id: int | None = None,
    details: dict | None = None,
) -> None:
    session.add(AuditLog(action=action, actor_id=actor_id, tenant_id=tenant_id, details=details or {}))


async def recent_audit(session: AsyncSession, tenant_id: int | None, limit: int = 10) -> list[AuditLog]:
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if tenant_id is not None:
        q = q.where(AuditLog.tenant_id == tenant_id)
    return list((await session.execute(q)).scalars())


# --- global settings ---------------------------------------------------------

async def get_global(session: AsyncSession, key: str, default: dict | None = None) -> dict:
    row = await session.get(GlobalSetting, key)
    return row.value if row else (default or {})


async def set_global(session: AsyncSession, key: str, value: dict) -> None:
    row = await session.get(GlobalSetting, key)
    if row is None:
        session.add(GlobalSetting(key=key, value=value))
    else:
        row.value = value
