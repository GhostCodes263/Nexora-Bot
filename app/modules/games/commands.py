from __future__ import annotations

import json
import secrets
from html import escape

from sqlalchemy import func, select

from app.bot.context import Ctx
from app.bot.registry import command
from app.database.models import GameStat, LotteryTicket
from app.modules.games import logic
from app.modules.games.common import bet_flow, play_allowed, settle
from app.repositories import users as users_repo
from app.services import economy as eco
from app.services.roles import Role

CAT = "games"
GAME_LIST = [("coinflip", "Heads or tails"), ("dice", "Roll against the bot"), ("slots", "Three reels"),
             ("roulette", "Red, black or a number"), ("rps", "Rock paper scissors"), ("blackjack", "Beat the dealer"),
             ("highlow", "Higher or lower"), ("cups", "Find the prize"), ("tictactoe", "Challenge a member"),
             ("duel", "Bet against a member"), ("trivia", "Quiz questions"), ("hangman", "Guess the word"),
             ("numbergame", "Guess the number"), ("scramble", "Unscramble the word"), ("mathgame", "Quick maths"),
             ("reaction", "First to click wins"), ("lottery", "Pool tickets, one winner"), ("spin", "Free prize wheel")]


def _tail(extra: list[str]) -> str:
    return ("\n" + "\n".join(extra)) if extra else ""


@command("games", description="List all games", category=CAT)
async def games(ctx: Ctx) -> None:
    await ctx.reply("<b>🎮 Games</b>\nBets use the group's virtual currency (no real money).\n" +
                    "\n".join(f"• /{n} — {d}" for n, d in GAME_LIST))


@command("coinflip", description="Bet on heads or tails", category=CAT, aliases=("cf",),
         usage="/coinflip <bet> [heads|tails]", examples=("/coinflip 100 heads",), scope="group", cooldown=3)
async def coinflip(ctx: Ctx) -> None:
    flow = await bet_flow(ctx, ctx.args[0] if ctx.args else None)
    if not flow:
        return
    acct, bet = flow
    cfg = eco.eco_cfg(ctx.tenant)  # type: ignore[arg-type]
    pick = ctx.args[1].lower() if len(ctx.args) > 1 and ctx.args[1].lower() in ("heads", "tails") else "heads"
    result = "heads" if secrets.randbelow(2) == 0 else "tails"
    won = result == pick
    extra = await settle(ctx.session, acct, cfg, "coinflip", bet, bet * 2 if won else 0)
    await ctx.reply(f"🪙 It's <b>{result}</b>! " + (f"You won <b>{eco.fmt(cfg, bet)}</b>." if won else f"You lost {eco.fmt(cfg, bet)}.") + _tail(extra))


@command("dice", description="Roll two dice against the bot", category=CAT, usage="/dice <bet>", scope="group", cooldown=3)
async def dice(ctx: Ctx) -> None:
    flow = await bet_flow(ctx, ctx.args[0] if ctx.args else None)
    if not flow:
        return
    acct, bet = flow
    cfg = eco.eco_cfg(ctx.tenant)  # type: ignore[arg-type]
    you = sum(secrets.randbelow(6) + 1 for _ in range(2))
    bot = sum(secrets.randbelow(6) + 1 for _ in range(2))
    payout = bet * 2 if you > bot else bet if you == bot else 0
    extra = await settle(ctx.session, acct, cfg, "dice", bet, payout)
    verdict = "You win!" if you > bot else "Draw, bet returned." if you == bot else "The bot wins."
    await ctx.reply(f"🎲 You rolled <b>{you}</b>, bot rolled <b>{bot}</b>. {verdict}" + _tail(extra))


@command("slots", description="Spin the slot machine", category=CAT, usage="/slots <bet>", scope="group", cooldown=4)
async def slots(ctx: Ctx) -> None:
    flow = await bet_flow(ctx, ctx.args[0] if ctx.args else None)
    if not flow:
        return
    acct, bet = flow
    cfg = eco.eco_cfg(ctx.tenant)  # type: ignore[arg-type]
    reels = logic.spin_slots()
    payout = bet * logic.slots_multiplier_x10(reels) // 10
    extra = await settle(ctx.session, acct, cfg, "slots", bet, payout)
    outcome = f"won <b>{eco.fmt(cfg, payout)}</b>" if payout > bet else "got your bet back" if payout == bet else "lost"
    await ctx.reply(f"🎰 | {' | '.join(reels)} |\nYou {outcome}." + _tail(extra))


