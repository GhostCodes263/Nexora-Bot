# Telegram Bot Platform

Multi-tenant Telegram bot (aiogram 3, PostgreSQL, Redis, SQLAlchemy 2, Alembic) to rent out to other
groups and to run your own public + VIP community. **Status: Phases 1, 2 and 3a done (242 commands).**
The exact command list is in `COMMANDS.md` (generated).

## What exists now
**Phase 1 – foundation:** multi-tenant core, role hierarchy (User → Owner), command registry + automatic
`/help`, button menus, owner panel, rate limits/cooldowns, maintenance mode, global ban, error handler,
Redis with memory fallback.

**Phase 2 – business features**
- **Moderation:** ban/unban/tban, kick, mute/tmute/unmute, warn/unwarn/warnings with per-group thresholds
  (`/warnrule 3 mute`), purge/del, lock/unlock, word filters, anti-link/flood/invite/forward/bot/media/spam,
  slow mode, mod notes, user history, mod log, report.
- **Group tools:** welcome/goodbye, rules, join captcha (auto-kick if not solved), pins, custom commands,
  keyword auto-replies, scheduled messages, join/leave stats.
- **Plans & rentals:** Free/Starter/Pro/Premium/Ultimate stored in the database, 7-day trials (one per group,
  max 2 per person), monthly/yearly, upgrade/downgrade with unused time converted, grace period, expiry
  reminders, automatic expiry, data kept and restored on renewal.
- **Payments:** Telegram Stars through a provider abstraction (`app/services/payments.py`), duplicate-callback
  protection, server-side price validation, refunds (`/refund`), revenue and payment lists.
- **Verification:** `/verify` interview in private chat, IDs like `V-48291`, statuses PENDING / UNDER_REVIEW /
  APPROVED / REJECTED / NEEDS_MORE_INFORMATION / SUSPENDED, reviewer queue with buttons, full action log,
  users can delete their data. No ID documents are ever requested; under-18s are turned away.
- **VIP:** only verified users can buy; payment unlocks a join-request link to your VIP channel that the bot
  approves only for active members; reminders, grace period, automatic removal on expiry.
- **Scheduler:** runs inside the bot every ~30 s (rental/VIP expiry and reminders, scheduled messages, captcha
  timeouts). A Redis lock stops two instances doing the same work.

**Phase 3a – economy, games, fun**
- **Economy (31 commands):** wallet and bank with daily interest, daily/weekly rewards with streaks, work, beg,
  crime, fish, rob (with padlocks), pay (optional tax), shop with item effects, inventory, selling, gifting,
  player-to-player trades with accept buttons, leaderboards, XP and levels (members earn XP for chatting),
  achievements, transaction history. Admins tune everything per group with `/ecoconfig`, and can add/remove
  money (always logged). **Every account belongs to one group**: the same person has separate balances elsewhere.
- **Games (25):** coinflip, dice, slots, roulette, blackjack, high-low, cups, rock-paper-scissors, tic-tac-toe,
  duels, trivia, hangman, number guess, word scramble, maths, reaction test, lottery, prize wheel, plus stats and
  leaderboards. Bets use **virtual currency only**.
- **Entertainment (34):** jokes, facts, 8-ball, truth/dare, would-you-rather, riddles, ship/rate/luck/horoscope,
  roasts and compliments (kept friendly), text toys, hug/pat/high-five and more.
- **Anti-exploit:** wallet rows are locked while they change, balances can never go negative, every change is in
  a ledger, trades and duels are one-shot, bets are escrowed before a game starts, rob has cooldowns and per-victim
  limits, free groups have a daily game-play cap (paid plans get more) and the shop is a paid feature.
- Games waiting on a button (blackjack, cups, high-low) keep your bet in escrow; if you never finish, it is lost
  after about 10 minutes.

## Not built yet (Phase 3b)
Dating/social, AI (Gemini/OpenAI with limits), analytics dashboards, customer dashboard, channel tools,
media tools. Nothing is stubbed; unbuilt features simply have no commands.

