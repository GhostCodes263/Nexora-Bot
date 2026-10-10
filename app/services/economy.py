from __future__ import annotations

import math
import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Achievement,
    EcoAccount,
    EcoInventory,
    EcoItem,
    EcoLedger,
    GameStat,
    Tenant,
)
from app.utils.time import aware, utcnow

DEFAULT_ECO: dict = {
    "currency": "coins",
    "symbol": "🪙",
    "daily": 200,
    "weekly": 1500,
    "work_min": 40,
    "work_max": 160,
    "beg_max": 50,
    "rob_enabled": True,
    "rob_chance": 35,
    "transfer_tax_pct": 0,
    "max_bet": 10_000,
    "interest_pct": 1,
    "starting_balance": 100,
    "xp_min": 5,
    "xp_max": 10,
    "lottery_price": 100,
}
# Admin-editable keys and their allowed ranges (everything is validated; nothing is trusted).
CFG_RULES: dict[str, tuple[type, int, int]] = {
    "daily": (int, 1, 1_000_000), "weekly": (int, 1, 10_000_000), "work_min": (int, 1, 1_000_000),
    "work_max": (int, 1, 1_000_000), "beg_max": (int, 1, 100_000), "rob_chance": (int, 5, 90),
    "transfer_tax_pct": (int, 0, 50), "max_bet": (int, 1, 10_000_000), "interest_pct": (int, 0, 10),
    "starting_balance": (int, 0, 1_000_000), "xp_min": (int, 0, 1000), "xp_max": (int, 0, 1000),
    "lottery_price": (int, 1, 1_000_000), "rob_enabled": (bool, 0, 1),
}
MAX_AMOUNT = 10**12
DEFAULT_ITEMS = [
    ("padlock", 500, "Blocks the next robbery attempt against you (used up).", "padlock"),
    ("laptop", 2000, "Boosts your /work earnings by 25%.", "work_boost"),
    ("potion", 750, "Use it for +200 XP.", "xp_potion"),
    ("trophy", 10_000, "A shiny collectible. Pure bragging rights.", "none"),
]
ACHIEVEMENTS: dict[str, tuple[str, str, str]] = {
    "first_daily": ("🌅", "Early bird", "Claim your first daily reward"),
    "streak7": ("🔥", "On fire", "Reach a 7-day daily streak"),
    "rich": ("💰", "Rich", "Hold 10,000 in total"),
    "millionaire": ("💎", "Millionaire", "Hold 1,000,000 in total"),
    "gambler": ("🎲", "Gambler", "Play 100 games"),
    "winner": ("🏆", "Winner", "Win 50 games"),
    "level5": ("⭐", "Rising star", "Reach level 5"),
    "level10": ("🌟", "Veteran", "Reach level 10"),
    "robber": ("🦹", "Master thief", "Pull off a robbery"),
    "trader": ("🤝", "Trader", "Complete a trade"),
}


class InsufficientFunds(Exception):
    pass


def eco_cfg(tenant: Tenant) -> dict:
    return {**DEFAULT_ECO, **(tenant.settings or {}).get("eco", {})}


def save_eco_cfg(tenant: Tenant, cfg: dict) -> None:
    tenant.settings = {**(tenant.settings or {}), "eco": cfg}  # reassign so the JSON change is saved


def fmt(cfg: dict, n: int) -> str:
    return f"{cfg['symbol']} {n:,}"


