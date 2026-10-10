from __future__ import annotations

from typing import TYPE_CHECKING

from app.bot.keyboards.billing import plans_button
from app.services import plans

if TYPE_CHECKING:
    from app.bot.context import Ctx


async def plan_of(ctx: Ctx) -> plans.PlanView:
    assert ctx.tenant is not None
    return await plans.effective_view(ctx.session, ctx.cache, ctx.tenant)


async def require_feature(ctx: Ctx, feature: str) -> bool:
    """True if this group's plan has the feature; otherwise explain and offer the plans."""
    if ctx.tenant is None:
        return False
    view = await plan_of(ctx)
    if view.has(feature):
        return True
    name = await plans.min_plan_name(ctx.session, feature)
    await ctx.reply(f"🔒 This feature needs the <b>{name}</b> plan or higher.", plans_button())
    return False
