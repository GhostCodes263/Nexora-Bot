from __future__ import annotations

from app.bot.context import Ctx
from app.services import economy as eco
from app.services.gates import plan_of


async def bet_flow(ctx: Ctx, arg: str | None) -> tuple[eco.EcoAccount, int] | None:
    """Validate a bet and the daily play limit. Replies and returns None if the bet is not allowed."""
    if ctx.anonymous:
        await ctx.reply("Anonymous admins can't play. Please post as yourself.")
        return None
    cfg = eco.eco_cfg(ctx.tenant)  # type: ignore[arg-type]
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    amount = eco.parse_amount(arg or "", acct.wallet)
    if amount is None:
        await ctx.reply(f"Enter a bet, e.g. <code>/{ctx.name} 100</code> (you can use <code>all</code> or <code>half</code>).")
        return None
    if amount > int(cfg["max_bet"]):
        await ctx.reply(f"The maximum bet here is {eco.fmt(cfg, int(cfg['max_bet']))}.")
        return None
    if amount > acct.wallet:
        await ctx.reply(f"You only have {eco.fmt(cfg, acct.wallet)} in your wallet.")
        return None
    if not await play_allowed(ctx):
        return None
    return acct, amount


async def play_allowed(ctx: Ctx) -> bool:
    plan = await plan_of(ctx)
    limit = plan.limit("game_plays_per_day", 20)
    used = await ctx.cache.hit(f"plays:{ctx.tenant.id}:{ctx.user_id}", 86400)  # type: ignore[union-attr]
    if used > limit:
        await ctx.reply(f"🔒 Daily game limit reached ({limit} plays on the {plan.name} plan). Upgrade with /plans for more.")
        return False
    return True


async def settle(
    ctx_session, acct: eco.EcoAccount, cfg: dict, game: str, bet: int, payout: int, *, escrowed: bool = False,
) -> list[str]:
    """Charge the bet (unless already escrowed), pay out, record stats and XP. Returns extra reply lines."""
    if not escrowed:
        eco.change_wallet(ctx_session, acct, -bet, f"bet {game}")
    if payout:
        eco.change_wallet(ctx_session, acct, payout, f"win {game}")
    net = payout - bet
    result = "win" if net > 0 else "draw" if net == 0 else "loss"
    extra = await eco.record_game(ctx_session, acct.tenant_id, acct.user_id, game, result, net)
    new_level = eco.add_xp(acct, 3)
    if new_level:
        eco.change_wallet(ctx_session, acct, new_level * 50, "level_up")
        extra.append(f"🎉 Level <b>{new_level}</b>! Bonus {eco.fmt(cfg, new_level * 50)}")
    extra += await eco.wealth_achievements(ctx_session, acct)
    return extra
