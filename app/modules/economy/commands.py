from __future__ import annotations

import re
import secrets
from html import escape

from sqlalchemy import select

from app.bot.context import Ctx
from app.bot.keyboards.play import Trade, rows
from app.bot.registry import command
from app.database.models import EcoItem, EcoLedger
from app.repositories import tenants as tenants_repo
from app.repositories import users as users_repo
from app.services import economy as eco
from app.services import gamestate
from app.services.gates import require_feature
from app.services.roles import Role
from app.utils.time import aware, human_duration, utcnow

CAT = "economy"
DAY, WEEK = 86400, 7 * 86400

JOBS = ["delivered parcels", "fixed a bug", "walked dogs", "served coffee", "painted a fence",
        "tutored a student", "washed cars", "repaired a bike", "wrote a report", "stocked shelves"]


def _cfg(ctx: Ctx) -> dict:
    assert ctx.tenant is not None
    return eco.eco_cfg(ctx.tenant)


async def _blocked(ctx: Ctx) -> bool:
    if ctx.anonymous:
        await ctx.reply("Anonymous admins can't use the economy. Please post as yourself.")
        return True
    return False


async def _name(ctx: Ctx, uid: int) -> str:
    u = await users_repo.get_user(ctx.session, uid)
    return escape((u.first_name if u and u.first_name else "") or str(uid))


def _rand(lo: int, hi: int) -> int:
    return lo + secrets.randbelow(max(hi - lo, 0) + 1)


async def _progress(ctx: Ctx, acct: eco.EcoAccount, gain_xp: int) -> list[str]:
    """Give XP, pay level-up bonuses and unlock achievements. Returns extra lines for the reply."""
    out: list[str] = []
    new_level = eco.add_xp(acct, gain_xp)
    if new_level:
        bonus = new_level * 50
        eco.change_wallet(ctx.session, acct, bonus, "level_up")
        out.append(f"🎉 Level <b>{new_level}</b>! Bonus {eco.fmt(_cfg(ctx), bonus)}")
    out += await eco.wealth_achievements(ctx.session, acct)
    return out


def _join(lines: list[str]) -> str:
    return "\n".join(lines)


# --- earning ----------------------------------------------------------------------------------------------

@command("balance", description="Show your wallet, bank and level", category=CAT, aliases=("bal",),
         usage="/balance [user]", scope="group")
