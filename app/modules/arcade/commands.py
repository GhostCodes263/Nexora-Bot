from __future__ import annotations

import secrets
from html import escape

from app.bot.context import Ctx
from app.bot.keyboards.play import Gm, rows
from app.bot.registry import command
from app.modules.games import logic
from app.modules.games.common import bet_flow, play_allowed, settle
from app.services import economy as eco
from app.services import gamestate

CAT = "games"


def bj_text(state: dict, reveal: bool = False) -> str:
    p, d = state["p"], state["d"]
    dealer = logic.show_hand(d) if reveal else f"{logic.card_name(d[0])} ?"
    dval = logic.hand_value(d) if reveal else "?"
    return (f"🃏 <b>Blackjack</b> · bet {state['bet']:,}\nYou: {logic.show_hand(p)} (<b>{logic.hand_value(p)}</b>)\n"
            f"Dealer: {dealer} ({dval})")


def bj_kb(gid: str):
    return rows([("➕ Hit", Gm(g="bj", id=gid, a="hit")), ("✋ Stand", Gm(g="bj", id=gid, a="stand"))])


@command("blackjack", description="Play blackjack against the dealer", category=CAT, aliases=("bj",),
         usage="/blackjack <bet>", scope="group", cooldown=5)
async def blackjack(ctx: Ctx) -> None:
    flow = await bet_flow(ctx, ctx.args[0] if ctx.args else None)
    if not flow:
        return
    acct, bet = flow
    cfg = eco.eco_cfg(ctx.tenant)  # type: ignore[arg-type]
    eco.change_wallet(ctx.session, acct, -bet, "bet blackjack")  # escrow: returned or paid out at the end
    deck = logic.new_deck()
    state = {"tenant": ctx.tenant.id, "chat": ctx.message.chat.id, "uid": ctx.user_id, "bet": bet,  # type: ignore[union-attr]
             "deck": deck[4:], "p": deck[:2], "d": deck[2:4]}
    if logic.hand_value(state["p"]) == 21:
        dealer_bj = logic.hand_value(state["d"]) == 21
        payout = bet if dealer_bj else bet * 5 // 2
        extra = await settle(ctx.session, acct, cfg, "blackjack", bet, payout, escrowed=True)
        await ctx.reply(bj_text(state, True) + ("\n🤝 Both have blackjack: bet returned." if dealer_bj else f"\n🎉 Blackjack! You win <b>{eco.fmt(cfg, payout - bet)}</b>.")
                        + ("\n" + "\n".join(extra) if extra else ""))
        return
    gid = gamestate.new_id()
    await gamestate.save(ctx.cache, gid, state, 600)
    await ctx.reply(bj_text(state), bj_kb(gid))


@command("highlow", description="Guess if the next card is higher or lower", category=CAT, usage="/highlow <bet>",
         scope="group", cooldown=5)
async def highlow(ctx: Ctx) -> None:
    flow = await bet_flow(ctx, ctx.args[0] if ctx.args else None)
    if not flow:
        return
    acct, bet = flow
    eco.change_wallet(ctx.session, acct, -bet, "bet highlow")
    card = secrets.randbelow(13) + 2
    gid = gamestate.new_id()
    await gamestate.save(ctx.cache, gid, {"tenant": ctx.tenant.id, "chat": ctx.message.chat.id,  # type: ignore[union-attr]
                                          "uid": ctx.user_id, "bet": bet, "card": card}, 600)
    names = {11: "J", 12: "Q", 13: "K", 14: "A"}
    await ctx.reply(f"🔼 The card is <b>{names.get(card, card)}</b>. Is the next one higher or lower? (bet {bet:,})",
                    rows([("⬆️ Higher", Gm(g="hl", id=gid, a="hi")), ("⬇️ Lower", Gm(g="hl", id=gid, a="lo"))]))


@command("cups", description="Find the cup hiding the prize", category=CAT, usage="/cups <bet>", scope="group", cooldown=5)
async def cups(ctx: Ctx) -> None:
    flow = await bet_flow(ctx, ctx.args[0] if ctx.args else None)
    if not flow:
        return
    acct, bet = flow
    eco.change_wallet(ctx.session, acct, -bet, "bet cups")
    gid = gamestate.new_id()
    await gamestate.save(ctx.cache, gid, {"tenant": ctx.tenant.id, "chat": ctx.message.chat.id,  # type: ignore[union-attr]
                                          "uid": ctx.user_id, "bet": bet, "prize": secrets.randbelow(3)}, 600)
    await ctx.reply(f"🥤 Which cup hides the prize? Win 2.7× your bet ({bet:,}).",
                    rows([(f"🥤 {i + 1}", Gm(g="cu", id=gid, a="pick", n=i)) for i in range(3)]))


