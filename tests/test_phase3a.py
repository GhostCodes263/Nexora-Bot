from datetime import timedelta

import pytest

from app.bot.loader import load_modules
from app.bot.registry import REGISTRY
from app.modules.entertainment import content
from app.modules.games import logic
from app.modules.games.common import settle
from app.repositories import tenants as tenants_repo
from app.services import economy as eco
from app.utils.time import utcnow


async def _t(session, chat_id=-1):
    return await tenants_repo.get_or_create(session, chat_id, "supergroup", f"T{chat_id}")


# --- amounts, levels ----------------------------------------------------------------------------------
def test_parse_amount():
    assert eco.parse_amount("500", 0) == 500
    assert eco.parse_amount("2.5k", 0) == 2500
    assert eco.parse_amount("1m", 0) == 1_000_000
    assert eco.parse_amount("all", 300) == 300
    assert eco.parse_amount("half", 300) == 150
    assert eco.parse_amount("all", 0) is None
    for bad in ("0", "-5", "abc", "1e9", "10x", "", "999999999999999"):
        assert eco.parse_amount(bad, 100) is None


def test_levels_follow_xp():
    assert eco.level_for(0) == 0 and eco.level_for(99) == 0
    assert eco.level_for(100) == 1 and eco.level_for(400) == 2
    assert eco.xp_for(3) == 900 and eco.level_for(eco.xp_for(7)) == 7


# --- accounts / tenant isolation / transfers --------------------------------------------------------------
async def test_accounts_are_per_tenant(session):
    a, b = await _t(session, -1), await _t(session, -2)
    acct_a = await eco.get_account(session, a, 5)
    acct_b = await eco.get_account(session, b, 5)
    assert acct_a.id != acct_b.id
    eco.change_wallet(session, acct_a, 900, "test")
    assert acct_b.wallet == 100                      # starting balance only: untouched by tenant A
    assert (await eco.get_account(session, a, 5)).id == acct_a.id    # idempotent get


async def test_transfer_moves_coins_and_applies_tax(session):
    t = await _t(session)
    x, y = await eco.get_account(session, t, 1), await eco.get_account(session, t, 2)
    tax = await eco.transfer(session, t, 1, 2, 60, tax_pct=10)
    assert tax == 6 and x.wallet == 40 and y.wallet == 154
    with pytest.raises(eco.InsufficientFunds):
        await eco.transfer(session, t, 1, 2, 500, tax_pct=0)
    assert x.wallet == 40                             # failed transfer changed nothing


async def test_wallet_never_goes_negative(session):
    acct = await eco.get_account(session, await _t(session), 1)
    with pytest.raises(eco.InsufficientFunds):
        eco.change_wallet(session, acct, -101, "x")
    assert acct.wallet == 100


async def test_interest_compounds_per_full_day(session):
    t = await _t(session)
    acct = await eco.get_account(session, t, 1)
    acct.bank, acct.last_interest = 1000, utcnow() - timedelta(days=3, hours=1)
    gained = eco.apply_interest(acct, {"interest_pct": 1})
    assert gained == 30 and acct.bank == 1030        # 1000 -> 1010 -> 1020 -> 1030
    assert eco.apply_interest(acct, {"interest_pct": 1}) == 0   # same day: nothing more


async def test_xp_levels_up():
    from app.database.models import EcoAccount

    acct = EcoAccount(tenant_id=1, user_id=1, wallet=0, bank=0, xp=90, streak=0)
    assert eco.add_xp(acct, 5) is None
    assert eco.add_xp(acct, 20) == 1


# --- items ------------------------------------------------------------------------------------------------------
async def test_default_items_seeded_once_and_inventory_works(session):
    t = await _t(session)
    await eco.ensure_default_items(session, t)
    await eco.ensure_default_items(session, t)
    items = await eco.list_items(session, t.id)
    assert {i.name for i in items} == {"padlock", "laptop", "potion", "trophy"}
    lock = await eco.find_item(session, t.id, "PADLOCK")
    await eco.add_item(session, t.id, 1, lock, 2)
    assert await eco.item_qty(session, t.id, 1, lock.id) == 2
    assert await eco.consume_effect(session, t.id, 1, "padlock")
    assert await eco.effect_qty(session, t.id, 1, "padlock") == 1
    assert not await eco.remove_item(session, t.id, 1, lock, 5)
    other = await _t(session, -2)
    assert await eco.effect_qty(session, other.id, 1, "padlock") == 0   # tenant B sees nothing


# --- achievements & stats ------------------------------------------------------------------------------------------
async def test_achievements_are_granted_once(session):
    t = await _t(session)
    assert await eco.grant(session, t.id, 1, "trader")
    assert await eco.grant(session, t.id, 1, "trader") is None


