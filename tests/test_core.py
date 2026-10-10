import pytest

from app.bot.loader import load_modules
from app.bot.parse import parse_command
from app.bot.registry import REGISTRY, CommandSpec, Registry
from app.config.settings import normalize_database_url
from app.modules.utility.commands import convert_value, safe_eval
from app.repositories import tenants as tenants_repo
from app.repositories import users as users_repo
from app.services import tenants as tenant_service
from app.services.cache import Cache
from app.services.permissions import can_manage
from app.services.roles import GLOBAL_ASSIGNABLE, TENANT_ASSIGNABLE, Role, parse_role


# --- database / users -------------------------------------------------------
async def test_user_creation_and_update(session):
    u = await users_repo.upsert_user(session, 111, "alice", "Alice")
    assert u.id == 111 and u.username == "alice"
    again = await users_repo.upsert_user(session, 111, "alice2", "Alice")
    assert again.username == "alice2"
    assert await users_repo.count_users(session) == 1


async def test_find_by_username_is_case_insensitive(session):
    await users_repo.upsert_user(session, 5, "Bob", "Bob")
    found = await users_repo.find_by_username(session, "@bob")
    assert found is not None and found.id == 5


async def test_global_ban_and_roles(session):
    await users_repo.upsert_user(session, 7, None, "X")
    await users_repo.set_global_ban(session, 7, True)
    assert (await users_repo.get_user(session, 7)).is_globally_banned
    await users_repo.set_global_role(session, 7, int(Role.DEVELOPER), 1)
    assert await users_repo.get_global_role(session, 7) == int(Role.DEVELOPER)
    assert await users_repo.remove_global_role(session, 7)
    assert await users_repo.get_global_role(session, 7) is None


# --- tenancy ----------------------------------------------------------------
async def test_get_or_create_is_idempotent(session):
    a = await tenants_repo.get_or_create(session, -100, "supergroup", "A")
    b = await tenants_repo.get_or_create(session, -100, "supergroup", "A")
    assert a.id == b.id


async def test_tenant_isolation_of_roles(session):
    a = await tenants_repo.get_or_create(session, -1, "group", "A")
    b = await tenants_repo.get_or_create(session, -2, "group", "B")
    await tenants_repo.set_role(session, a.id, 42, int(Role.MODERATOR), 1)
    assert await tenants_repo.get_role(session, a.id, 42) == int(Role.MODERATOR)
    assert await tenants_repo.get_role(session, b.id, 42) is None
    assert len(await tenants_repo.list_roles(session, b.id)) == 0
    assert not await tenants_repo.remove_role(session, b.id, 42)
    assert await tenants_repo.get_role(session, a.id, 42) is not None


async def test_tenant_isolation_of_settings_and_audit(session):
    a = await tenants_repo.get_or_create(session, -1, "group", "A")
    b = await tenants_repo.get_or_create(session, -2, "group", "B")
    tenants_repo.set_module(a, "utility", False)
    tenants_repo.set_prefix(a, "!")
    await tenants_repo.add_audit(session, "x", 1, a.id, {})
    await session.flush()
    assert b.prefix == "/" and not b.module_overrides
    assert len(await tenants_repo.recent_audit(session, a.id)) == 1
    assert len(await tenants_repo.recent_audit(session, b.id)) == 0


async def test_global_settings_roundtrip(session):
    assert await tenants_repo.get_global(session, "maintenance") == {}
    await tenants_repo.set_global(session, "maintenance", {"on": True})
    assert (await tenants_repo.get_global(session, "maintenance"))["on"] is True


# --- permissions / roles -----------------------------------------------------
def test_role_hierarchy_order():
    order = [Role.USER, Role.TRUSTED, Role.MODERATOR, Role.ADMIN, Role.GROUP_OWNER,
             Role.SUPER_ADMIN, Role.DEVELOPER, Role.OWNER]
    assert order == sorted(order)


def test_can_manage_rules():
    assert can_manage(Role.GROUP_OWNER, Role.USER, Role.ADMIN)
    assert not can_manage(Role.ADMIN, Role.ADMIN)          # peers
    assert not can_manage(Role.ADMIN, Role.USER, Role.ADMIN)  # can't grant own level
    assert not can_manage(Role.MODERATOR, Role.GROUP_OWNER)
    assert can_manage(Role.OWNER, Role.DEVELOPER)


