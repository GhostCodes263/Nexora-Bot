from __future__ import annotations

from pathlib import Path

from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile

from app.bot.context import Ctx
from app.bot.registry import command
from app.repositories import tenants as tenants_repo
from app.services import banners
from app.services.gates import require_feature
from app.services.roles import Role
from app.services.style import THEMES, theme_of

CAT = "admin"
ASSETS = Path(__file__).resolve().parents[3] / "assets" / "banners"
KEY_HELP = ", ".join(f"<code>{k}</code>" for k in banners.KEYS)


def _photo_id(ctx: Ctx) -> str | None:
    r = ctx.message.reply_to_message
    return r.photo[-1].file_id if r and r.photo else None


@command("setbanner", description="Set the banner image for a screen (reply to a photo)", category=CAT,
         usage="/setbanner <screen>  (reply to a photo)", examples=("/setbanner menu",), permission=Role.ADMIN)
async def setbanner(ctx: Ctx) -> None:
    key = ctx.args[0].lower() if ctx.args else "menu"
    if key not in banners.KEYS:
        await ctx.reply(f"Choose a screen: {KEY_HELP}")
        return
    photo = _photo_id(ctx)
    if photo is None:
        await ctx.reply("Reply to a photo with <code>/setbanner menu</code>. Landscape images (2:1) look best.")
        return
    if ctx.tenant is None:
        if ctx.role < Role.OWNER:
            return
        await banners.set_global(ctx.session, ctx.cache, key, photo)
        await tenants_repo.add_audit(ctx.session, "banner_set", ctx.user_id, None, {"key": key})
        await ctx.reply(f"✅ The bot-wide <b>{key}</b> banner is set.", banner=key)
        return
    if not await require_feature(ctx, "branding"):
        return
    banners.set_tenant(ctx.tenant, key, photo)
    await tenants_repo.add_audit(ctx.session, "banner_set", ctx.user_id, ctx.tenant.id, {"key": key})
    await ctx.reply(f"✅ This group's <b>{key}</b> banner is set. (Setting <code>menu</code> covers every screen that has no banner of its own.)", banner=key)


@command("delbanner", description="Remove a banner and fall back to the default", category=CAT,
         usage="/delbanner <screen>", permission=Role.ADMIN)
async def delbanner(ctx: Ctx) -> None:
    key = ctx.args[0].lower() if ctx.args else ""
    if key not in banners.KEYS:
        await ctx.reply(f"Usage: <code>/delbanner menu</code>. Screens: {KEY_HELP}")
        return
    if ctx.tenant is None:
        if ctx.role < Role.OWNER:
            return
        removed = await banners.delete_global(ctx.session, ctx.cache, key)
    else:
        removed = banners.set_tenant(ctx.tenant, key, None)
    await ctx.reply("🗑 Removed." if removed else "No banner was set for that screen.")


@command("banners", description="Show which banners are set", category=CAT, permission=Role.ADMIN)
async def banners_cmd(ctx: Ctx) -> None:
    g = await banners.global_banners(ctx.session, ctx.cache)
    own = (ctx.tenant.settings or {}).get("banners", {}) if ctx.tenant else {}
    lines = ["<b>🖼 Banners</b>"]
    for k in banners.KEYS:
        mark = "🏠 group" if k in own else "🤖 bot" if k in g else "—"
        lines.append(f"• {k}: {mark}")
    lines.append("\nSet one with <code>/setbanner &lt;screen&gt;</code> (reply to a photo). "
                 "Groups need the Pro plan or higher for custom banners.")
    await ctx.reply("\n".join(lines))


@command("uploadbanners", description="Upload the built-in banner images to Telegram (owner)", category=CAT,
         usage="/uploadbanners [force]", permission=Role.OWNER, scope="private", cooldown=30)
async def uploadbanners(ctx: Ctx) -> None:
    force = bool(ctx.args and ctx.args[0].lower() == "force")
    g = await banners.global_banners(ctx.session, ctx.cache)
    done, skipped = [], []
    for key in banners.KEYS:
        path = ASSETS / f"{key}.jpg"
        if not path.exists() or (key in g and not force):
            skipped.append(key)
            continue
        try:
            sent = await ctx.bot.send_photo(ctx.message.chat.id, FSInputFile(path), caption=f"banner: {key}")
        except TelegramAPIError as exc:
            await ctx.reply(f"⚠️ Upload failed at {key}: {exc}")
            return
        await banners.set_global(ctx.session, ctx.cache, key, sent.photo[-1].file_id)
        done.append(key)
    await ctx.reply(f"✅ Uploaded {len(done)} banner(s)" + (f", kept {len(skipped)} existing (use <code>/uploadbanners force</code> to replace)." if skipped else "."))


@command("theme", description="Choose this group's menu style", category=CAT, usage="/theme [classic|kawaii|cyber|minimal]",
         examples=("/theme kawaii",), permission=Role.ADMIN, scope="group")
async def theme(ctx: Ctx) -> None:
    assert ctx.tenant is not None
    name = ctx.args[0].lower() if ctx.args else ""
    if name not in THEMES:
        cur = theme_of(ctx.tenant).name
        await ctx.reply("<b>🎨 Themes</b>\n" + "\n".join(
            f"{'✅' if t.name == cur else '•'} <b>{t.name}</b> — {t.description}" for t in THEMES.values())
            + "\n\nPick one: <code>/theme kawaii</code>")
        return
    ctx.tenant.settings = {**(ctx.tenant.settings or {}), "theme": name}
    await tenants_repo.add_audit(ctx.session, "theme_set", ctx.user_id, ctx.tenant.id, {"theme": name})
    from app.bot import ui

    text, kb = ui.home_view(ctx.role, ctx.tenant, True)
    await ctx.reply(f"🎨 Theme set to <b>{name}</b>. Preview:\n\n{text}", kb, banner="menu")