def level_for(xp: int) -> int:
    return math.isqrt(max(xp, 0) // 100)


def xp_for(level: int) -> int:
    return 100 * level * level


def networth(a: EcoAccount) -> int:
    return a.wallet + a.bank


def parse_amount(text: str, balance: int) -> int | None:
    """'500', '2.5k', '1m', 'all', 'half' -> positive int, or None if invalid."""
    t = text.strip().lower().replace(",", "")
    if t == "all":
        return balance if 0 < balance <= MAX_AMOUNT else None
    if t == "half":
        return balance // 2 if balance // 2 > 0 else None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb]?)", t)
    if not m:
        return None
    value = int(float(m.group(1)) * {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[m.group(2)])
    return value if 0 < value <= MAX_AMOUNT else None


# --- accounts ----------------------------------------------------------------------------------------

async def get_account(session: AsyncSession, tenant: Tenant, user_id: int, *, lock: bool = False) -> EcoAccount:
    """Fetch (or create) the account of `user_id` in THIS tenant. lock=True takes a row lock so two
    simultaneous commands can't both spend the same coins."""
    q = select(EcoAccount).where(EcoAccount.tenant_id == tenant.id, EcoAccount.user_id == user_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    acct = (await session.execute(q)).scalars().first()
    if acct is not None:
        return acct
    start = int(eco_cfg(tenant)["starting_balance"])
    acct = EcoAccount(tenant_id=tenant.id, user_id=user_id, wallet=start, bank=0, xp=0, streak=0)
    try:
        async with session.begin_nested():
            session.add(acct)
            await session.flush()
        if start:
            session.add(EcoLedger(tenant_id=tenant.id, user_id=user_id, delta=start, reason="starting_balance"))
    except IntegrityError:
        acct = (await session.execute(q)).scalars().first()  # another request created it first
        assert acct is not None
    return acct


def change_wallet(session: AsyncSession, acct: EcoAccount, delta: int, reason: str) -> None:
    if acct.wallet + delta < 0:
        raise InsufficientFunds
    acct.wallet += delta
    session.add(EcoLedger(tenant_id=acct.tenant_id, user_id=acct.user_id, delta=delta, reason=reason[:48]))


async def transfer(session: AsyncSession, tenant: Tenant, from_id: int, to_id: int, amount: int, tax_pct: int) -> int:
    """Move coins between two members of the same tenant. Locks rows in a fixed order (no deadlocks)."""
    accounts = {}
    for uid in sorted((from_id, to_id)):
        accounts[uid] = await get_account(session, tenant, uid, lock=True)
    tax = amount * tax_pct // 100
    change_wallet(session, accounts[from_id], -amount, "transfer_out")
    change_wallet(session, accounts[to_id], amount - tax, "transfer_in")
    return tax


def apply_interest(acct: EcoAccount, cfg: dict) -> int:
    """Lazy compound interest on the bank balance (1 step per full day, at most 30). Returns the gain."""
    now = utcnow()
    pct = int(cfg["interest_pct"])
    if acct.bank <= 0 or pct <= 0:
        acct.last_interest = now
        return 0
    last = aware(acct.last_interest) or now
    days = min(int((now - last).total_seconds() // 86400), 30)
    if days < 1:
        if acct.last_interest is None:
            acct.last_interest = now
        return 0
    before = acct.bank
    for _ in range(days):
        acct.bank += acct.bank * pct // 100
    acct.last_interest = last + timedelta(days=days)
    return acct.bank - before


def add_xp(acct: EcoAccount, amount: int) -> int | None:
    """Add XP; returns the new level if the member levelled up."""
    old = level_for(acct.xp)
    acct.xp += max(amount, 0)
    new = level_for(acct.xp)
    return new if new > old else None


# --- items -------------------------------------------------------------------------------------------------

async def ensure_default_items(session: AsyncSession, tenant: Tenant) -> None:
    if (tenant.settings or {}).get("eco_items_seeded"):
        return
    for name, price, desc, effect in DEFAULT_ITEMS:
        session.add(EcoItem(tenant_id=tenant.id, name=name, price=price, description=desc, effect=effect))
    tenant.settings = {**(tenant.settings or {}), "eco_items_seeded": True}
    await session.flush()


async def find_item(session: AsyncSession, tenant_id: int, name: str) -> EcoItem | None:
    res = await session.execute(select(EcoItem).where(EcoItem.tenant_id == tenant_id, EcoItem.name == name.lower().strip()))
    return res.scalars().first()


async def list_items(session: AsyncSession, tenant_id: int) -> list[EcoItem]:
    return list((await session.execute(select(EcoItem).where(EcoItem.tenant_id == tenant_id).order_by(EcoItem.price))).scalars())


async def _inv_row(session: AsyncSession, tenant_id: int, user_id: int, item_id: int, lock: bool = False) -> EcoInventory | None:
    q = select(EcoInventory).where(EcoInventory.tenant_id == tenant_id, EcoInventory.user_id == user_id,
                                   EcoInventory.item_id == item_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(q)).scalars().first()


async def add_item(session: AsyncSession, tenant_id: int, user_id: int, item: EcoItem, qty: int) -> None:
    row = await _inv_row(session, tenant_id, user_id, item.id, lock=True)
    if row is None:
        session.add(EcoInventory(tenant_id=tenant_id, user_id=user_id, item_id=item.id, qty=qty))
    else:
        row.qty += qty


async def remove_item(session: AsyncSession, tenant_id: int, user_id: int, item: EcoItem, qty: int) -> bool:
    row = await _inv_row(session, tenant_id, user_id, item.id, lock=True)
    if row is None or row.qty < qty:
        return False
    row.qty -= qty
    if row.qty == 0:
        await session.delete(row)
    return True


async def item_qty(session: AsyncSession, tenant_id: int, user_id: int, item_id: int) -> int:
    row = await _inv_row(session, tenant_id, user_id, item_id)
    return row.qty if row else 0


async def effect_qty(session: AsyncSession, tenant_id: int, user_id: int, effect: str) -> int:
    q = (select(func.coalesce(func.sum(EcoInventory.qty), 0))
         .join(EcoItem, EcoItem.id == EcoInventory.item_id)
         .where(EcoInventory.tenant_id == tenant_id, EcoInventory.user_id == user_id, EcoItem.effect == effect))
    return int((await session.execute(q)).scalar_one())


async def inventory(session: AsyncSession, tenant_id: int, user_id: int) -> list[tuple[EcoItem, int]]:
    q = (select(EcoItem, EcoInventory.qty).join(EcoInventory, EcoInventory.item_id == EcoItem.id)
         .where(EcoInventory.tenant_id == tenant_id, EcoInventory.user_id == user_id, EcoInventory.qty > 0))
    return [(i, int(q_)) for i, q_ in (await session.execute(q)).all()]


async def consume_effect(session: AsyncSession, tenant_id: int, user_id: int, effect: str) -> bool:
    """Use up one item with the given effect (e.g. a padlock). False if the member has none."""
    q = (select(EcoItem).join(EcoInventory, EcoInventory.item_id == EcoItem.id)
         .where(EcoInventory.tenant_id == tenant_id, EcoInventory.user_id == user_id,
                EcoItem.effect == effect, EcoInventory.qty > 0).limit(1))
    item = (await session.execute(q)).scalars().first()
    return item is not None and await remove_item(session, tenant_id, user_id, item, 1)


# --- achievements & stats -------------------------------------------------------------------------------------

async def grant(session: AsyncSession, tenant_id: int, user_id: int, code: str) -> str | None:
    """Give an achievement once. Returns a message if it was newly earned."""
    have = await session.execute(select(Achievement.id).where(
        Achievement.tenant_id == tenant_id, Achievement.user_id == user_id, Achievement.code == code))
    if have.scalars().first() is not None:
        return None
    try:
        async with session.begin_nested():
            session.add(Achievement(tenant_id=tenant_id, user_id=user_id, code=code))
            await session.flush()
    except IntegrityError:
        return None
    emoji, title, _ = ACHIEVEMENTS[code]
    return f"{emoji} Achievement unlocked: <b>{title}</b>!"


async def wealth_achievements(session: AsyncSession, acct: EcoAccount) -> list[str]:
    out = []
    worth = networth(acct)
    for code, need in (("rich", 10_000), ("millionaire", 1_000_000)):
        if worth >= need and (msg := await grant(session, acct.tenant_id, acct.user_id, code)):
            out.append(msg)
    lvl = level_for(acct.xp)
    for code, need in (("level5", 5), ("level10", 10)):
        if lvl >= need and (msg := await grant(session, acct.tenant_id, acct.user_id, code)):
            out.append(msg)
    return out


async def record_game(
    session: AsyncSession, tenant_id: int, user_id: int, game: str, result: str, net: int
) -> list[str]:
    """Update per-game stats (result: win | loss | draw) and return any new achievements."""
    q = select(GameStat).where(GameStat.tenant_id == tenant_id, GameStat.user_id == user_id, GameStat.game == game)
    stat = (await session.execute(q)).scalars().first()
    if stat is None:
        stat = GameStat(tenant_id=tenant_id, user_id=user_id, game=game, plays=0, wins=0, losses=0, net=0)
        try:
            async with session.begin_nested():
                session.add(stat)
                await session.flush()
        except IntegrityError:
            stat = (await session.execute(q)).scalars().first()
            assert stat is not None
    stat.plays += 1
    stat.wins += result == "win"
    stat.losses += result == "loss"
    stat.net += net
    totals = (await session.execute(
        select(func.sum(GameStat.plays), func.sum(GameStat.wins))
        .where(GameStat.tenant_id == tenant_id, GameStat.user_id == user_id))).one()
    out = []
    if (totals[0] or 0) >= 100 and (msg := await grant(session, tenant_id, user_id, "gambler")):
        out.append(msg)
    if (totals[1] or 0) >= 50 and (msg := await grant(session, tenant_id, user_id, "winner")):
        out.append(msg)
    return out


async def top_wealth(session: AsyncSession, tenant_id: int, limit: int = 10) -> list[EcoAccount]:
    q = (select(EcoAccount).where(EcoAccount.tenant_id == tenant_id)
         .order_by((EcoAccount.wallet + EcoAccount.bank).desc()).limit(limit))
    return list((await session.execute(q)).scalars())


async def top_xp(session: AsyncSession, tenant_id: int, limit: int = 10) -> list[EcoAccount]:
    q = select(EcoAccount).where(EcoAccount.tenant_id == tenant_id).order_by(EcoAccount.xp.desc()).limit(limit)
    return list((await session.execute(q)).scalars())