## Updating an existing deployment
Replace/upload the new files in your GitHub repo and commit. Render redeploys and the new migration
(`0002`, `0003`) create the new tables automatically and update the plans (shop = paid feature, daily game cap).

## Setting things up after deploy
1. **Payments:** nothing to configure. Stars invoices need no provider token. Prices are in the `tp_plans`
   table; change them from Telegram with `/setplan pro stars_month 500` or `/setplan pro price_month 9.99`.
   (Default Stars amounts assume about 50 Stars per US$1.)
2. **Billing rules:** `/setbilling` shows trial days, grace days and trials per person.
3. **VIP channel:** create a private channel, add the bot as admin (rights: *Invite users*, *Ban users*), then in
   a private chat with the bot send `/vipconfig channel_id -100XXXXXXXXXX` (or set `VIP_CHANNEL_ID`).
   Set prices with `/vipconfig monthly_stars 500` and `/vipconfig annual_stars 5000`.
4. **Reviewers:** `/addreviewer <id>` gives someone the right to review applications only. They get no access
   to payments, config or other groups. You (the owner) always can review.
5. **Customer groups:** add the bot, make it admin (delete messages, restrict members, pin, invite), send
   `/menu`. Admins start a trial with `/trial pro` or buy with `/rent`.

## Important behaviours and limits
- **Slow mode is enforced by the bot** (Telegram's bot API cannot set native slow mode), so the bot needs
  *Delete messages*.
- Join/leave features rely on Telegram's service messages, which Telegram hides in very large groups.
- Auto-moderation ignores admins, group owners and anyone you promoted to Trusted or higher.
- Stars purchases are one-time payments (no automatic renewal); the bot sends renewal reminders instead.
- During the grace period paid features stay on; when it ends they switch off but nothing is deleted.
- Invoices are sent to the buyer's private chat, so they must press Start in a chat with the bot once.

## Deploy on Render
See `render.yaml`. In short: BotFather token + `/setprivacy` Disable, push to GitHub, Render → New →
Blueprint, set `BOT_TOKEN` and `OWNER_ID`. Webhooks are used on Render (no keep-alive pinger needed); polling is
the automatic fallback when no public URL exists.

| Variable | Required | Notes |
|---|---|---|
| `BOT_TOKEN`, `OWNER_ID` | yes | BotFather token; your numeric Telegram ID |
| `DATABASE_URL`, `REDIS_URL` | yes / recommended | filled in by `render.yaml` |
| `WEBHOOK_SECRET` | recommended | Render generates it |
| `VIP_CHANNEL_ID`, `PUBLIC_CHANNEL_ID` | optional | VIP can also be set with `/vipconfig` |
| `GEMINI_API_KEY`, `OPENAI_API_KEY` | Phase 3 | |

## Tests and CI
`pytest` covers users, tenant isolation, roles, registry, cooldowns/rate limits, Redis outage fallback,
moderation thresholds, economy transactions, tenant isolation of balances, interest, trades/items,
achievements, game rules, trials, rentals, grace/expiry, plan switching, payment idempotency and refunds,
checkout validation, verification workflow, reviewer isolation, VIP expiry and eligibility.
GitHub Actions runs them on every push (Actions tab).
> The tests were written but never executed by me (no network to install packages in the build
> environment). Treat the first CI run as the real result and send me any failures.

## Adding a command
```python
@command("hello", description="Say hello", category="utility", cooldown=5)
async def hello(ctx: Ctx) -> None:
    await ctx.reply("Hello!")
```
Put it in `app/modules/<module>/commands.py`; it shows up in `/help` and `COMMANDS.md` automatically
(`python scripts/generate_inventory.py`). Plan limits: use `require_feature(ctx, "feature")`.

## Layout
```
app/main.py            startup, webhook/polling, health, scheduler
app/bot/               registry, dispatcher handlers, menus, middleware
app/modules/*/         commands grouped by module
app/services/          roles, permissions, plans, billing, payments, vip, scheduler, cache
app/repositories/      database access (tenant-scoped)
alembic/versions/      0001 core, 0002 phase 2, 0003 economy/games (tables are prefixed tp_)
```