async def balance(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    target, _ = await ctx.resolve_target()
    uid = target.id if target else ctx.user_id
    acct = await eco.get_account(ctx.session, ctx.tenant, uid)  # type: ignore[arg-type]
    cfg = _cfg(ctx)
    await ctx.reply(
        f"<b>💳 {await _name(ctx, uid)}</b>\nWallet: {eco.fmt(cfg, acct.wallet)}\nBank: {eco.fmt(cfg, acct.bank)}\n"
        f"Net worth: <b>{eco.fmt(cfg, eco.networth(acct))}</b>\nLevel {eco.level_for(acct.xp)} · {acct.xp:,} XP")


@command("daily", description="Claim your daily reward (streaks give a bonus)", category=CAT, scope="group")
async def daily(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    cfg, now = _cfg(ctx), utcnow()
    last = aware(acct.last_daily)
    if last and (now - last).total_seconds() < DAY:
        await ctx.reply(f"⏳ Come back in <b>{human_duration(DAY - (now - last).total_seconds())}</b>.")
        return
    acct.streak = acct.streak + 1 if last and (now - last).total_seconds() < 2 * DAY else 1
    base = int(cfg["daily"])
    reward = base + base * min(acct.streak, 10) // 10
    acct.last_daily = now
    eco.change_wallet(ctx.session, acct, reward, "daily")
    lines = [f"🌞 Daily reward: <b>{eco.fmt(cfg, reward)}</b> · streak <b>{acct.streak}</b> 🔥"]
    if msg := await eco.grant(ctx.session, acct.tenant_id, acct.user_id, "first_daily"):
        lines.append(msg)
    if acct.streak >= 7 and (msg := await eco.grant(ctx.session, acct.tenant_id, acct.user_id, "streak7")):
        lines.append(msg)
    await ctx.reply(_join(lines + await _progress(ctx, acct, 10)))


@command("weekly", description="Claim your weekly reward", category=CAT, scope="group")
async def weekly(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    cfg, now = _cfg(ctx), utcnow()
    last = aware(acct.last_weekly)
    if last and (now - last).total_seconds() < WEEK:
        await ctx.reply(f"⏳ Come back in <b>{human_duration(WEEK - (now - last).total_seconds())}</b>.")
        return
    acct.last_weekly = now
    reward = int(cfg["weekly"])
    eco.change_wallet(ctx.session, acct, reward, "weekly")
    await ctx.reply(_join([f"📅 Weekly reward: <b>{eco.fmt(cfg, reward)}</b>"] + await _progress(ctx, acct, 30)))


@command("work", description="Work a shift for coins", category=CAT, scope="group", cooldown=1800)
async def work(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    cfg = _cfg(ctx)
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    pay = _rand(int(cfg["work_min"]), max(int(cfg["work_min"]), int(cfg["work_max"])))
    boosted = await eco.effect_qty(ctx.session, acct.tenant_id, ctx.user_id, "work_boost") > 0
    if boosted:
        pay = pay * 125 // 100
    eco.change_wallet(ctx.session, acct, pay, "work")
    lines = [f"💼 You {secrets.choice(JOBS)} and earned <b>{eco.fmt(cfg, pay)}</b>" + (" (laptop bonus 💻)" if boosted else "")]
    await ctx.reply(_join(lines + await _progress(ctx, acct, 8)))


@command("beg", description="Beg for a few coins (might fail)", category=CAT, scope="group", cooldown=120)
async def beg(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    cfg = _cfg(ctx)
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    if secrets.randbelow(100) < 60:
        amount = _rand(1, int(cfg["beg_max"]))
        eco.change_wallet(ctx.session, acct, amount, "beg")
        await ctx.reply(f"🙏 A kind stranger gave you <b>{eco.fmt(cfg, amount)}</b>.")
    else:
        await ctx.reply("😔 Nobody gave you anything this time.")


@command("crime", description="Risky: big win or a fine", category=CAT, scope="group", cooldown=3600)
async def crime(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    cfg = _cfg(ctx)
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    if secrets.randbelow(100) < 50:
        gain = _rand(100, 400)
        eco.change_wallet(ctx.session, acct, gain, "crime")
        await ctx.reply(f"🕶 The heist worked! You scored <b>{eco.fmt(cfg, gain)}</b>.")
    else:
        fine = min(_rand(50, 200), acct.wallet)
        eco.change_wallet(ctx.session, acct, -fine, "crime_fine")
        await ctx.reply(f"🚔 Caught! You paid a fine of <b>{eco.fmt(cfg, fine)}</b>.")


@command("fish", description="Go fishing for a small prize", category=CAT, scope="group", cooldown=600)
async def fish(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    cfg = _cfg(ctx)
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    catches = [("an old boot 🥾", 0), ("a sardine 🐟", 15), ("a trout 🐟", 40), ("a salmon 🐠", 80), ("a tuna 🐟", 120), ("a golden fish ✨🐠", 400)]
    weights = [20, 30, 25, 15, 8, 2]
    roll, acc = secrets.randbelow(sum(weights)), 0
    for (name, value), w in zip(catches, weights, strict=True):
        acc += w
        if roll < acc:
            break
    if value:
        eco.change_wallet(ctx.session, acct, value, "fish")
    await ctx.reply(f"🎣 You caught {name}" + (f" and sold it for <b>{eco.fmt(cfg, value)}</b>." if value else " — worthless."))


@command("rob", description="Try to steal from another member (risky)", category=CAT, usage="/rob <user>",
         scope="group", cooldown=3600)
async def rob(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    cfg = _cfg(ctx)
    if not cfg["rob_enabled"]:
        await ctx.reply("Robbing is disabled in this group.")
        return
    target, _ = await ctx.resolve_target()
    if target is None or target.id in (ctx.user_id, ctx.bot.id):
        await ctx.reply("Usage: <code>/rob @user</code> (not yourself).")
        return
    if await ctx.cache.hit(f"robbed:{ctx.tenant.id}:{target.id}", 3600) > 3:  # type: ignore[union-attr]
        await ctx.reply("That member was robbed too often recently. Leave them alone for a while.")
        return
    accts = {}
    for uid in sorted((ctx.user_id, target.id)):
        accts[uid] = await eco.get_account(ctx.session, ctx.tenant, uid, lock=True)  # type: ignore[arg-type]
    thief, victim = accts[ctx.user_id], accts[target.id]
    if thief.wallet < 100 or victim.wallet < 100:
        await ctx.reply("Both of you need at least 100 in your wallet for a robbery.")
        return
    if await eco.consume_effect(ctx.session, victim.tenant_id, target.id, "padlock"):
        await ctx.reply("🔒 Their padlock stopped you (and broke).")
        return
    if secrets.randbelow(100) < int(cfg["rob_chance"]):
        loot = min(victim.wallet * _rand(10, 30) // 100, 50_000)
        eco.change_wallet(ctx.session, victim, -loot, "robbed")
        eco.change_wallet(ctx.session, thief, loot, "robbery")
        lines = [f"🦹 You stole <b>{eco.fmt(cfg, loot)}</b> from {escape(target.name)}!"]
        if msg := await eco.grant(ctx.session, thief.tenant_id, thief.user_id, "robber"):
            lines.append(msg)
        await ctx.reply(_join(lines))
    else:
        fine = min(thief.wallet // 10, 5_000)
        eco.change_wallet(ctx.session, thief, -fine, "rob_fine")
        await ctx.reply(f"🚨 You got caught and paid <b>{eco.fmt(cfg, fine)}</b>.")


# --- money movement --------------------------------------------------------------------------------------------

@command("pay", description="Send coins to another member", category=CAT, aliases=("give", "transfer"),
         usage="/pay <user> <amount>", examples=("/pay @alex 500",), scope="group", cooldown=3)
async def pay(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    cfg = _cfg(ctx)
    target, rest = await ctx.resolve_target()
    if target is None or not rest or target.id in (ctx.user_id, ctx.bot.id):
        await ctx.reply("Usage: <code>/pay @user 500</code> (not to yourself).")
        return
    mine = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id)  # type: ignore[arg-type]
    amount = eco.parse_amount(rest[0], mine.wallet)
    if amount is None or amount > mine.wallet:
        await ctx.reply("Enter a valid amount you can afford (wallet only).")
        return
    try:
        tax = await eco.transfer(ctx.session, ctx.tenant, ctx.user_id, target.id, amount, int(cfg["transfer_tax_pct"]))  # type: ignore[arg-type]
    except eco.InsufficientFunds:
        await ctx.reply("Not enough coins in your wallet.")
        return
    await ctx.reply(f"💸 Sent <b>{eco.fmt(cfg, amount)}</b> to {escape(target.name)}" + (f" (tax {eco.fmt(cfg, tax)})." if tax else "."))


@command("deposit", description="Move coins from wallet to bank (earns interest)", category=CAT, aliases=("dep",),
         usage="/deposit <amount|all>", scope="group")
async def deposit(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    cfg = _cfg(ctx)
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    eco.apply_interest(acct, cfg)
    amount = eco.parse_amount(ctx.args[0], acct.wallet) if ctx.args else None
    if amount is None or amount > acct.wallet:
        await ctx.reply("Usage: <code>/deposit 500</code> or <code>/deposit all</code> (must be in your wallet).")
        return
    eco.change_wallet(ctx.session, acct, -amount, "deposit")
    acct.bank += amount
    await ctx.reply(f"🏦 Deposited <b>{eco.fmt(cfg, amount)}</b>. Bank: {eco.fmt(cfg, acct.bank)}")


@command("withdraw", description="Move coins from bank to wallet", category=CAT, aliases=("with",),
         usage="/withdraw <amount|all>", scope="group")
async def withdraw(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    cfg = _cfg(ctx)
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    eco.apply_interest(acct, cfg)
    amount = eco.parse_amount(ctx.args[0], acct.bank) if ctx.args else None
    if amount is None or amount > acct.bank:
        await ctx.reply("Usage: <code>/withdraw 500</code> or <code>/withdraw all</code> (must be in your bank).")
        return
    acct.bank -= amount
    eco.change_wallet(ctx.session, acct, amount, "withdraw")
    await ctx.reply(f"🏦 Withdrew <b>{eco.fmt(cfg, amount)}</b>. Wallet: {eco.fmt(cfg, acct.wallet)}")


@command("bank", description="Your bank balance and daily interest", category=CAT, scope="group")
async def bank(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    cfg = _cfg(ctx)
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    gained = eco.apply_interest(acct, cfg)
    pct = int(cfg["interest_pct"])
    await ctx.reply(f"🏦 Bank: <b>{eco.fmt(cfg, acct.bank)}</b>\nInterest: {pct}% per day (compounded, up to 30 days)"
                    + (f"\n💹 Just added {eco.fmt(cfg, gained)} in interest." if gained else "")
                    + f"\nTomorrow's interest: about {eco.fmt(cfg, acct.bank * pct // 100)}")


# --- shop & items ---------------------------------------------------------------------------------------------------

def _item_line(cfg: dict, i: EcoItem) -> str:
    return f"• <b>{escape(i.name)}</b> — {eco.fmt(cfg, i.price)}\n   <i>{escape(i.description)}</i>"


@command("shop", description="Browse the item shop", category=CAT, scope="group")
async def shop(ctx: Ctx) -> None:
    if not await require_feature(ctx, "shop"):
        return
    await eco.ensure_default_items(ctx.session, ctx.tenant)  # type: ignore[arg-type]
    items = await eco.list_items(ctx.session, ctx.tenant.id)  # type: ignore[union-attr]
    cfg = _cfg(ctx)
    if not items:
        await ctx.reply("The shop is empty. Admins can add items with /additem.")
        return
    await ctx.reply("<b>🛒 Shop</b>\n" + "\n".join(_item_line(cfg, i) for i in items) + "\n\nBuy with <code>/buy name [qty]</code>")


@command("buy", description="Buy an item", category=CAT, usage="/buy <item> [quantity]", examples=("/buy padlock 2",), scope="group")
async def buy(ctx: Ctx) -> None:
    if await _blocked(ctx) or not await require_feature(ctx, "shop"):
        return
    if not ctx.args:
        await ctx.reply("Usage: <code>/buy padlock 2</code>")
        return
    qty = int(ctx.args[1]) if len(ctx.args) > 1 and ctx.args[1].isdigit() else 1
    item = await eco.find_item(ctx.session, ctx.tenant.id, ctx.args[0])  # type: ignore[union-attr]
    if item is None or not 1 <= qty <= 100:
        await ctx.reply("Unknown item, or quantity not between 1 and 100. See /shop.")
        return
    cfg = _cfg(ctx)
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    cost = item.price * qty
    try:
        eco.change_wallet(ctx.session, acct, -cost, f"buy {item.name}"[:48])
    except eco.InsufficientFunds:
        await ctx.reply(f"You need {eco.fmt(cfg, cost)} in your wallet (you have {eco.fmt(cfg, acct.wallet)}).")
        return
    await eco.add_item(ctx.session, acct.tenant_id, ctx.user_id, item, qty)
    await ctx.reply(f"🛍 Bought <b>{qty}× {escape(item.name)}</b> for {eco.fmt(cfg, cost)}.")


@command("sell", description="Sell an item back for half price", category=CAT, usage="/sell <item> [quantity]", scope="group")
async def sell(ctx: Ctx) -> None:
    if await _blocked(ctx) or not await require_feature(ctx, "shop"):
        return
    if not ctx.args:
        await ctx.reply("Usage: <code>/sell padlock 1</code>")
        return
    qty = int(ctx.args[1]) if len(ctx.args) > 1 and ctx.args[1].isdigit() else 1
    item = await eco.find_item(ctx.session, ctx.tenant.id, ctx.args[0])  # type: ignore[union-attr]
    if item is None or not 1 <= qty <= 100:
        await ctx.reply("Unknown item, or quantity not between 1 and 100.")
        return
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    if not await eco.remove_item(ctx.session, acct.tenant_id, ctx.user_id, item, qty):
        await ctx.reply("You don't own that many.")
        return
    gain = item.price * qty // 2
    eco.change_wallet(ctx.session, acct, gain, f"sell {item.name}"[:48])
    await ctx.reply(f"💰 Sold {qty}× {escape(item.name)} for <b>{eco.fmt(_cfg(ctx), gain)}</b>.")


@command("inventory", description="Show the items someone owns", category=CAT, aliases=("inv",),
         usage="/inventory [user]", scope="group")
async def inventory(ctx: Ctx) -> None:
    target, _ = await ctx.resolve_target()
    uid = target.id if target else ctx.user_id
    rows_ = await eco.inventory(ctx.session, ctx.tenant.id, uid)  # type: ignore[union-attr]
    if not rows_:
        await ctx.reply("🎒 Empty. Visit the /shop.")
        return
    await ctx.reply(f"<b>🎒 {await _name(ctx, uid)}'s items</b>\n" + "\n".join(f"• {escape(i.name)} × {q}" for i, q in rows_))


@command("iteminfo", description="Details about a shop item", category=CAT, usage="/iteminfo <item>", scope="group")
async def iteminfo(ctx: Ctx) -> None:
    item = await eco.find_item(ctx.session, ctx.tenant.id, ctx.raw_args) if ctx.raw_args else None  # type: ignore[union-attr]
    if item is None:
        await ctx.reply("Item not found. See /shop.")
        return
    await ctx.reply(_item_line(_cfg(ctx), item) + f"\nEffect: <code>{item.effect}</code>")


@command("use", description="Use an item (potions give XP; others work automatically)", category=CAT,
         usage="/use <item>", scope="group")
async def use(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    item = await eco.find_item(ctx.session, ctx.tenant.id, ctx.raw_args) if ctx.raw_args else None  # type: ignore[union-attr]
    if item is None:
        await ctx.reply("Usage: <code>/use potion</code>")
        return
    if item.effect != "xp_potion":
        await ctx.reply("That item works automatically while you own it. Nothing to do!")
        return
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    if not await eco.remove_item(ctx.session, acct.tenant_id, ctx.user_id, item, 1):
        await ctx.reply("You don't have one.")
        return
    await ctx.reply(_join(["🧪 +200 XP!"] + await _progress(ctx, acct, 200)))


@command("additem", description="Add a custom shop item", category=CAT, usage="/additem <name> <price> [description]",
         examples=("/additem vipbadge 5000 Shows you're a big spender",), permission=Role.ADMIN, scope="group")
async def additem(ctx: Ctx) -> None:
    if not await require_feature(ctx, "shop"):
        return
    a = ctx.raw_args.split(None, 2)
    if len(a) < 2 or not re.fullmatch(r"[a-z0-9_]{1,32}", a[0].lower()) or not a[1].isdigit() or not 1 <= int(a[1]) <= 10**9:
        await ctx.reply("Usage: <code>/additem name 500 optional description</code> (name: letters, digits, _)")
        return
    await eco.ensure_default_items(ctx.session, ctx.tenant)  # type: ignore[arg-type]
    if len(await eco.list_items(ctx.session, ctx.tenant.id)) >= 50 and not await eco.find_item(ctx.session, ctx.tenant.id, a[0]):  # type: ignore[union-attr]
        await ctx.reply("A shop can hold at most 50 items.")
        return
    item = await eco.find_item(ctx.session, ctx.tenant.id, a[0])  # type: ignore[union-attr]
    if item:
        item.price, item.description = int(a[1]), (a[2] if len(a) > 2 else item.description)[:200]
    else:
        ctx.session.add(EcoItem(tenant_id=ctx.tenant.id, name=a[0].lower(), price=int(a[1]),  # type: ignore[union-attr]
                                description=(a[2] if len(a) > 2 else "")[:200], effect="none"))
    await ctx.reply(f"✅ Shop item <b>{escape(a[0].lower())}</b> saved.")


@command("delitem", description="Remove a shop item", category=CAT, usage="/delitem <name>", permission=Role.ADMIN, scope="group")
async def delitem(ctx: Ctx) -> None:
    item = await eco.find_item(ctx.session, ctx.tenant.id, ctx.raw_args) if ctx.raw_args else None  # type: ignore[union-attr]
    if item is None:
        await ctx.reply("No such item.")
        return
    await ctx.session.delete(item)
    await ctx.reply(f"🗑 Removed <b>{escape(item.name)}</b> (owners keep nothing; inventories are cleared).")


@command("giftitem", description="Give an item to someone for free", category=CAT, usage="/giftitem <user> <item> [qty]", scope="group")
async def giftitem(ctx: Ctx) -> None:
    if await _blocked(ctx):
        return
    target, rest = await ctx.resolve_target()
    if target is None or not rest or target.id in (ctx.user_id, ctx.bot.id):
        await ctx.reply("Usage: <code>/giftitem @user padlock 1</code>")
        return
    qty = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 1
    item = await eco.find_item(ctx.session, ctx.tenant.id, rest[0])  # type: ignore[union-attr]
    if item is None or not 1 <= qty <= 100:
        await ctx.reply("Unknown item or quantity.")
        return
    if not await eco.remove_item(ctx.session, ctx.tenant.id, ctx.user_id, item, qty):  # type: ignore[union-attr]
        await ctx.reply("You don't own that many.")
        return
    await eco.add_item(ctx.session, ctx.tenant.id, target.id, item, qty)  # type: ignore[union-attr]
    await ctx.reply(f"🎁 Gave {qty}× {escape(item.name)} to {escape(target.name)}.")


@command("trade", description="Offer to sell an item to a member for a price", category=CAT,
         usage="/trade <user> <item> <qty> <price>", examples=("/trade @alex trophy 1 8000",), scope="group", cooldown=5)
async def trade(ctx: Ctx) -> None:
    if await _blocked(ctx) or not await require_feature(ctx, "shop"):
        return
    target, rest = await ctx.resolve_target()
    if target is None or len(rest) != 3 or not rest[1].isdigit() or not rest[2].isdigit() or target.id in (ctx.user_id, ctx.bot.id):
        await ctx.reply("Usage: <code>/trade @user trophy 1 8000</code>")
        return
    item = await eco.find_item(ctx.session, ctx.tenant.id, rest[0])  # type: ignore[union-attr]
    qty, price = int(rest[1]), int(rest[2])
    if item is None or not 1 <= qty <= 100 or not 0 <= price <= 10**9:
        await ctx.reply("Unknown item, or quantity/price out of range.")
        return
    if await eco.item_qty(ctx.session, ctx.tenant.id, ctx.user_id, item.id) < qty:  # type: ignore[union-attr]
        await ctx.reply("You don't own that many.")
        return
    tid = gamestate.new_id()
    await gamestate.save(ctx.cache, f"tr{tid}", {
        "tenant": ctx.tenant.id, "chat": ctx.message.chat.id, "seller": ctx.user_id,  # type: ignore[union-attr]
        "buyer": target.id, "item": item.id, "qty": qty, "price": price}, 300)
    kb = rows([("✅ Accept", Trade(a="yes", id=tid)), ("✖️ Decline", Trade(a="no", id=tid))])
    await ctx.reply(f"🤝 <b>Trade offer</b> for {escape(target.name)}:\n{qty}× {escape(item.name)} for "
                    f"{eco.fmt(_cfg(ctx), price)}\n<i>Only {escape(target.name)} can accept. Expires in 5 minutes.</i>", kb)


# --- leaderboards & profile ----------------------------------------------------------------------------------------------

@command("richest", description="Top members by net worth", category=CAT, aliases=("leaderboard", "lb"), scope="group")
async def richest(ctx: Ctx) -> None:
    cfg = _cfg(ctx)
    top = await eco.top_wealth(ctx.session, ctx.tenant.id)  # type: ignore[union-attr]
    if not top:
        await ctx.reply("Nobody has an account yet. Try /daily!")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["<b>🏆 Richest members</b>"]
    for n, a in enumerate(top):
        lines.append(f"{medals[n] if n < 3 else f'{n + 1}.'} {await _name(ctx, a.user_id)} — {eco.fmt(cfg, eco.networth(a))}")
    await ctx.reply("\n".join(lines))


@command("toplevels", description="Top members by XP", category=CAT, aliases=("topxp",), scope="group")
async def toplevels(ctx: Ctx) -> None:
    top = await eco.top_xp(ctx.session, ctx.tenant.id)  # type: ignore[union-attr]
    if not top:
        await ctx.reply("Nobody has XP yet. Chat and play to earn some!")
        return
    lines = ["<b>⭐ Top levels</b>"]
    for n, a in enumerate(top, 1):
        lines.append(f"{n}. {await _name(ctx, a.user_id)} — level {eco.level_for(a.xp)} ({a.xp:,} XP)")
    await ctx.reply("\n".join(lines))


@command("rank", description="Your level, XP and progress", category=CAT, aliases=("level",), usage="/rank [user]", scope="group")
async def rank(ctx: Ctx) -> None:
    target, _ = await ctx.resolve_target()
    uid = target.id if target else ctx.user_id
    acct = await eco.get_account(ctx.session, ctx.tenant, uid)  # type: ignore[arg-type]
    lvl = eco.level_for(acct.xp)
    lo, hi = eco.xp_for(lvl), eco.xp_for(lvl + 1)
    filled = int(10 * (acct.xp - lo) / max(hi - lo, 1))
    await ctx.reply(f"<b>⭐ {await _name(ctx, uid)}</b>\nLevel <b>{lvl}</b> · {acct.xp:,} XP\n"
                    f"{'█' * filled}{'░' * (10 - filled)} {acct.xp - lo:,}/{hi - lo:,} to level {lvl + 1}")


@command("achievements", description="Show unlocked achievements", category=CAT, aliases=("badges",),
         usage="/achievements [user]", scope="group")
async def achievements(ctx: Ctx) -> None:
    from app.database.models import Achievement

    target, _ = await ctx.resolve_target()
    uid = target.id if target else ctx.user_id
    got = set((await ctx.session.execute(select(Achievement.code).where(
        Achievement.tenant_id == ctx.tenant.id, Achievement.user_id == uid))).scalars())  # type: ignore[union-attr]
    lines = [f"<b>🏅 {await _name(ctx, uid)}'s achievements</b> ({len(got)}/{len(eco.ACHIEVEMENTS)})"]
    for code, (emoji, title, desc) in eco.ACHIEVEMENTS.items():
        lines.append(f"{emoji if code in got else '🔒'} <b>{title}</b> — {desc}")
    await ctx.reply("\n".join(lines))


@command("transactions", description="Your last 10 balance changes", category=CAT, aliases=("history",), scope="group")
async def transactions(ctx: Ctx) -> None:
    cfg = _cfg(ctx)
    rows_ = (await ctx.session.execute(
        select(EcoLedger).where(EcoLedger.tenant_id == ctx.tenant.id, EcoLedger.user_id == ctx.user_id)  # type: ignore[union-attr]
        .order_by(EcoLedger.id.desc()).limit(10))).scalars().all()
    if not rows_:
        await ctx.reply("No transactions yet.")
        return
    await ctx.reply("<b>🧾 Recent activity</b>\n" + "\n".join(
        f"{r.created_at:%m-%d %H:%M} · {'+' if r.delta > 0 else ''}{r.delta:,} · {escape(r.reason)}" for r in rows_))


# --- admin ------------------------------------------------------------------------------------------------------------------

@command("ecoconfig", description="View or change this group's economy settings", category=CAT,
         usage="/ecoconfig [setting] [value]", examples=("/ecoconfig daily 500", "/ecoconfig currency gems",
         "/ecoconfig rob_enabled off"), permission=Role.ADMIN, scope="group")
async def ecoconfig(ctx: Ctx) -> None:
    cfg = _cfg(ctx)
    a = ctx.args
    if not a:
        keys = ["currency", "symbol", *CFG_KEYS]
        await ctx.reply("<b>⚙️ Economy settings</b>\n" + "\n".join(f"• {k}: <code>{escape(str(cfg[k]))}</code>" for k in keys))
        return
    key = a[0].lower()
    if len(a) < 2:
        await ctx.reply("Usage: <code>/ecoconfig daily 500</code>")
        return
    if key in ("currency", "symbol"):
        cfg[key] = " ".join(a[1:])[:16 if key == "currency" else 4]
    elif key in eco.CFG_RULES:
        typ, lo, hi = eco.CFG_RULES[key]
        if typ is bool:
            cfg[key] = a[1].lower() in ("on", "true", "yes", "1")
        elif a[1].isdigit() and lo <= int(a[1]) <= hi:
            cfg[key] = int(a[1])
        else:
            await ctx.reply(f"{key} must be a number from {lo} to {hi}.")
            return
    else:
        await ctx.reply("Unknown setting. Send /ecoconfig to see them all.")
        return
    eco.save_eco_cfg(ctx.tenant, cfg)  # type: ignore[arg-type]
    await tenants_repo.add_audit(ctx.session, "eco_config", ctx.user_id, ctx.tenant.id, {"key": key})  # type: ignore[union-attr]
    await ctx.reply(f"✅ {key} = <code>{escape(str(cfg[key]))}</code>")


CFG_KEYS = list(eco.CFG_RULES)


async def _adjust(ctx: Ctx, sign: int) -> None:
    target, rest = await ctx.resolve_target()
    if target is None or not rest or not rest[0].isdigit() or not 1 <= int(rest[0]) <= 10**9:
        await ctx.reply(f"Usage: <code>/{ctx.name} @user 500</code> (max 1,000,000,000)")
        return
    acct = await eco.get_account(ctx.session, ctx.tenant, target.id, lock=True)  # type: ignore[arg-type]
    amount = int(rest[0])
    try:
        eco.change_wallet(ctx.session, acct, sign * amount, f"admin {ctx.user_id}"[:48])
    except eco.InsufficientFunds:
        acct.wallet = 0
    await tenants_repo.add_audit(ctx.session, f"eco_{ctx.name}", ctx.user_id, ctx.tenant.id,  # type: ignore[union-attr]
                                 {"target": target.id, "amount": amount})
    await ctx.reply(f"✅ {escape(target.name)} now has {eco.fmt(_cfg(ctx), acct.wallet)} in their wallet.")


@command("addmoney", description="Give coins to a member (logged)", category=CAT, usage="/addmoney <user> <amount>",
         permission=Role.ADMIN, scope="group")
async def addmoney(ctx: Ctx) -> None:
    await _adjust(ctx, 1)


@command("removemoney", description="Take coins from a member (logged)", category=CAT, usage="/removemoney <user> <amount>",
         permission=Role.ADMIN, scope="group")
async def removemoney(ctx: Ctx) -> None:
    await _adjust(ctx, -1)


@command("ecoreset", description="Reset one member's economy account", category=CAT, usage="/ecoreset <user> confirm",
         permission=Role.ADMIN, scope="group")
async def ecoreset(ctx: Ctx) -> None:
    target, rest = await ctx.resolve_target()
    if target is None or not rest or rest[0] != "confirm":
        await ctx.reply("Usage: <code>/ecoreset @user confirm</code> (wallet, bank, XP and streak are wiped)")
        return
    acct = await eco.get_account(ctx.session, ctx.tenant, target.id, lock=True)  # type: ignore[arg-type]
    acct.wallet = acct.bank = acct.xp = acct.streak = 0
    acct.last_daily = acct.last_weekly = acct.last_interest = None
    await tenants_repo.add_audit(ctx.session, "eco_reset", ctx.user_id, ctx.tenant.id, {"target": target.id})  # type: ignore[union-attr]
    await ctx.reply(f"🧹 Reset {escape(target.name)}'s account.")
