from __future__ import annotations

import re
import secrets
from datetime import timedelta
from html import escape

from sqlalchemy import func, select, update

from app.bot.context import Ctx
from app.bot.handlers.dating import (
    answer_proposal,
    dating_enabled,
    notify_match,
    proposal_kb,
    show_next,
)
from app.bot.registry import command
from app.bot.states.dating import DSetup
from app.database.models import DatingReport
from app.modules.dating import flow
from app.repositories import dating as repo
from app.repositories import tenants as tenants_repo
from app.repositories import users as users_repo
from app.services import gamestate
from app.services.roles import Role
from app.utils.time import aware, utcnow

CAT = "dating"
OFF = {"enabled_by_default": False}  # rented groups must opt in to dating


async def _profile(ctx: Ctx, *, need_active: bool = True):
    p = await repo.get_profile(ctx.session, ctx.user_id)
    if p is None or (need_active and p.opted_out):
        await ctx.reply("Create your profile first with /dsetup." if p is None else "You've opted out. Use /doptin to come back.")
        return None
    return p


async def _name(ctx: Ctx, uid: int) -> str:
    p = await repo.get_profile(ctx.session, uid)
    return escape(p.name) if p else str(uid)


async def _reach(ctx: Ctx, target_id: int) -> list[int] | None:
    """Pools (groups) where both people are members and dating is enabled. Replies if there are none."""
    if target_id == ctx.user_id or await repo.is_blocked(ctx.session, ctx.user_id, target_id):
        await ctx.reply("That profile isn't available.")
        return None
    them = await repo.get_profile(ctx.session, target_id)
    shared = [t for t in await repo.shared_tenants(ctx.session, ctx.user_id, target_id) if await dating_enabled(ctx.session, t)]
    if them is None or them.hidden or them.opted_out or not shared:
        await ctx.reply("That profile isn't available (they must share a dating pool with you).")
        return None
    return shared


async def _target(ctx: Ctx, usage: str):
    target, rest = await ctx.resolve_target()
    if target is None:
        await ctx.reply(f"Usage: <code>{usage}</code> (use their numeric ID or @username if I've seen them)")
        return None, []
    return target, rest


@command("dating", description="How dating works and your status", category=CAT, **OFF)
async def dating(ctx: Ctx) -> None:
    p = await repo.get_profile(ctx.session, ctx.user_id)
    pools = await repo.pools_of(ctx.session, ctx.user_id)
    state = "no profile yet" if p is None else "opted out" if p.opted_out else "hidden" if p.hidden else "visible"
    await ctx.reply(
        "<b>❤️ Dating</b> (18+, opt-in, private)\n"
        f"Your profile: <b>{state}</b> · pools joined: {len(pools)}\n\n"
        "1. /dsetup in private chat to build your profile\n2. /djoin inside a group that enabled dating\n"
        "3. /discover to browse people from your pools\n4. mutual likes become matches; matches can /propose\n\n"
        "Privacy: /dhide, /doptout, /dprivacy, /dblock, /dreport, /ddelete")


@command("dsetup", description="Create or update your dating profile", category=CAT, scope="private", **OFF)
async def dsetup(ctx: Ctx) -> None:
    await ctx.state.set_state(DSetup.answering)
    await ctx.state.update_data(idx=0, answers={}, photo=None)
    await ctx.reply("<b>❤️ Dating profile</b> (adults only). I store only what you type here and you can delete it any time "
                    "with /ddelete. Stop any time with /cancelsetup.\n\n" + flow.QUESTIONS[0]["text"])


@command("cancelsetup", description="Stop filling in your dating profile", category=CAT, scope="private", **OFF)
async def cancelsetup(ctx: Ctx) -> None:
    await ctx.state.clear()
    await ctx.reply("Okay, nothing more was saved.")


