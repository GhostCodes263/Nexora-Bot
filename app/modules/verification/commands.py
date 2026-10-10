from __future__ import annotations

from datetime import timedelta
from html import escape

from sqlalchemy import delete, func, select

from app.bot.context import Ctx
from app.bot.registry import command
from app.bot.states.verification import Apply
from app.database.models import VerificationApplication
from app.modules.verification import flow
from app.repositories import tenants as tenants_repo
from app.repositories import verification as repo
from app.services import config
from app.services import vip as vip_service
from app.services.roles import Role
from app.utils.time import aware, utcnow

CAT = "verification"

INTRO = (
    "<b>✅ Verification</b>\n"
    "I'll ask a few short questions. I only store what you type here. <b>Never send ID documents or "
    "exact addresses.</b> You can stop any time with /cancelverify and delete your data later with "
    "/deleteverification.\n"
)


async def _reviewer(ctx: Ctx) -> bool:
    if await repo.is_reviewer(ctx.session, ctx.user_id, ctx.role):
        return True
    return False  # stay silent: reviewer commands are not advertised


@command("verify", description="Apply for community verification", category=CAT, scope="private")
async def verify(ctx: Ctx) -> None:
    app = await repo.latest(ctx.session, ctx.user_id)
    if app is not None:
        if app.status in ("PENDING", "UNDER_REVIEW"):
            await ctx.reply(f"Your application <b>{app.public_id}</b> is already in review ({app.status}).")
            return
        if app.status == "APPROVED":
            await ctx.reply(f"✅ You are already verified (<b>{app.public_id}</b>). Unlock VIP with /vip.")
            return
        if app.status == "SUSPENDED":
            await ctx.reply("Your verification is suspended. Please contact the owner.")
            return
        if app.status == "NEEDS_MORE_INFORMATION":
            await ctx.state.set_state(Apply.more_info)
            await ctx.reply(f"A reviewer asked for more information:\n<i>{escape(app.reviewer_note or '')}</i>\n\nSend your answer as one message.")
            return
        if app.status == "REJECTED" and utcnow() - aware(app.updated_at) < timedelta(days=7):
            await ctx.reply("Your last application was not approved. You can apply again 7 days after the decision.")
            return
    await ctx.state.set_state(Apply.answering)
    await ctx.state.update_data(idx=0, answers={}, photo=None)
    qs = await flow.questions(ctx.session)
    await ctx.reply(INTRO + "\n" + qs[0]["text"])


@command("cancelverify", description="Stop filling in a verification application", category=CAT, scope="private")
async def cancelverify(ctx: Ctx) -> None:
    await ctx.state.clear()
    await ctx.reply("Okay, nothing was saved. Start again any time with /verify.")


@command("vstatus", description="Check your verification status", category=CAT, scope="private")
async def vstatus(ctx: Ctx) -> None:
    app = await repo.latest(ctx.session, ctx.user_id)
    if app is None:
        await ctx.reply("You haven't applied yet. Start with /verify.")
        return
    text = f"🪪 <b>{app.public_id}</b> — <b>{app.status}</b>"
    if app.status in ("REJECTED", "NEEDS_MORE_INFORMATION") and app.reviewer_note:
        text += f"\nNote: {escape(app.reviewer_note)}"
    if app.status == "APPROVED":
        text += "\n✅ Verified. Unlock VIP with /vip."
    await ctx.reply(text)


@command("deleteverification", description="Delete your verification application and data", category=CAT,
         usage="/deleteverification confirm", scope="private")
async def deleteverification(ctx: Ctx) -> None:
    if not ctx.args or ctx.args[0].lower() != "confirm":
        await ctx.reply("This permanently deletes your application, answers and photo reference. "
                        "Send <code>/deleteverification confirm</code> to proceed.")
        return
    res = await ctx.session.execute(delete(VerificationApplication).where(VerificationApplication.user_id == ctx.user_id))
    await tenants_repo.add_audit(ctx.session, "verification_deleted_by_user", ctx.user_id, None, {})
    await ctx.reply("🗑 Deleted." if res.rowcount else "You have no application stored.")


# --- reviewers (not listed in /help; every action re-checks the reviewer list) ----------------------

@command("vqueue", description="Applications waiting for review", category=CAT, scope="private", hidden=True)
async def vqueue(ctx: Ctx) -> None:
    if not await _reviewer(ctx):
        return
    rows = await repo.queue(ctx.session, 10)
    if not rows:
        await ctx.reply("📭 The queue is empty.")
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=f"{a.public_id} · {a.status}{' 🚩' if a.flagged else ''}",
        callback_data=flow.Ver(a="open", i=a.id).pack())] for a in rows])
    await ctx.reply(f"<b>📋 Queue</b> ({len(rows)} shown)", kb)


@command("vreview", description="Open an application by its ID", category=CAT, usage="/vreview V-48291",
         scope="private", hidden=True)
async def vreview(ctx: Ctx) -> None:
    if not await _reviewer(ctx):
        return
    app = await repo.get_by_public_id(ctx.session, ctx.raw_args) if ctx.raw_args else None
    if app is None:
        await ctx.reply("Usage: <code>/vreview V-48291</code>")
        return
    from app.database.models import User

    user = await ctx.session.get(User, app.user_id)
    await ctx.reply(flow.review_text(app, user.username if user else None), flow.review_kb(app))