def test_parse_role_limits_what_can_be_granted():
    assert parse_role("mod", TENANT_ASSIGNABLE) == Role.MODERATOR
    assert parse_role("owner", TENANT_ASSIGNABLE) is None
    assert parse_role("group_owner", TENANT_ASSIGNABLE) is None
    assert parse_role("superadmin", GLOBAL_ASSIGNABLE) == Role.SUPER_ADMIN
    assert parse_role("admin", GLOBAL_ASSIGNABLE) is None


# --- parsing -------------------------------------------------------------------
def test_parse_command_variants():
    assert parse_command("/help", "MyBot") == ("help", "")
    assert parse_command("/Help@mybot utility", "MyBot") == ("help", "utility")
    assert parse_command("/help@OtherBot", "MyBot") is None
    assert parse_command("hello", "MyBot") is None
    assert parse_command("!ping", "MyBot", "!") == ("ping", "")
    assert parse_command("!ping", "MyBot", "/") is None
    assert parse_command("/calc 2 + 2", "MyBot") == ("calc", "2 + 2")
    assert parse_command("/bad-name", "MyBot") is None


# --- registry / help discovery ---------------------------------------------------
def test_registry_rejects_duplicates():
    r = Registry()
    spec = CommandSpec(name="a", handler=None, description="d", category="c", aliases=("b",))  # type: ignore[arg-type]
    r.register(spec)
    with pytest.raises(ValueError):
        r.register(CommandSpec(name="b", handler=None, description="d", category="c"))  # type: ignore[arg-type]


def test_all_modules_register_with_metadata():
    load_modules()
    specs = REGISTRY.all()
    assert len(specs) >= 45
    for s in specs:
        assert s.description and s.category and s.usage.startswith("/")
    assert REGISTRY.get("whois") is REGISTRY.get("userinfo")  # alias lookup
    assert any(s.name == "ping" for s in REGISTRY.search("pong")) or REGISTRY.search("ping")


def test_staff_commands_require_staff_roles():
    load_modules()
    assert REGISTRY.get("globalban").permission >= Role.SUPER_ADMIN
    assert REGISTRY.get("maintenance").permission == Role.OWNER
    assert REGISTRY.get("promote").permission >= Role.ADMIN


def test_module_toggle_logic():
    load_modules()
    from app.database.models import Tenant

    t = Tenant(chat_id=1, chat_type="group", module_overrides={}, settings={})
    ping = REGISTRY.get("ping")
    assert tenant_service.is_enabled(t, ping)
    t.module_overrides = {"utility": False}
    assert not tenant_service.is_enabled(t, ping)
    # core/admin can never be switched off
    assert tenant_service.is_enabled(t, REGISTRY.get("help"))
    t.module_overrides = {"admin": False, "core": False}
    assert tenant_service.is_enabled(t, REGISTRY.get("help"))
    assert tenant_service.is_enabled(t, REGISTRY.get("prefix"))


# --- cache: cooldowns and rate limits (in-memory fallback) -------------------------
async def test_cooldown_blocks_second_call():
    c = Cache("")
    assert await c.cooldown("k", 30) == 0
    assert await c.cooldown("k", 30) > 0


async def test_rate_limit_counter():
    c = Cache("")
    counts = [await c.hit("rl", 10) for _ in range(5)]
    assert counts == [1, 2, 3, 4, 5]


async def test_cache_works_when_redis_is_unreachable():
    c = Cache("redis://127.0.0.1:1/0")
    await c.connect()
    assert not c.redis_ok
    await c.set("a", "1", 5)
    assert await c.get("a") == "1"


# --- utilities --------------------------------------------------------------------------
def test_safe_eval_math_and_limits():
    assert safe_eval("2*(3+4)") == 14
    assert safe_eval("2^3") == 8
    assert safe_eval("sqrt(144)") == 12
    for bad in ("__import__('os')", "9**9**9", "open('x')", "1/0", "a+1", "(" * 50):
        with pytest.raises((ValueError, SyntaxError, ZeroDivisionError, RecursionError, MemoryError)):
            safe_eval(bad)


def test_unit_conversion():
    assert convert_value(1, "km", "m") == 1000
    assert round(convert_value(32, "f", "c"), 6) == 0
    assert round(convert_value(0, "c", "k"), 2) == 273.15
    with pytest.raises(ValueError):
        convert_value(1, "kg", "m")


def test_database_url_normalisation():
    assert normalize_database_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert normalize_database_url("postgresql://u:p@h/db?sslmode=require") == "postgresql+asyncpg://u:p@h/db"
    assert normalize_database_url("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"