@command("myprofile", description="Show your own dating profile", category=CAT, scope="private", **OFF)
async def myprofile(ctx: Ctx) -> None:
    p = await repo.get_profile(ctx.session, ctx.user_id)
    if p is None:
        await ctx.reply("You have no profile yet. Create one with /dsetup.")
        return
    flags = f"hidden: {'yes' if p.hidden else 'no'} · opted out: {'yes' if p.opted_out else 'no'} · share contact on match: {'yes' if p.share_contact else 'no'}"
    caption = flow.card(p) + f"\n\n<i>{flags}\nPrefers: {p.pref_gender}, ages {p.pref_min_age}-{p.pref_max_age}</i>"
    if p.photo_file_id:
        await ctx.message.reply_photo(p.photo_file_id, caption=caption[:1020])
    else:
        await ctx.reply(caption)


@command("profile", description="View a profile from a pool you share", category=CAT, usage="/profile <id|@user>",
         scope="private", **OFF)
async def profile(ctx: Ctx) -> None:
    me = await _profile(ctx)
    target, _ = await _target(ctx, "/profile @user")
    if me is None or target is None:
        return
    if await _reach(ctx, target.id) is None:
        return
    them = await repo.get_profile(ctx.session, target.id)
    text = flow.card(them, flow.compat_score(me, them))  # type: ignore[arg-type]
    if them.photo_file_id:  # type: ignore[union-attr]
        await ctx.message.reply_photo(them.photo_file_id, caption=text[:1020])  # type: ignore[union-attr]
    else:
        await ctx.reply(text)


async def _set_flag(ctx: Ctx, **values) -> None:
    p = await repo.get_profile(ctx.session, ctx.user_id)
    if p is None:
        await ctx.reply("You have no profile yet. Create one with /dsetup.")
        return
    for k, v in values.items():
        setattr(p, k, v)
    p.updated_at = utcnow()


@command("dhide", description="Hide your profile from discovery (keeps matches)", category=CAT, scope="private", **OFF)
async def dhide(ctx: Ctx) -> None:
    await _set_flag(ctx, hidden=True)
    await ctx.reply("🙈 Your profile is hidden. Bring it back with /dshow.")


@command("dshow", description="Make your profile visible again", category=CAT, scope="private", **OFF)
async def dshow(ctx: Ctx) -> None:
    await _set_flag(ctx, hidden=False)
    await ctx.reply("👀 Your profile is visible in the pools you joined.")


@command("doptout", description="Opt out of matching entirely", category=CAT, scope="private", **OFF)
async def doptout(ctx: Ctx) -> None:
    await _set_flag(ctx, opted_out=True, hidden=True)
    for t in await repo.pools_of(ctx.session, ctx.user_id):
        await repo.leave_pool(ctx.session, t.id, ctx.user_id)
    await ctx.reply("✅ You're opted out: hidden everywhere and removed from all pools. Existing matches stay. /doptin reverses this.")


@command("doptin", description="Opt back in to matching", category=CAT, scope="private", **OFF)
async def doptin(ctx: Ctx) -> None:
    await _set_flag(ctx, opted_out=False)
    await ctx.reply("✅ Opted back in. Rejoin groups' pools with /djoin and use /dshow if you want to be visible.")


@command("ddelete", description="Delete your dating profile and all dating data", category=CAT,
         usage="/ddelete confirm", scope="private", **OFF)
async def ddelete(ctx: Ctx) -> None:
    if not ctx.args or ctx.args[0].lower() != "confirm":
        await ctx.reply("This permanently deletes your profile, photo, swipes, matches, couples and blocks.\n"
                        "Send <code>/ddelete confirm</code> to proceed.")
        return
    await repo.delete_all(ctx.session, ctx.user_id)
    await tenants_repo.add_audit(ctx.session, "dating_deleted_by_user", ctx.user_id, None, {})
    await ctx.reply("🗑 All your dating data was deleted.")


@command("djoin", description="Join this group's dating pool", category=CAT, scope="group", **OFF)
async def djoin(ctx: Ctx) -> None:
    p = await repo.get_profile(ctx.session, ctx.user_id)
    if ctx.anonymous or p is None or p.opted_out:
        await ctx.reply("Create a profile in a private chat with me first: /dsetup (adults only).")
        return
    added = await repo.join_pool(ctx.session, ctx.tenant.id, ctx.user_id)  # type: ignore[union-attr]
    await ctx.reply("❤️ You joined this group's dating pool. Browse in private with /discover." if added else "You're already in this pool.")