@command("vstats", description="Verification counts by status", category=CAT, scope="private", hidden=True)
async def vstats(ctx: Ctx) -> None:
    if not await _reviewer(ctx):
        return
    rows = (await ctx.session.execute(
        select(VerificationApplication.status, func.count()).group_by(VerificationApplication.status))).all()
    await ctx.reply("<b>📊 Verification</b>\n" + ("\n".join(f"• {s}: {n}" for s, n in rows) or "No applications yet."))


# --- owner-only -------------------------------------------------------------------------------------------------

@command("vsuspend", description="Suspend a verification and revoke VIP access", category=CAT,
         usage="/vsuspend <V-ID> [reason]", permission=Role.OWNER, scope="private")
async def vsuspend(ctx: Ctx) -> None:
    pid, _, reason = ctx.raw_args.partition(" ")
    app = await repo.get_by_public_id(ctx.session, pid) if pid else None
    if app is None:
        await ctx.reply("Usage: <code>/vsuspend V-48291 reason</code>")
        return
    await repo.set_status(ctx.session, app, "SUSPENDED", ctx.user_id, reason.strip())
    m = await vip_service.get(ctx.session, app.user_id)
    if m is not None and vip_service.is_active(m):
        m.status = "revoked"
        ch = await vip_service.channel_id(ctx.session, ctx.settings)
        if ch:
            await vip_service.revoke_access(ctx.bot, ch, app.user_id)
    await flow.dm(ctx.bot, app.user_id, "⛔ Your verification has been suspended. Contact the owner if you think this is a mistake.")
    await ctx.reply(f"⛔ {app.public_id} suspended.")


async def _target_id(ctx: Ctx) -> int | None:
    target, _ = await ctx.resolve_target()
    return target.id if target else None


@command("addreviewer", description="Let a trusted person review verification applications", category=CAT,
         usage="/addreviewer <id|@user>", permission=Role.OWNER, scope="private")
async def addreviewer(ctx: Ctx) -> None:
    uid = await _target_id(ctx)
    if uid is None:
        await ctx.reply("Usage: <code>/addreviewer 12345</code>")
        return
    added = await repo.add_reviewer(ctx.session, uid, ctx.user_id)
    await tenants_repo.add_audit(ctx.session, "reviewer_added", ctx.user_id, None, {"target": uid})
    await ctx.reply(("✅ Added" if added else "Already a reviewer:") + f" <code>{uid}</code>.\n"
                    "Reviewers can only review applications. They get no payment, database or owner access.")


@command("delreviewer", description="Remove a reviewer", category=CAT, usage="/delreviewer <id|@user>",
         permission=Role.OWNER, scope="private")
async def delreviewer(ctx: Ctx) -> None:
    uid = await _target_id(ctx)
    if uid is None:
        await ctx.reply("Usage: <code>/delreviewer 12345</code>")
        return
    removed = await repo.remove_reviewer(ctx.session, uid)
    await tenants_repo.add_audit(ctx.session, "reviewer_removed", ctx.user_id, None, {"target": uid})
    await ctx.reply("✅ Removed." if removed else "That user is not a reviewer.")


@command("reviewers", description="List verification reviewers", category=CAT, permission=Role.OWNER, scope="private")
async def reviewers(ctx: Ctx) -> None:
    rows = await repo.list_reviewers(ctx.session)
    await ctx.reply("<b>🧑‍⚖️ Reviewers</b>\n" + ("\n".join(f"• <code>{r.user_id}</code>" for r in rows) or "None yet. Add one with /addreviewer."))


@command("vquestions", description="View or edit the application questions", category=CAT,
         usage="/vquestions [add <text> | del <n> | photo on|off]", permission=Role.OWNER, scope="private")
async def vquestions(ctx: Ctx) -> None:
    cfg = await config.verification(ctx.session)
    stored = await tenants_repo.get_global(ctx.session, "verification")
    action, _, rest = ctx.raw_args.partition(" ")
    extra = list(cfg.get("extra_questions", []))
    if action == "add" and rest.strip():
        extra.append(rest.strip()[:200])
    elif action == "del" and rest.strip().isdigit() and 1 <= int(rest.strip()) <= len(extra):
        extra.pop(int(rest.strip()) - 1)
    elif action == "photo" and rest.strip().lower() in ("on", "off"):
        await tenants_repo.set_global(ctx.session, "verification", {**stored, "require_photo": rest.strip().lower() == "on"})
        await ctx.reply(f"📸 Photo is now {'required' if rest.strip().lower() == 'on' else 'not required'}.")
        return
    elif action:
        await ctx.reply("Usage: <code>/vquestions add Why do you want to join?</code> · <code>/vquestions del 1</code> · <code>/vquestions photo on</code>")
        return
    else:
        qs = await flow.questions(ctx.session)
        await ctx.reply("<b>❓ Questions</b>\n" + "\n".join(f"{n}. {escape(q['text'])}" for n, q in enumerate(qs, 1)))
        return
    await tenants_repo.set_global(ctx.session, "verification", {**stored, "extra_questions": extra})
    await ctx.reply("✅ Saved. Extra questions: " + (", ".join(f"{n}. {escape(q)}" for n, q in enumerate(extra, 1)) or "none"))