async def test_game_stats_and_gambler_achievement(session):
    t = await _t(session)
    msgs = []
    for i in range(100):
        msgs += await eco.record_game(session, t.id, 1, "dice", "win" if i % 2 else "loss", 10 if i % 2 else -10)
    assert any("Gambler" in m for m in msgs)
    from sqlalchemy import select

    from app.database.models import GameStat
    stat = (await session.execute(select(GameStat))).scalars().one()
    assert stat.plays == 100 and stat.wins == 50 and stat.losses == 50 and stat.net == 0


async def test_settle_charges_pays_and_records(session):
    t = await _t(session)
    acct = await eco.get_account(session, t, 1)
    cfg = eco.eco_cfg(t)
    await settle(session, acct, cfg, "dice", 40, 80)
    assert acct.wallet == 100 + 40               # -40 bet, +80 payout
    await settle(session, acct, cfg, "dice", 40, 0)
    assert acct.wallet == 140 - 40               # loss
    acct.wallet = 100
    await settle(session, acct, cfg, "blackjack", 50, 100, escrowed=True)   # bet already taken earlier
    assert acct.wallet == 200


# --- pure game rules ----------------------------------------------------------------------------------------------
def test_slots_payouts():
    assert logic.slots_multiplier_x10(["💎"] * 3) == 100
    assert logic.slots_multiplier_x10(["🍒"] * 3) == 40
    assert logic.slots_multiplier_x10(["🍒", "🍒", "🍋"]) == 15
    assert logic.slots_multiplier_x10(["🍒", "🍋", "🍇"]) == 0


def test_roulette_rules():
    assert logic.roulette_multiplier("red", 1) == 2 and logic.roulette_multiplier("red", 2) == 0
    assert logic.roulette_multiplier("black", 2) == 2
    assert logic.roulette_multiplier("even", 0) == 0 and logic.roulette_multiplier("odd", 3) == 2
    assert logic.roulette_multiplier("17", 17) == 36 and logic.roulette_multiplier("17", 18) == 0
    assert logic.roulette_multiplier("green", 0) == 14
    assert logic.roulette_multiplier("purple", 5) == -1


def test_rps_and_blackjack_and_ttt():
    assert logic.rps_result("rock", "scissors") == "win" and logic.rps_result("rock", "paper") == "loss"
    assert logic.rps_result("paper", "paper") == "draw"
    assert logic.hand_value([1, 13]) == 21 and logic.hand_value([1, 1, 9]) == 21 and logic.hand_value([10, 10, 5]) == 25
    assert len(logic.new_deck()) == 52
    assert logic.ttt_winner(["X", "X", "X", "", "", "", "", "", ""]) == "X"
    assert logic.ttt_winner(["X", "O", "X", "X", "O", "O", "O", "X", "X"]) == "draw"
    assert logic.ttt_winner([""] * 9) is None


def test_word_games_helpers():
    assert logic.hangman_mask("cat", {"c", "t"}) == "c _ t"
    word = "planet"
    assert logic.scramble(word) != word and sorted(logic.scramble(word)) == sorted(word)
    problem, answer = logic.math_problem()
    assert eval(problem) == answer  # noqa: S307  (the string is generated by us: digits and + - *)


def test_trivia_bank_is_well_formed():
    assert len(logic.TRIVIA) >= 30
    for cat, q, correct, wrong in logic.TRIVIA:
        assert q.endswith("?") and len(wrong) == 3 and correct not in wrong and len(set(wrong)) == 3, q


# --- content & registry ---------------------------------------------------------------------------------------------------
def test_entertainment_content_present():
    for name in ("JOKES", "DAD_JOKES", "QUOTES", "FACTS", "WOULD_YOU_RATHER", "TRUTHS", "DARES", "EIGHT_BALL",
                 "PICKUP", "ROASTS", "COMPLIMENTS", "ADVICE", "FORTUNES", "HOROSCOPE", "RIDDLES", "MEMES"):
        assert len(getattr(content, name)) >= 8, name
    assert all("{name}" in r for r in content.ROASTS + content.COMPLIMENTS)
    assert all(ch in content.FLIP_MAP for ch in "abcdefghijklmnopqrstuvwxyz")


def test_command_counts_and_economy_is_toggleable():
    load_modules()
    assert len(REGISTRY.all()) >= 230
    by_cat = REGISTRY.by_category()
    assert len(by_cat["economy"]) >= 30 and len(by_cat["games"]) >= 25 and len(by_cat["entertainment"]) >= 30
    from app.services import tenants as tenant_service
    from app.database.models import Tenant

    t = Tenant(chat_id=1, chat_type="group", module_overrides={"economy": False}, settings={})
    assert not tenant_service.is_enabled(t, REGISTRY.get("daily"))
    assert tenant_service.is_enabled(t, REGISTRY.get("joke"))