@command("dleave", description="Leave this group's dating pool", category=CAT, scope="group", **OFF)
async def dleave(ctx: Ctx) -> None:
    left = await repo.leave_pool(ctx.session, ctx.tenant.id, ctx.user_id)  # type: ignore[union-attr]
    await ctx.reply("✅ You left this pool." if left else "You weren't in this pool.")


@command("dpool", description="List the dating pools you joined", category=CAT, scope="private", **OFF)
async def dpool(ctx: Ctx) -> None:
    pools = await repo.pools_of(ctx.session, ctx.user_id)
    await ctx.reply("<b>🏠 Your pools</b>\n" + ("\n".join(f"• {escape(t.title or str(t.chat_id))}" for t in pools)
                    or "None yet. Send /djoin inside a group that enabled dating."))


@command("discover", description="Browse profiles from your pools", category=CAT, scope="private", cooldown=2, **OFF)
async def discover(ctx: Ctx) -> None:
    if await _profile(ctx) is None:
        return
    await show_next(ctx.bot, ctx.session, ctx.message.chat.id, ctx.user_id)


async def _swipe(ctx: Ctx, kind: str) -> None:
    me = await _profile(ctx)
    target, _ = await _target(ctx, f"/{ctx.name} @user")
    if me is None or target is None:
        return
    shared = await _reach(ctx, target.id)
    if shared is None:
        return
    matched = await repo.record_swipe(ctx.session, shared[0], ctx.user_id, target.id, kind)
    if matched:
        await notify_match(ctx.bot, ctx.session, ctx.user_id, target.id)
        await ctx.reply("🎉 It's a match! I sent you both the details.")
    elif kind == "dislike":
        await ctx.reply("👎 Passed.")
    else:
        if kind == "crush" and await ctx.cache.set_nx(f"crush:{ctx.user_id}:{target.id}", "1", 86400):
            from app.bot.handlers.dating import _send
            await _send(ctx.bot, target.id, "🔥 Someone has a crush on you! Open /discover, they may show up.")
        await ctx.reply("❤️ Like sent." if kind == "like" else "🔥 Crush sent (anonymous unless it's mutual).")


@command("like", description="Like someone in a shared pool", category=CAT, usage="/like <id|@user>", scope="private", **OFF)
async def like(ctx: Ctx) -> None:
    await _swipe(ctx, "like")


@command("dislike", description="Pass on someone", category=CAT, usage="/dislike <id|@user>", scope="private", **OFF)
async def dislike(ctx: Ctx) -> None:
    await _swipe(ctx, "dislike")


@command("crush", description="Send an anonymous crush", category=CAT, usage="/crush <id|@user>", scope="private", cooldown=5, **OFF)
async def crush(ctx: Ctx) -> None:
    await _swipe(ctx, "crush")


@command("matches", description="Your mutual matches", category=CAT, scope="private", **OFF)
async def matches(ctx: Ctx) -> None:
    rows_ = await repo.matches_of(ctx.session, ctx.user_id)
    lines = ["<b>💘 Your matches</b>"]
    for m in rows_[:20]:
        other = m.user_b if m.user_a == ctx.user_id else m.user_a
        p = await repo.get_profile(ctx.session, other)
        if p is None:
            continue
        link = f' · <a href="tg://user?id={other}">message</a>' if p.share_contact else ""
        lines.append(f"• {escape(p.name)}, {p.age}{link}")
    await ctx.reply("\n".join(lines) if len(lines) > 1 else "No matches yet. Keep exploring with /discover.")


@command("compat", description="Compatibility score with someone", category=CAT, usage="/compat <id|@user>",
         scope="private", cooldown=3, **OFF)
async def compat(ctx: Ctx) -> None:
    me = await _profile(ctx)
    target, _ = await _target(ctx, "/compat @user")
    if me is None or target is None or await _reach(ctx, target.id) is None:
        return
    them = await repo.get_profile(ctx.session, target.id)
    score = flow.compat_score(me, them)  # type: ignore[arg-type]
    common = flow.interests_set(me.interests) & flow.interests_set(them.interests)  # type: ignore[union-attr]
    await ctx.reply(f"💘 Compatibility with <b>{escape(them.name)}</b>: <b>{score}%</b>\n"  # type: ignore[union-attr]
                    + (f"Shared interests: {escape(', '.join(sorted(common)))}" if common else "No shared interests listed yet."))


