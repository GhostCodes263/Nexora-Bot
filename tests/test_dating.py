from datetime import timedelta

from sqlalchemy import select

from app.bot.loader import load_modules
from app.bot.registry import REGISTRY
from app.database.models import Couple, DatingReport, Tenant
from app.modules.dating import flow
from app.repositories import dating as repo
from app.repositories import tenants as tenants_repo
from app.services import tenants as tenant_service
from app.utils.time import utcnow


def _data(**kw):
    base = dict(name="A", age=30, gender="female", location="Spain", interests="music, hiking, chess",
                bio="hi", looking_for="friendship", rel_status="")
    return {**base, **kw}


async def _tenant(session, chat_id):
    return await tenants_repo.get_or_create(session, chat_id, "supergroup", f"G{chat_id}")


async def _person(session, uid, tenant_ids, **kw):
    p = await repo.save_profile(session, uid, _data(name=f"U{uid}", **kw), None)
    for t in tenant_ids:
        await repo.join_pool(session, t, uid)
    await session.flush()
    return p


# --- pure helpers -------------------------------------------------------------------------------------------------
def test_gender_age_preferences():
    from app.database.models import DatingProfile as P

    a = P(user_id=1, name="a", age=30, gender="woman", pref_gender="male", pref_min_age=25, pref_max_age=40)
    b = P(user_id=2, name="b", age=35, gender="Male")
    c = P(user_id=3, name="c", age=22, gender="m")
    d = P(user_id=4, name="d", age=33, gender="nonbinary")
    assert flow.accepts(a, b) and not flow.accepts(a, c) and not flow.accepts(a, d)
    a.pref_gender = "any"
    assert flow.accepts(a, d)


def test_compat_score_is_bounded_and_rewards_overlap():
    from app.database.models import DatingProfile as P

    a = P(user_id=1, name="a", age=30, interests="music, chess", looking_for="relationship", location="Peru")
    same = P(user_id=2, name="b", age=30, interests="chess, music", looking_for="relationship", location="peru")
    other = P(user_id=3, name="c", age=60, interests="golf", looking_for="friends", location="Chile")
    assert flow.compat_score(a, same) == 100
    assert 0 <= flow.compat_score(a, other) < flow.compat_score(a, same)


def test_validation_blocks_bad_input():
    age_q = next(q for q in flow.QUESTIONS if q["kind"] == "age")
    assert not flow.validate(age_q, "abc", False)[0] and not flow.validate(age_q, "7", False)[0]
    assert flow.validate(age_q, "27", False) == (True, "27", "")
    photo_q = next(q for q in flow.QUESTIONS if q["kind"] == "photo")
    assert flow.validate(photo_q, "skip", False)[0] and not flow.validate(photo_q, "hello", False)[0]


def test_couple_achievements_by_time_and_xp():
    c = Couple(tenant_id=1, user_a=1, user_b=2, since=utcnow() - timedelta(days=40), xp=250)
    done = {name: ok for name, ok in flow.couple_achievements(c, utcnow())}
    assert done["💞 One week together"] and done["🌙 One month together"] and not done["💯 100 days together"]
    assert done["🍷 Sweethearts (200 couple XP)"] and not done["🔥 Power couple (1,000 couple XP)"]
    assert flow.couple_level(250) == 2


# --- tenant isolation of discovery ---------------------------------------------------------------------------------------
async def test_discovery_only_within_shared_pools(session):
    a, b = await _tenant(session, -1), await _tenant(session, -2)
    me = await _person(session, 1, [a.id])
    await _person(session, 2, [a.id])          # shares group A with me -> visible
    await _person(session, 3, [b.id])          # only in group B -> must NEVER be shown to me
    found = {p.user_id for p, _ in await repo.candidates(session, me)}
    assert found == {2}
    assert await repo.shared_tenants(session, 1, 3) == []


async def test_hidden_optedout_blocked_and_swiped_are_excluded(session):
    a = await _tenant(session, -1)
    me = await _person(session, 1, [a.id])
    hidden = await _person(session, 2, [a.id])
    hidden.hidden = True
    out = await _person(session, 3, [a.id])
    out.opted_out = True
    await _person(session, 4, [a.id])
    await _person(session, 5, [a.id])
    await _person(session, 6, [a.id])
    await repo.add_block(session, 1, 4)        # I blocked 4
    await repo.add_block(session, 5, 1)        # 5 blocked me: also invisible both ways
    await repo.record_swipe(session, a.id, 1, 6, "dislike")
    await session.flush()
    assert await repo.candidates(session, me) == []