@command("roulette", description="Bet on red, black, even, odd, green or a number", category=CAT,
         usage="/roulette <bet> <red|black|even|odd|green|0-36>", examples=("/roulette 200 red",), scope="group", cooldown=4)
async def roulette(ctx: Ctx) -> None:
    if len(ctx.args) < 2 or logic.roulette_multiplier(ctx.args[1], 99) == -1:
        await ctx.reply("Usage: <code>/roulette 200 red</code> (red, black, even, odd, green or 0-36)")
        return
    flow = await bet_flow(ctx, ctx.args[0])
    if not flow:
        return
    acct, bet = flow
    cfg = eco.eco_cfg(ctx.tenant)  # type: ignore[arg-type]
    n = secrets.randbelow(37)
    mult = logic.roulette_multiplier(ctx.args[1], n)
    extra = await settle(ctx.session, acct, cfg, "roulette", bet, bet * mult)
    color = {"red": "🔴", "black": "⚫️", "green": "🟢"}[logic.roulette_color(n)]
    await ctx.reply(f"🎡 The ball lands on {color} <b>{n}</b>. " + (f"You won <b>{eco.fmt(cfg, bet * (mult - 1))}</b>!" if mult else f"You lost {eco.fmt(cfg, bet)}.") + _tail(extra))


@command("rps", description="Rock paper scissors against the bot (optional bet)", category=CAT,
         usage="/rps <rock|paper|scissors> [bet]", scope="group", cooldown=3)
async def rps(ctx: Ctx) -> None:
    pick = ctx.args[0].lower() if ctx.args else ""
    pick = {"r": "rock", "p": "paper", "s": "scissors"}.get(pick, pick)
    if pick not in ("rock", "paper", "scissors"):
        await ctx.reply("Usage: <code>/rps rock</code> (optionally add a bet: <code>/rps rock 50</code>)")
        return
    bot = secrets.choice(["rock", "paper", "scissors"])
    res = logic.rps_result(pick, bot)
    icon = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    text = f"You: {icon[pick]}  Bot: {icon[bot]} — " + {"win": "You win!", "loss": "Bot wins.", "draw": "Draw."}[res]
    if len(ctx.args) > 1:
        flow = await bet_flow(ctx, ctx.args[1])
        if not flow:
            return
        acct, bet = flow
        cfg = eco.eco_cfg(ctx.tenant)  # type: ignore[arg-type]
        payout = {"win": bet * 2, "draw": bet, "loss": 0}[res]
        text += _tail(await settle(ctx.session, acct, cfg, "rps", bet, payout))
    await ctx.reply(text)


@command("spin", description="Spin the free prize wheel (every 6 hours)", category=CAT, scope="group", cooldown=21600)
async def spin(ctx: Ctx) -> None:
    if ctx.anonymous or not await play_allowed(ctx):
        return
    cfg = eco.eco_cfg(ctx.tenant)  # type: ignore[arg-type]
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    prizes, weights = [10, 25, 50, 100, 250, 500], [30, 28, 20, 14, 6, 2]
    roll, acc = secrets.randbelow(sum(weights)), 0
    for prize, w in zip(prizes, weights, strict=True):
        acc += w
        if roll < acc:
            break
    eco.change_wallet(ctx.session, acct, prize, "wheel")
    await ctx.reply(f"🎡 The wheel stops on <b>{eco.fmt(cfg, prize)}</b>!")


# --- puzzle games (one active puzzle per chat) ------------------------------------------------------------------

async def _put(ctx: Ctx, key: str, data: dict, ttl: int = 600) -> None:
    await ctx.cache.set(f"{key}:{ctx.message.chat.id}", json.dumps(data), ttl)


async def _get(ctx: Ctx, key: str) -> dict | None:
    raw = await ctx.cache.get(f"{key}:{ctx.message.chat.id}")
    return json.loads(raw) if raw else None