@command("dpref", description="Set who you want to see (gender and age range)", category=CAT,
         usage="/dpref [gender any|male|female|other] [age 20-35]", examples=("/dpref gender female", "/dpref age 25-40"),
         scope="private", **OFF)
async def dpref(ctx: Ctx) -> None:
    p = await _profile(ctx)
    if p is None:
        return
    a = ctx.args
    if len(a) == 2 and a[0].lower() == "gender" and a[1].lower() in ("any", "male", "female", "other"):
        p.pref_gender = a[1].lower()
    elif len(a) == 2 and a[0].lower() == "age" and (m := re.fullmatch(r"(\d{2})-(\d{2})", a[1])) and flow.MIN_AGE <= int(m[1]) <= int(m[2]) <= 99:
        p.pref_min_age, p.pref_max_age = int(m[1]), int(m[2])
    elif a:
        await ctx.reply("Usage: <code>/dpref gender female</code> or <code>/dpref age 25-40</code> (18-99)")
        return
    await ctx.reply(f"⚙️ You see: <b>{p.pref_gender}</b>, ages <b>{p.pref_min_age}-{p.pref_max_age}</b>")


@command("dprivacy", description="Choose whether matches get a link to message you", category=CAT,
         usage="/dprivacy contact <on|off>", scope="private", **OFF)
async def dprivacy(ctx: Ctx) -> None:
    p = await _profile(ctx)
    if p is None:
        return
    if len(ctx.args) == 2 and ctx.args[0].lower() == "contact" and ctx.args[1].lower() in ("on", "off"):
        p.share_contact = ctx.args[1].lower() == "on"
    await ctx.reply(f"🔒 Contact link shown to new matches: <b>{'on' if p.share_contact else 'off'}</b>.\n"
                    "Change with <code>/dprivacy contact off</code>. Your profile is never shown outside pools you joined.")


# --- blocking & reporting --------------------------------------------------------------------------------------

@command("dblock", description="Block someone (they can't see you either)", category=CAT, usage="/dblock <id|@user>",
         scope="private", **OFF)
async def dblock(ctx: Ctx) -> None:
    target, _ = await _target(ctx, "/dblock @user")
    if target is None or target.id == ctx.user_id:
        return
    await repo.add_block(ctx.session, ctx.user_id, target.id)
    await ctx.reply("🚫 Blocked. Neither of you will see the other in dating.")


@command("dunblock", description="Unblock someone", category=CAT, usage="/dunblock <id|@user>", scope="private", **OFF)
async def dunblock(ctx: Ctx) -> None:
    target, _ = await _target(ctx, "/dunblock @user")
    if target is None:
        return
    await ctx.reply("✅ Unblocked." if await repo.remove_block(ctx.session, ctx.user_id, target.id) else "They weren't blocked.")


@command("dblocked", description="List people you blocked", category=CAT, scope="private", **OFF)
async def dblocked(ctx: Ctx) -> None:
    ids = await repo.blocks_of(ctx.session, ctx.user_id)
    await ctx.reply("<b>🚫 Blocked</b>\n" + ("\n".join(f"• <code>{i}</code>" for i in ids) or "Nobody."))


@command("dreport", description="Report a dating profile to the moderators", category=CAT,
         usage="/dreport <id|@user> [reason]", scope="private", cooldown=10, **OFF)
async def dreport(ctx: Ctx) -> None:
    target, rest = await _target(ctx, "/dreport @user reason")
    if target is None or target.id == ctx.user_id:
        return
    shared = await repo.shared_tenants(ctx.session, ctx.user_id, target.id)
    await repo.add_report(ctx.session, ctx.user_id, target.id, shared[0] if shared else None, " ".join(rest))
    await repo.add_block(ctx.session, ctx.user_id, target.id)
    await ctx.reply("🚨 Reported and blocked. Thank you for keeping the community safe.")


