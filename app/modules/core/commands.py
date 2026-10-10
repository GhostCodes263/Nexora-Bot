from __future__ import annotations

from app.bot import categories, ui
from app.bot.context import Ctx
from app.bot.registry import REGISTRY, command
from app.services import platform
from app.services.roles import Role

CAT = "core"


@command("start", description="Start the bot and open the menu", category=CAT)
async def start(ctx: Ctx) -> None:
    if ctx.in_group:
        await ctx.reply("👋 I'm here! Use /menu for buttons or /help to browse commands.")
        return
    text, kb = ui.home_view(ctx.role, None, False)
    await ctx.reply(f"👋 <b>Welcome, {ctx.user.first_name or 'friend'}!</b>\n\n{text}", kb)


@command("menu", description="Open the interactive menu", category=CAT, aliases=("home",))
async def menu(ctx: Ctx) -> None:
    text, kb = ui.home_view(ctx.role, ctx.tenant, ctx.in_group)
    await ctx.reply(text, kb)


@command(
    "help", description="Browse or search all commands", category=CAT,
    usage="/help [category|command|search words]",
    examples=("/help", "/help utility", "/help time"), aliases=("commands",),
)
async def help_cmd(ctx: Ctx) -> None:
    query = ctx.raw_args.strip().lstrip("/")
    if not query:
        text, kb = ui.home_view(ctx.role, ctx.tenant, ctx.in_group)
    else:
        key = query.lower()
        by_title = {categories.info(c)[1].lower(): c for c in REGISTRY.by_category()}
        category = key if key in REGISTRY.by_category() else by_title.get(key)
        spec = REGISTRY.get(key)
        if category:
            text, kb = ui.category_view(category, 0, ctx.role, ctx.tenant, ctx.in_group)
        elif spec:
            text, kb = ui.command_view(spec.name, ctx.role, ctx.tenant, ctx.in_group)
        else:
            text, kb = ui.search_view(query, ctx.role, ctx.tenant, ctx.in_group)
    await ctx.reply(text, kb)


@command(
    "settings", description="Open this group's settings", category=CAT,
    permission=Role.ADMIN, scope="group",
)
async def settings_cmd(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    text, kb = ui.settings_view(ctx.tenant)
    await ctx.reply(text, kb)


@command("about", description="About this bot", category=CAT)
async def about(ctx: Ctx) -> None:
    n = len(REGISTRY.all())
    await ctx.reply(
        f"<b>🤖 Telegram Bot Platform</b> v{platform.VERSION}\n"
        f"{n} commands · multi-tenant · button-driven\nUptime: {platform.uptime()}"
    )


@command("privacy", description="What data the bot stores about you", category=CAT)
async def privacy(ctx: Ctx) -> None:
    await ctx.reply(
        "<b>🔒 Privacy</b>\n"
        "I store only what features need:\n"
        "• Your Telegram ID, username and display name\n"
        "• Roles granted to you in groups\n"
        "• Group settings and an audit log of admin actions\n\n"
        "Use /deletemydata to clear your name/username and your roles."
    )


@command(
    "deletemydata", description="Clear your stored name, username and roles", category=CAT,
    scope="private",
)
async def deletemydata(ctx: Ctx) -> None:
    text, kb = ui.confirm_forget_view()
    await ctx.reply(text, kb)