async def _reward(ctx: Ctx, amount: int, game: str) -> list[str]:
    acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
    eco.change_wallet(ctx.session, acct, amount, f"win {game}")
    return await eco.record_game(ctx.session, acct.tenant_id, ctx.user_id, game, "win", amount)


@command("scramble", description="Unscramble a word (first correct /answer wins)", category=CAT, scope="group", cooldown=20)
async def scramble(ctx: Ctx) -> None:
    if await _get(ctx, "puz"):
        await ctx.reply("A puzzle is already running here. Solve it with <code>/answer</code>!")
        return
    word = secrets.choice(logic.WORDS)
    await _put(ctx, "puz", {"type": "scramble", "answer": word, "reward": 60})
    await ctx.reply(f"🔤 Unscramble: <b>{logic.scramble(word).upper()}</b>\nAnswer with <code>/answer word</code> — prize 60.")


@command("mathgame", description="Solve a quick sum (first correct /answer wins)", category=CAT, scope="group", cooldown=20)
async def mathgame(ctx: Ctx) -> None:
    if await _get(ctx, "puz"):
        await ctx.reply("A puzzle is already running here. Solve it with <code>/answer</code>!")
        return
    problem, answer = logic.math_problem()
    await _put(ctx, "puz", {"type": "math", "answer": str(answer), "reward": 40})
    await ctx.reply(f"🧮 What is <b>{problem}</b>?\nAnswer with <code>/answer 42</code> — prize 40.")


@command("answer", description="Answer the running scramble or maths puzzle", category=CAT, usage="/answer <your answer>", scope="group")
async def answer(ctx: Ctx) -> None:
    puz = await _get(ctx, "puz")
    if puz is None:
        await ctx.reply("There's no puzzle running. Start one with /scramble or /mathgame.")
        return
    if ctx.anonymous or ctx.raw_args.strip().lower() != str(puz["answer"]).lower():
        await ctx.reply("❌ Not quite. Try again!")
        return
    await ctx.cache.delete(f"puz:{ctx.message.chat.id}")
    extra = await _reward(ctx, int(puz["reward"]), puz["type"])
    await ctx.reply(f"✅ Correct, <b>{escape(ctx.user.first_name or 'you')}</b>! +{eco.fmt(eco.eco_cfg(ctx.tenant), int(puz['reward']))}" + _tail(extra))  # type: ignore[arg-type]


@command("numbergame", description="Start a number guessing game (1-100)", category=CAT, scope="group", cooldown=20)
async def numbergame(ctx: Ctx) -> None:
    if await _get(ctx, "num"):
        await ctx.reply("A number game is already running. Use <code>/guessnum 50</code>!")
        return
    await _put(ctx, "num", {"n": secrets.randbelow(100) + 1, "tries": 0}, 900)
    await ctx.reply("🔢 I'm thinking of a number from 1 to 100. Guess with <code>/guessnum 50</code>. Fewer tries = bigger prize!")


@command("guessnum", description="Guess the number in the running game", category=CAT, usage="/guessnum <1-100>", scope="group")
async def guessnum(ctx: Ctx) -> None:
    game = await _get(ctx, "num")
    if game is None:
        await ctx.reply("No number game is running. Start one with /numbergame.")
        return
    if not ctx.args or not ctx.args[0].isdigit() or not 1 <= int(ctx.args[0]) <= 100:
        await ctx.reply("Guess a number from 1 to 100.")
        return
    guess = int(ctx.args[0])
    game["tries"] += 1
    if guess != game["n"]:
        await _put(ctx, "num", game, 900)
        await ctx.reply("⬆️ Higher!" if guess < game["n"] else "⬇️ Lower!")
        return
    await ctx.cache.delete(f"num:{ctx.message.chat.id}")
    prize = max(20, 100 - game["tries"] * 5)
    extra = await _reward(ctx, prize, "numbergame") if not ctx.anonymous else []
    await ctx.reply(f"🎉 <b>{escape(ctx.user.first_name or 'You')}</b> got it in {game['tries']} tries! +{eco.fmt(eco.eco_cfg(ctx.tenant), prize)}" + _tail(extra))  # type: ignore[arg-type]


