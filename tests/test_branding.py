from pathlib import Path

import pytest

from app.bot import ui
from app.bot.loader import load_modules
from app.bot.registry import REGISTRY
from app.database.models import Plan
from app.repositories import tenants as tenants_repo
from app.services import banners, style
from app.services.cache import Cache
from app.services.roles import Role


@pytest.fixture
async def seeded(session):
    session.add_all([
        Plan(code="free", name="Free", features=["moderation_basic"], limits={}, sort=0),
        Plan(code="pro", name="Pro", price_month_cents=899, features=["moderation_basic", "branding"], limits={}, sort=2),
    ])
    await session.flush()
    return session


async def _tenant(session, chat_id=-1):
    return await tenants_repo.get_or_create(session, chat_id, "supergroup", "T")


def test_every_screen_has_a_built_in_banner_image():
    assets = Path(__file__).resolve().parent.parent / "assets" / "banners"
    for key in banners.KEYS:
        assert (assets / f"{key}.jpg").exists(), key


def test_generator_and_bot_agree_on_screen_names():
    import importlib.util

    spec = importlib.util.spec_from_file_location("mb", Path(__file__).resolve().parent.parent / "scripts" / "make_banners.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(mod.SPECS) == set(banners.KEYS)


def test_themes_decorate_headers():
    assert "🌸" in style.header(style.THEMES["kawaii"], "🏠", "Main menu")
    assert style.header(style.THEMES["minimal"], "🏠", "Main menu") == "<b>🏠 Main menu</b>"
    assert style.theme_of(None).name == "classic"


async def test_theme_is_per_group_and_shows_in_menus(session):
    a, b = await _tenant(session, -1), await _tenant(session, -2)
    a.settings = {"theme": "cyber"}
    load_modules()
    text_a, _ = ui.home_view(Role.USER, a, True)
    text_b, _ = ui.home_view(Role.USER, b, True)
    assert "⚡" in text_a and "⚡" not in text_b


async def test_banner_priority_and_plan_gate(seeded):
    cache = Cache("")
    t = await _tenant(seeded)
    await banners.set_global(seeded, cache, "menu", "BOT_MENU")
    await banners.set_global(seeded, cache, "games", "BOT_GAMES")
    assert await banners.resolve(seeded, cache, t, "games") == "BOT_GAMES"
    assert await banners.resolve(seeded, cache, t, "vip") == "BOT_MENU"          # falls back to the menu banner
    banners.set_tenant(t, "menu", "GROUP_MENU")
    assert await banners.resolve(seeded, cache, t, "games") == "BOT_GAMES"        # Free plan: group art is ignored
    from app.database.models import Rental
    from app.utils.time import utcnow
    from datetime import timedelta

    seeded.add(Rental(tenant_id=t.id, customer_id=5, plan_code="pro", status="active", expires_at=utcnow() + timedelta(days=5)))
    await seeded.flush()
    await cache.delete(f"plan:{t.id}")
    assert await banners.resolve(seeded, cache, t, "games") == "GROUP_MENU"       # Pro: the group's own art wins
    other = await _tenant(seeded, -2)
    assert await banners.resolve(seeded, cache, other, "games") == "BOT_GAMES"    # other groups unaffected


async def test_delete_banners(seeded):
    cache = Cache("")
    await banners.set_global(seeded, cache, "menu", "X")
    assert await banners.delete_global(seeded, cache, "menu")
    assert not await banners.delete_global(seeded, cache, "menu")
    assert await banners.resolve(seeded, cache, None, "menu") is None
    t = await _tenant(seeded)
    assert banners.set_tenant(t, "menu", "Y") and banners.set_tenant(t, "menu", None) and not banners.set_tenant(t, "menu", None)


def test_branding_commands_registered_and_gated():
    load_modules()
    for name in ("setbanner", "delbanner", "banners", "uploadbanners", "theme"):
        assert REGISTRY.get(name) is not None, name
    assert REGISTRY.get("uploadbanners").permission == Role.OWNER
    assert REGISTRY.get("setbanner").permission >= Role.ADMIN
