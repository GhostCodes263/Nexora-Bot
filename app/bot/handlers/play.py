from __future__ import annotations

import logging
import secrets
from html import escape

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.play import Gm, Trade, rows
from app.config.settings import Settings
from app.database.models import Tenant
from app.modules.arcade.commands import bj_kb, bj_text, ttt_kb
from app.modules.games import logic
from app.modules.games.common import settle
from app.repositories import tenants as tenants_repo
from app.services import economy as eco
from app.services import gamestate
from app.services.cache import Cache

log = logging.getLogger(__name__)
router = Router(name="play")
GROUPS = {"group", "supergroup"}


async def _edit(cb: CallbackQuery, text: str, kb=None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise


async def _ctx(cb: CallbackQuery, session: AsyncSession, cache: Cache, game_id: str) -> tuple[dict, Tenant] | None:
    msg = cb.message
    if msg is None or not hasattr(msg, "chat") or msg.chat.type not in GROUPS:  # type: ignore[union-attr]
        await cb.answer("This game has expired.", show_alert=True)
        return None
    state = await gamestate.load(cache, game_id)
    if state is None:
        await cb.answer("This game has expired.", show_alert=True)
        return None
    # The state is bound to the chat it was created in; a button copied elsewhere does nothing.
    if state.get("chat") != msg.chat.id:  # type: ignore[union-attr]
        await cb.answer()
        return None
    tenant = await tenants_repo.get_by_chat_id(session, msg.chat.id)  # type: ignore[union-attr]
    if tenant is None or tenant.id != state.get("tenant"):
        await cb.answer()
        return None
    return state, tenant


@router.callback_query(Gm.filter())
async def on_game(cb: CallbackQuery, callback_data: Gm, session: AsyncSession, cache: Cache) -> None:
    got = await _ctx(cb, session, cache, callback_data.id)
    if got is None:
        return
    state, tenant = got
    cfg = eco.eco_cfg(tenant)
    g, a, n, gid, uid = callback_data.g, callback_data.a, callback_data.n, callback_data.id, cb.from_user.id

    # ---- single-player games: only the player who started them may press -------------------------------
    if g in ("bj", "hl", "cu"):
        if uid != state["uid"]:
            await cb.answer("This isn't your game.", show_alert=True)
            return
        acct = await eco.get_account(session, tenant, uid, lock=True)
        bet = int(state["bet"])
        if g == "hl":
            nxt = secrets.randbelow(13) + 2
            await gamestate.drop(cache, gid)
            cur = state["card"]
            won = (nxt > cur and a == "hi") or (nxt < cur and a == "lo")
            payout = bet * 19 // 10 if won else bet if nxt == cur else 0
            extra = await settle(session, acct, cfg, "highlow", bet, payout, escrowed=True)
            word = "🎉 Correct!" if won else "🤝 Same card, bet returned." if nxt == cur else "❌ Wrong."
            await _edit(cb, f"🔼 {cur} → <b>{nxt}</b>. {word}" + ("\n" + "\n".join(extra) if extra else ""))
        elif g == "cu":
            await gamestate.drop(cache, gid)
            won = n == state["prize"]
            extra = await settle(session, acct, cfg, "cups", bet, bet * 27 // 10 if won else 0, escrowed=True)
            await _edit(cb, ("🎉 You found it! " if won else f"❌ Wrong cup, the prize was under cup {state['prize'] + 1}. ")
                        + ("\n" + "\n".join(extra) if extra else ""))
        else:
            deck, p, d = state["deck"], state["p"], state["d"]
            if a == "hit":
                p.append(deck.pop(0))
                if logic.hand_value(p) > 21:
                    await gamestate.drop(cache, gid)
                    extra = await settle(session, acct, cfg, "blackjack", bet, 0, escrowed=True)
                    await _edit(cb, bj_text(state, True) + "\n💥 Bust! You lose." + ("\n" + "\n".join(extra) if extra else ""))
                else:
                    await gamestate.save(cache, gid, state, 600)
                    await _edit(cb, bj_text(state), bj_kb(gid))
            else:  # stand
                while logic.hand_value(d) < 17:
                    d.append(deck.pop(0))
                await gamestate.drop(cache, gid)
                pv, dv = logic.hand_value(p), logic.hand_value(d)
                payout = bet * 2 if dv > 21 or pv > dv else bet if pv == dv else 0
                extra = await settle(session, acct, cfg, "blackjack", bet, payout, escrowed=True)
                verdict = "🎉 You win!" if payout > bet else "🤝 Push, bet returned." if payout == bet else "❌ Dealer wins."
                await _edit(cb, bj_text(state, True) + f"\n{verdict}" + ("\n" + "\n".join(extra) if extra else ""))
        await cb.answer()
        return

    # ---- riddle ---------------------------------------------------------------------------------------
    if g == "rd":
        await _edit(cb, f"🧩 {escape(state['q'])}\n✅ <b>{escape(state['answer'])}</b>")
        await cb.answer()
        return

    # ---- tic-tac-toe -----------------------------------------------------------------------------------
    if g == "tt":
        turn = state["turn"]
        if uid != state[turn]:
            await cb.answer("It's not your turn." if uid in (state["X"], state["O"]) else "You're not in this game.", show_alert=True)
            return
        board = state["board"]
        if state["board"][n]:
            await cb.answer("That square is taken.")
            return
        board[n] = turn
        result = logic.ttt_winner(board)
        if result is None:
            state["turn"] = "O" if turn == "X" else "X"
            await gamestate.save(cache, gid, state, 1800)
            nm = state["names"][state["turn"]]
            await _edit(cb, f"⭕❌ <b>{escape(state['names']['X'])}</b> vs <b>{escape(state['names']['O'])}</b>\n"
                            f"{escape(nm)}'s turn ({'❌' if state['turn'] == 'X' else '⭕'})", ttt_kb(gid, board))
        else:
            await gamestate.drop(cache, gid)
            if result == "draw":
                for who in ("X", "O"):
                    await eco.record_game(session, tenant.id, state[who], "tictactoe", "draw", 0)
                text = "🤝 It's a draw!"
            else:
                loser = "O" if result == "X" else "X"
                await eco.record_game(session, tenant.id, state[result], "tictactoe", "win", 0)
                await eco.record_game(session, tenant.id, state[loser], "tictactoe", "loss", 0)
                text = f"🏆 <b>{escape(state['names'][result])}</b> wins!"
            await _edit(cb, text, ttt_kb("done", board))
        await cb.answer()
        return

    # ---- duel ----------------------------------------------------------------------------------------------
    if g == "du":
        if a == "no" and uid in (state["a"], state["b"]):
            await gamestate.drop(cache, gid)
            await _edit(cb, "✖️ Duel cancelled.")
            await cb.answer()
            return
        if a != "yes" or uid != state["b"]:
            await cb.answer("Only the challenged member can accept.", show_alert=True)
            return
        await gamestate.drop(cache, gid)
        bet = int(state["bet"])
        accts = {u: await eco.get_account(session, tenant, u, lock=True) for u in sorted((state["a"], state["b"]))}
        if any(x.wallet < bet for x in accts.values()):
            await _edit(cb, "❌ Duel cancelled: someone can't afford the bet any more.")
            await cb.answer()
            return
        winner, loser = (state["a"], state["b"]) if secrets.randbelow(2) == 0 else (state["b"], state["a"])
        eco.change_wallet(session, accts[loser], -bet, "duel_loss")
        eco.change_wallet(session, accts[winner], bet, "duel_win")
        await eco.record_game(session, tenant.id, winner, "duel", "win", bet)
        await eco.record_game(session, tenant.id, loser, "duel", "loss", -bet)
        wn = state["names"]["a" if winner == state["a"] else "b"]
        await _edit(cb, f"⚔️ <b>{escape(wn)}</b> won the duel and takes <b>{eco.fmt(cfg, bet)}</b>!")
        await cb.answer()
        return

    # ---- reaction & trivia: first correct press wins (claimed atomically) -------------------------------------
    if g == "rx":
        if not await cache.set_nx(f"rxwin:{gid}", "1", 300):
            await cb.answer("Too late!")
            return
        await gamestate.drop(cache, gid)
        acct = await eco.get_account(session, tenant, uid, lock=True)
        eco.change_wallet(session, acct, int(state["reward"]), "win reaction")
        await eco.record_game(session, tenant.id, uid, "reaction", "win", int(state["reward"]))
        await _edit(cb, f"⚡ <b>{escape(cb.from_user.first_name)}</b> was fastest! +{eco.fmt(cfg, int(state['reward']))}")
        await cb.answer("You won!")
        return

    if g == "tv":
        if uid in state["locked"]:
            await cb.answer("You already tried this one.", show_alert=True)
            return
        if n != state["answer"]:
            state["locked"].append(uid)
            await gamestate.save(cache, gid, state, 90)
            await cb.answer("❌ Wrong! You're out for this question.", show_alert=True)
            return
        if not await cache.set_nx(f"tvwin:{gid}", "1", 300):
            await cb.answer("Someone was faster!")
            return
        await gamestate.drop(cache, gid)
        acct = await eco.get_account(session, tenant, uid, lock=True)
        eco.change_wallet(session, acct, int(state["reward"]), "win trivia")
        extra = await eco.record_game(session, tenant.id, uid, "trivia", "win", int(state["reward"]))
        eco.add_xp(acct, 10)
        await _edit(cb, f"❓ {escape(state['q'])}\n✅ <b>{escape(state['options'][state['answer']])}</b> — "
                        f"<b>{escape(cb.from_user.first_name)}</b> got it! +{eco.fmt(cfg, int(state['reward']))}"
                        + ("\n" + "\n".join(extra) if extra else ""))
        await cb.answer("Correct! 🎉")
        return
    await cb.answer()


@router.callback_query(Trade.filter())
async def on_trade(cb: CallbackQuery, callback_data: Trade, session: AsyncSession, cache: Cache) -> None:
    key = f"tr{callback_data.id}"
    got = await _ctx(cb, session, cache, key)
    if got is None:
        return
    state, tenant = got
    cfg, uid = eco.eco_cfg(tenant), cb.from_user.id
    if callback_data.a == "no":
        if uid not in (state["seller"], state["buyer"]):
            await cb.answer("Not your trade.", show_alert=True)
            return
        await gamestate.drop(cache, key)
        await _edit(cb, "✖️ Trade cancelled.")
        await cb.answer()
        return
    if uid != state["buyer"]:
        await cb.answer("Only the person this offer is for can accept.", show_alert=True)
        return
    await gamestate.drop(cache, key)  # one-shot: a second tap finds nothing
    item = await session.get(eco.EcoItem, state["item"])
    if item is None or item.tenant_id != tenant.id:
        await _edit(cb, "❌ That item no longer exists.")
        await cb.answer()
        return
    accts = {u: await eco.get_account(session, tenant, u, lock=True) for u in sorted((state["seller"], state["buyer"]))}
    if await eco.item_qty(session, tenant.id, state["seller"], item.id) < state["qty"]:
        await _edit(cb, "❌ The seller no longer has those items.")
        await cb.answer()
        return
    try:
        eco.change_wallet(session, accts[state["buyer"]], -int(state["price"]), "trade_buy")
    except eco.InsufficientFunds:
        await _edit(cb, "❌ The buyer can't afford this any more.")
        await cb.answer()
        return
    eco.change_wallet(session, accts[state["seller"]], int(state["price"]), "trade_sell")
    if not await eco.remove_item(session, tenant.id, state["seller"], item, int(state["qty"])):
        raise RuntimeError("trade inventory changed mid-transaction")  # rolls the whole trade back
    await eco.add_item(session, tenant.id, state["buyer"], item, int(state["qty"]))
    for u in (state["seller"], state["buyer"]):
        await eco.grant(session, tenant.id, u, "trader")
    await _edit(cb, f"🤝 Trade complete: {state['qty']}× {escape(item.name)} for {eco.fmt(cfg, int(state['price']))}.")
    await cb.answer("Done!")