@command("dreports", description="Open dating reports grouped by person", category=CAT, permission=Role.SUPER_ADMIN,
         scope="private", **OFF)
async def dreports(ctx: Ctx) -> None:
    rows_ = (await ctx.session.execute(
        select(DatingReport.reported_id, func.count()).where(DatingReport.status == "open")
        .group_by(DatingReport.reported_id).order_by(func.count().desc()).limit(15))).all()
    if not rows_:
        await ctx.reply("No open reports. 🎉")
        return
    await ctx.reply("<b>🚨 Open reports</b>\n" + "\n".join(f"• <code>{uid}</code> — {n} report(s)" for uid, n in rows_)
                    + "\n\nAct on them (e.g. /globalban) then /dresolve &lt;id&gt;.")


@command("dresolve", description="Mark reports about a person as handled", category=CAT, usage="/dresolve <id>",
         permission=Role.SUPER_ADMIN, scope="private", **OFF)
async def dresolve(ctx: Ctx) -> None:
    if not ctx.args or not ctx.args[0].isdigit():
        await ctx.reply("Usage: <code>/dresolve 12345</code>")
        return
    res = await ctx.session.execute(update(DatingReport).where(
        DatingReport.reported_id == int(ctx.args[0]), DatingReport.status == "open").values(status="resolved"))
    await tenants_repo.add_audit(ctx.session, "dating_reports_resolved", ctx.user_id, None, {"target": int(ctx.args[0])})
    await ctx.reply(f"✅ {res.rowcount} report(s) resolved.")


# --- stats -------------------------------------------------------------------------------------------------------------

@command("dstats", description="Your match statistics", category=CAT, scope="private", **OFF)
async def dstats(ctx: Ctx) -> None:
    s = await repo.stats(ctx.session, ctx.user_id)
    await ctx.reply(f"<b>📊 Your stats</b>\nLikes sent: {s['likes_given']} · passes: {s['passes']}\n"
                    f"Likes received: {s['likes_received']} (crushes: {s['crushes_received']})\nMatches: <b>{s['matches']}</b> · "
                    f"couples: {len(await repo.couples_of(ctx.session, ctx.user_id))}")


# --- couples -----------------------------------------------------------------------------------------------------------------

@command("propose", description="Propose to become a couple with a match", category=CAT, usage="/propose <id|@user>",
         scope="private", cooldown=30, **OFF)
async def propose(ctx: Ctx) -> None:
    me = await _profile(ctx)
    target, _ = await _target(ctx, "/propose @user")
    if me is None or target is None or target.id == ctx.user_id:
        return
    tid = next((t for t in await repo.shared_tenants(ctx.session, ctx.user_id, target.id)
                if await repo.get_match(ctx.session, t, ctx.user_id, target.id)), None)
    if tid is None or await repo.is_blocked(ctx.session, ctx.user_id, target.id):
        await ctx.reply("You can only propose to someone you've matched with.")
        return
    if await repo.couple_of(ctx.session, tid, ctx.user_id) or await repo.couple_of(ctx.session, tid, target.id):
        await ctx.reply("One of you is already in a couple in that group.")
        return
    pid = gamestate.new_id()
    await gamestate.save(ctx.cache, f"pp{pid}", {"a": ctx.user_id, "b": target.id, "tenant": tid}, 86400)
    await ctx.cache.set(f"pplast:{target.id}", pid, 86400)
    from app.bot.handlers.dating import _send
    await _send(ctx.bot, target.id, f"💍 <b>{escape(me.name)}</b> proposes to become a couple with you!\n"
                                    "Answer with the buttons, or /daccept or /dreject.", None, proposal_kb(pid))
    await ctx.reply("💌 Proposal sent. They have 24 hours to answer.")


async def _answer(ctx: Ctx, accept: bool) -> None:
    pid = await ctx.cache.get(f"pplast:{ctx.user_id}")
    if not pid:
        await ctx.reply("You have no pending proposal.")
        return
    await ctx.reply(await answer_proposal(ctx.session, ctx.cache, ctx.bot, ctx.user_id, pid, accept))