def ttt_kb(gid: str, board: list[str]):
    sym = {"": "▫️", "X": "❌", "O": "⭕"}
    return rows(*[[(sym[board[r * 3 + c]], Gm(g="tt", id=gid, a="mv", n=r * 3 + c)) for c in range(3)] for r in range(3)])


@command("tictactoe", description="Challenge a member to tic-tac-toe", category=CAT, aliases=("ttt",),
         usage="/tictactoe <reply|@user>", scope="group", cooldown=10)
async def tictactoe(ctx: Ctx) -> None:
    target, _ = await ctx.resolve_target()
    if target is None or target.id in (ctx.user_id, ctx.bot.id) or ctx.anonymous:
        await ctx.reply("Usage: reply to a member with <code>/tictactoe</code> or use <code>/tictactoe @user</code>.")
        return
    gid = gamestate.new_id()
    state = {"tenant": ctx.tenant.id, "chat": ctx.message.chat.id, "board": [""] * 9, "turn": "X",  # type: ignore[union-attr]
             "X": ctx.user_id, "O": target.id, "names": {"X": ctx.user.first_name or "Player 1", "O": target.name}}
    await gamestate.save(ctx.cache, gid, state, 1800)
    await ctx.reply(f"⭕❌ <b>{escape(state['names']['X'])}</b> vs <b>{escape(target.name)}</b>\n"
                    f"{escape(state['names']['X'])}'s turn (❌)", ttt_kb(gid, state["board"]))


@command("duel", description="Bet coins against another member (winner takes the bet)", category=CAT,
         usage="/duel <user> <bet>", examples=("/duel @alex 200",), scope="group", cooldown=15)
async def duel(ctx: Ctx) -> None:
    target, rest = await ctx.resolve_target()
    if ctx.anonymous or target is None or not rest or target.id in (ctx.user_id, ctx.bot.id):
        await ctx.reply("Usage: <code>/duel @user 200</code>")
        return
    flow = await bet_flow(ctx, rest[0])
    if not flow:
        return
    _, bet = flow
    gid = gamestate.new_id()
    await gamestate.save(ctx.cache, gid, {"tenant": ctx.tenant.id, "chat": ctx.message.chat.id,  # type: ignore[union-attr]
                                          "a": ctx.user_id, "b": target.id, "bet": bet,
                                          "names": {"a": ctx.user.first_name or "Challenger", "b": target.name}}, 300)
    await ctx.reply(f"⚔️ <b>{escape(ctx.user.first_name or 'Someone')}</b> challenges <b>{escape(target.name)}</b> to a duel for "
                    f"<b>{eco.fmt(eco.eco_cfg(ctx.tenant), bet)}</b>!\nOnly {escape(target.name)} can accept (5 min).",  # type: ignore[arg-type]
                    rows([("✅ Accept", Gm(g="du", id=gid, a="yes")), ("✖️ Decline", Gm(g="du", id=gid, a="no"))]))


@command("reaction", description="First member to press the button wins a prize", category=CAT, scope="group", cooldown=60)
async def reaction(ctx: Ctx) -> None:
    if not await play_allowed(ctx):
        return
    gid = gamestate.new_id()
    await gamestate.save(ctx.cache, gid, {"tenant": ctx.tenant.id, "chat": ctx.message.chat.id, "reward": 30}, 120)  # type: ignore[union-attr]
    await ctx.reply("⚡ <b>Reaction test!</b> First to press wins 30.", rows([("🖱 PRESS ME", Gm(g="rx", id=gid, a="go"))]))


@command("trivia", description="Answer a quiz question for coins", category=CAT, usage="/trivia [category]",
         examples=("/trivia", "/trivia science"), scope="group", cooldown=15)
async def trivia(ctx: Ctx) -> None:
    cat = ctx.args[0].lower() if ctx.args else None
    pool = [t for t in logic.TRIVIA if cat is None or t[0] == cat]
    if not pool:
        await ctx.reply("Categories: " + ", ".join(f"<code>{c}</code>" for c in logic.TRIVIA_CATEGORIES))
        return
    category, question, correct, wrong = secrets.choice(pool)
    options = [correct, *wrong]
    for i in range(len(options) - 1, 0, -1):  # secure shuffle
        j = secrets.randbelow(i + 1)
        options[i], options[j] = options[j], options[i]
    gid = gamestate.new_id()
    await gamestate.save(ctx.cache, gid, {"tenant": ctx.tenant.id, "chat": ctx.message.chat.id,  # type: ignore[union-attr]
                                          "answer": options.index(correct), "options": options, "locked": [],
                                          "reward": 40, "q": question}, 90)
    await ctx.reply(f"❓ <b>{escape(question)}</b>\n<i>{category} · first correct answer wins 40 · 90 seconds</i>",
                    rows(*[[(opt, Gm(g="tv", id=gid, a="ans", n=i))] for i, opt in enumerate(options)]))