# --- swipes, matches, couples ---------------------------------------------------------------------------------------------
async def test_match_requires_mutual_like_and_is_created_once(session):
    a = await _tenant(session, -1)
    await _person(session, 1, [a.id])
    await _person(session, 2, [a.id])
    assert not await repo.record_swipe(session, a.id, 1, 2, "like")        # one-sided
    assert await repo.get_match(session, a.id, 1, 2) is None
    assert await repo.record_swipe(session, a.id, 2, 1, "crush")           # crush counts as a like
    assert await repo.get_match(session, a.id, 2, 1) is not None
    assert not await repo.record_swipe(session, a.id, 2, 1, "like")        # no duplicate notification
    assert len(await repo.matches_of(session, 1)) == 1
    stats = await repo.stats(session, 1)
    assert stats["matches"] == 1 and stats["likes_given"] == 1 and stats["crushes_received"] == 1


async def test_dislike_never_matches(session):
    a = await _tenant(session, -1)
    await repo.record_swipe(session, a.id, 1, 2, "like")
    assert not await repo.record_swipe(session, a.id, 2, 1, "dislike")
    assert await repo.get_match(session, a.id, 1, 2) is None


async def test_one_couple_per_person_per_group(session):
    a, b = await _tenant(session, -1), await _tenant(session, -2)
    c = await repo.create_couple(session, a.id, 9, 3)
    assert (c.user_a, c.user_b) == (3, 9)                                   # stored in canonical order
    assert await repo.couple_of(session, a.id, 9) is not None
    assert await repo.couple_of(session, b.id, 9) is None                   # other tenant unaffected


# --- privacy -------------------------------------------------------------------------------------------------------------------
async def test_delete_all_removes_data_but_keeps_reports_against_the_user(session):
    a = await _tenant(session, -1)
    await _person(session, 1, [a.id])
    await _person(session, 2, [a.id])
    await repo.record_swipe(session, a.id, 1, 2, "like")
    await repo.record_swipe(session, a.id, 2, 1, "like")
    await repo.add_block(session, 1, 7)
    await repo.add_report(session, 1, 2, a.id, "mine")      # filed BY the deleting user
    await repo.add_report(session, 3, 1, a.id, "theirs")    # filed AGAINST the deleting user
    await repo.create_couple(session, a.id, 1, 2)
    await session.flush()
    await repo.delete_all(session, 1)
    assert await repo.get_profile(session, 1) is None
    assert await repo.pools_of(session, 1) == [] and await repo.matches_of(session, 1) == []
    assert await repo.couples_of(session, 1) == [] and await repo.blocks_of(session, 1) == []
    assert (await repo.get_profile(session, 2)) is not None                # others are untouched
    reports = (await session.execute(select(DatingReport.reason))).scalars().all()
    assert reports == ["theirs"]


async def test_leaving_a_pool_hides_you_from_that_group(session):
    a = await _tenant(session, -1)
    me = await _person(session, 1, [a.id])
    await _person(session, 2, [a.id])
    assert len(await repo.candidates(session, me)) == 1
    await repo.leave_pool(session, a.id, 2)
    assert await repo.candidates(session, me) == []


# --- module defaults / registry ----------------------------------------------------------------------------------------------------
def test_dating_is_off_by_default_for_rented_groups():
    load_modules()
    t = Tenant(chat_id=1, chat_type="group", module_overrides={}, settings={})
    assert not tenant_service.module_enabled(t, "dating")
    assert not tenant_service.is_enabled(t, REGISTRY.get("djoin"))
    t.module_overrides = {"dating": True}
    assert tenant_service.module_enabled(t, "dating") and tenant_service.is_enabled(t, REGISTRY.get("djoin"))
    assert tenant_service.module_enabled(t, "economy")                       # other modules stay on by default


def test_dating_command_set():
    load_modules()
    names = {s.name for s in REGISTRY.by_category()["dating"]}
    assert {"dsetup", "discover", "like", "dislike", "crush", "matches", "compat", "propose", "daccept", "dreject",
            "couple", "breakup", "datenight", "dstats", "dblock", "dreport", "ddelete", "doptout"} <= names
    assert len(REGISTRY.all()) >= 270