@command("daccept", description="Accept your pending couple proposal", category=CAT, scope="private", **OFF)
async def daccept(ctx: Ctx) -> None:
    await _answer(ctx, True)


@command("dreject", description="Decline your pending couple proposal", category=CAT, scope="private", **OFF)
async def dreject(ctx: Ctx) -> None:
    await _answer(ctx, False)


async def _my_couple(ctx: Ctx):
    cs = await repo.couples_of(ctx.session, ctx.user_id)
    if not cs:
        await ctx.reply("You're not in a couple yet. Match with someone and /propose!")
        return None
    return cs[0]


@command("couple", description="Show your couple card", category=CAT, scope="private", **OFF)
async def couple(ctx: Ctx) -> None:
    c = await _my_couple(ctx)
    if c is None:
        return
    other = c.user_b if c.user_a == ctx.user_id else c.user_a
    days = (utcnow() - aware(c.since)).days
    await ctx.reply(f"💞 <b>You & {await _name(ctx, other)}</b>\nTogether {days} day(s) · couple level {flow.couple_level(c.xp)} · {c.xp:,} XP\n"
                    "Earn XP with /datenight.")


@command("datenight", description="Do a date night for couple XP (every 12 hours)", category=CAT, scope="private", **OFF)
async def datenight(ctx: Ctx) -> None:
    c = await _my_couple(ctx)
    if c is None:
        return
    last = aware(c.last_activity)
    if last and utcnow() - last < timedelta(hours=12):
        left = timedelta(hours=12) - (utcnow() - last)
        await ctx.reply(f"🌙 You two already had a date night. Next one in {int(left.total_seconds() // 3600)}h {int(left.total_seconds() % 3600 // 60)}m.")
        return
    ideas = ["watched a movie together 🎬", "cooked dinner together 🍝", "went stargazing 🌌", "had a picnic 🧺",
             "played games together 🎮", "took a long walk 🚶", "tried a new café ☕"]
    gain = 25 + secrets.randbelow(36)
    before = flow.couple_level(c.xp)
    c.xp += gain
    c.last_activity = utcnow()
    other = c.user_b if c.user_a == ctx.user_id else c.user_a
    text = f"💖 You and {await _name(ctx, other)} {secrets.choice(ideas)} (+{gain} couple XP)"
    if flow.couple_level(c.xp) > before:
        text += f"\n🎉 Couple level {flow.couple_level(c.xp)}!"
    await ctx.reply(text)


@command("couplexp", description="Your couple's XP and level", category=CAT, scope="private", **OFF)
async def couplexp(ctx: Ctx) -> None:
    c = await _my_couple(ctx)
    if c is None:
        return
    lvl = flow.couple_level(c.xp)
    lo, hi = 50 * lvl * lvl, 50 * (lvl + 1) ** 2
    filled = int(10 * (c.xp - lo) / max(hi - lo, 1))
    await ctx.reply(f"⭐ Couple level <b>{lvl}</b> · {c.xp:,} XP\n{'█' * filled}{'░' * (10 - filled)} {c.xp - lo}/{hi - lo}")


@command("coupleachievements", description="Achievements your couple unlocked", category=CAT, scope="private", **OFF)
async def coupleachievements(ctx: Ctx) -> None:
    c = await _my_couple(ctx)
    if c is None:
        return
    await ctx.reply("<b>🏅 Couple achievements</b>\n" + "\n".join(
        f"{'✅' if done else '🔒'} {name}" for name, done in flow.couple_achievements(c, utcnow())))


@command("breakup", description="End your couple", category=CAT, usage="/breakup confirm", scope="private", **OFF)
async def breakup(ctx: Ctx) -> None:
    c = await _my_couple(ctx)
    if c is None:
        return
    if not ctx.args or ctx.args[0].lower() != "confirm":
        await ctx.reply("💔 This ends your couple and resets your couple XP. Send <code>/breakup confirm</code> to continue.")
        return
    other = c.user_b if c.user_a == ctx.user_id else c.user_a
    await ctx.session.delete(c)
    from app.bot.handlers.dating import _send
    await _send(ctx.bot, other, "💔 Your couple was ended by your partner.")
    await ctx.reply("💔 Your couple has ended. Take care of yourself.")