HANG = ["😀", "🙂", "😐", "😟", "😨", "😱", "💀"]


@command("hangman", description="Start (or show) a hangman game for the chat", category=CAT, scope="group", cooldown=10)
async def hangman(ctx: Ctx) -> None:
    game = await _get(ctx, "hang")
    if game is None:
        game = {"word": secrets.choice(logic.WORDS), "guessed": [], "wrong": 0}
        await _put(ctx, "hang", game, 1200)
    await ctx.reply(_hang_text(game) + "\nGuess with <code>/guess a</code> or the whole word.")


def _hang_text(game: dict) -> str:
    return (f"{HANG[min(game['wrong'], 6)]} <code>{logic.hangman_mask(game['word'], set(game['guessed']))}</code>\n"
            f"Wrong guesses: {game['wrong']}/6 · Used: {' '.join(sorted(game['guessed'])) or '-'}")


@command("guess", description="Guess a letter or the word in hangman", category=CAT, usage="/guess <letter or word>", scope="group")
async def guess(ctx: Ctx) -> None:
    game = await _get(ctx, "hang")
    g = ctx.raw_args.strip().lower()
    if game is None or not g.isalpha():
        await ctx.reply("Start a game with /hangman, then <code>/guess a</code>.")
        return
    if len(g) > 1:
        if g == game["word"]:
            game["guessed"] = list(set(game["word"]))
        else:
            game["wrong"] += 1
    elif g in game["guessed"]:
        await ctx.reply("You already tried that letter.")
        return
    else:
        game["guessed"].append(g)
        if g not in game["word"]:
            game["wrong"] += 1
    solved = all(ch in game["guessed"] for ch in game["word"])
    if solved:
        await ctx.cache.delete(f"hang:{ctx.message.chat.id}")
        extra = await _reward(ctx, 60, "hangman") if not ctx.anonymous else []
        await ctx.reply(f"🎉 The word was <b>{game['word']}</b>! {escape(ctx.user.first_name or '')} solved it. +{eco.fmt(eco.eco_cfg(ctx.tenant), 60)}" + _tail(extra))  # type: ignore[arg-type]
    elif game["wrong"] >= 6:
        await ctx.cache.delete(f"hang:{ctx.message.chat.id}")
        await ctx.reply(f"💀 Game over! The word was <b>{game['word']}</b>.")
    else:
        await _put(ctx, "hang", game, 1200)
        await ctx.reply(_hang_text(game))


# --- lottery -----------------------------------------------------------------------------------------------------------

@command("lottery", description="Buy tickets for the group lottery", category=CAT, usage="/lottery [buy <tickets>]",
         scope="group")
async def lottery(ctx: Ctx) -> None:
    cfg = eco.eco_cfg(ctx.tenant)  # type: ignore[arg-type]
    tid = ctx.tenant.id  # type: ignore[union-attr]
    if len(ctx.args) >= 2 and ctx.args[0] == "buy" and ctx.args[1].isdigit() and 1 <= int(ctx.args[1]) <= 100:
        if ctx.anonymous:
            return
        n, price = int(ctx.args[1]), int(cfg["lottery_price"])
        acct = await eco.get_account(ctx.session, ctx.tenant, ctx.user_id, lock=True)  # type: ignore[arg-type]
        try:
            eco.change_wallet(ctx.session, acct, -n * price, "lottery")
        except eco.InsufficientFunds:
            await ctx.reply(f"You need {eco.fmt(cfg, n * price)} for {n} ticket(s).")
            return
        row = (await ctx.session.execute(select(LotteryTicket).where(
            LotteryTicket.tenant_id == tid, LotteryTicket.user_id == ctx.user_id).with_for_update())).scalars().first()
        if row is None:
            ctx.session.add(LotteryTicket(tenant_id=tid, user_id=ctx.user_id, tickets=n))
        else:
            row.tickets += n
        await ctx.reply(f"🎟 Bought {n} ticket(s) for {eco.fmt(cfg, n * price)}.")
        return
    total = int((await ctx.session.execute(select(func.coalesce(func.sum(LotteryTicket.tickets), 0)).where(LotteryTicket.tenant_id == tid))).scalar_one())
    mine = (await ctx.session.execute(select(LotteryTicket.tickets).where(
        LotteryTicket.tenant_id == tid, LotteryTicket.user_id == ctx.user_id))).scalars().first() or 0
    await ctx.reply(f"🎟 <b>Lottery</b>\nTicket price: {eco.fmt(cfg, int(cfg['lottery_price']))}\nPot: <b>{eco.fmt(cfg, total * int(cfg['lottery_price']))}</b> "
                    f"({total} tickets, yours: {mine})\nBuy with <code>/lottery buy 5</code>. An admin draws the winner with /drawlottery.")


@command("drawlottery", description="Draw the lottery winner and pay the pot", category=CAT,
         permission=Role.ADMIN, scope="group")
async def drawlottery(ctx: Ctx) -> None:
    cfg = eco.eco_cfg(ctx.tenant)  # type: ignore[arg-type]
    tid = ctx.tenant.id  # type: ignore[union-attr]
    rows = (await ctx.session.execute(select(LotteryTicket).where(LotteryTicket.tenant_id == tid).with_for_update())).scalars().all()
    total = sum(r.tickets for r in rows)
    if total == 0:
        await ctx.reply("Nobody has bought a ticket yet.")
        return
    pick, acc, winner = secrets.randbelow(total), 0, rows[0]
    for r in rows:
        acc += r.tickets
        if pick < acc:
            winner = r
            break
    pot = total * int(cfg["lottery_price"])
    acct = await eco.get_account(ctx.session, ctx.tenant, winner.user_id, lock=True)  # type: ignore[arg-type]
    eco.change_wallet(ctx.session, acct, pot, "lottery_win")
    for r in rows:
        await ctx.session.delete(r)
    u = await users_repo.get_user(ctx.session, winner.user_id)
    await ctx.reply(f'🎉 <a href="tg://user?id={winner.user_id}">{escape((u.first_name if u else "") or "The winner")}</a> '
                    f"won the lottery pot of <b>{eco.fmt(cfg, pot)}</b>!")


# --- statistics ------------------------------------------------------------------------------------------------------------------

@command("gamestats", description="Your game statistics", category=CAT, usage="/gamestats [user]", scope="group")
async def gamestats(ctx: Ctx) -> None:
    target, _ = await ctx.resolve_target()
    uid = target.id if target else ctx.user_id
    rows = (await ctx.session.execute(select(GameStat).where(
        GameStat.tenant_id == ctx.tenant.id, GameStat.user_id == uid).order_by(GameStat.plays.desc()))).scalars().all()  # type: ignore[union-attr]
    if not rows:
        await ctx.reply("No games played yet. See /games.")
        return
    cfg = eco.eco_cfg(ctx.tenant)  # type: ignore[arg-type]
    await ctx.reply("<b>📈 Game stats</b>\n" + "\n".join(
        f"• {r.game}: {r.plays} played, {r.wins} won, {r.losses} lost, net {'+' if r.net >= 0 else ''}{r.net:,}" for r in rows)
        + f"\n<i>Amounts are in {escape(str(cfg['currency']))}.</i>")


@command("gametop", description="Top players by wins", category=CAT, usage="/gametop [game]", scope="group")
async def gametop(ctx: Ctx) -> None:
    game = ctx.args[0].lower() if ctx.args else None
    q = select(GameStat.user_id, func.sum(GameStat.wins).label("w"), func.sum(GameStat.plays)).where(
        GameStat.tenant_id == ctx.tenant.id).group_by(GameStat.user_id).order_by(func.sum(GameStat.wins).desc()).limit(10)  # type: ignore[union-attr]
    if game:
        q = q.where(GameStat.game == game)
    rows = (await ctx.session.execute(q)).all()
    if not rows:
        await ctx.reply("No games recorded yet.")
        return
    lines = [f"<b>🏆 Top players{' · ' + escape(game) if game else ''}</b>"]
    for n, (uid, wins, plays) in enumerate(rows, 1):
        u = await users_repo.get_user(ctx.session, uid)
        lines.append(f"{n}. {escape((u.first_name if u else '') or str(uid))} — {wins} wins / {plays} plays")
    await ctx.reply("\n".join(lines))
