from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime
from html import escape

from app.bot.context import Ctx
from app.bot.keyboards.play import Gm, rows
from app.bot.registry import command
from app.modules.entertainment import content as c
from app.services import gamestate

CAT = "entertainment"


def pick(items: list):
    return secrets.choice(items)


def digest_int(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).lower().encode()).hexdigest(), 16)


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _text(ctx: Ctx) -> str | None:
    if ctx.raw_args:
        return ctx.raw_args
    r = ctx.message.reply_to_message
    return (r.text or r.caption) if r else None


async def _name(ctx: Ctx) -> str:
    target, _ = await ctx.resolve_target()
    return escape(target.name) if target else escape(ctx.user.first_name or "friend")


@command("joke", description="A random joke", category=CAT, cooldown=3)
async def joke(ctx: Ctx) -> None:
    await ctx.reply("😄 " + pick(c.JOKES))


@command("dadjoke", description="A groan-worthy dad joke", category=CAT, cooldown=3)
async def dadjoke(ctx: Ctx) -> None:
    await ctx.reply("👨 " + pick(c.DAD_JOKES))


@command("quote", description="A motivational line", category=CAT, cooldown=3)
async def quote(ctx: Ctx) -> None:
    await ctx.reply("💬 " + pick(c.QUOTES))


@command("fact", description="A random fun fact", category=CAT, aliases=("funfact",), cooldown=3)
async def fact(ctx: Ctx) -> None:
    await ctx.reply("🧠 " + pick(c.FACTS))


@command("wyr", description="Would you rather...?", category=CAT, aliases=("wouldyourather",), cooldown=3)
async def wyr(ctx: Ctx) -> None:
    await ctx.reply("🤔 " + pick(c.WOULD_YOU_RATHER))


@command("truth", description="A truth question", category=CAT, cooldown=3)
async def truth(ctx: Ctx) -> None:
    await ctx.reply("🫣 <b>Truth:</b> " + pick(c.TRUTHS))


@command("dare", description="A harmless dare", category=CAT, cooldown=3)
async def dare(ctx: Ctx) -> None:
    await ctx.reply("😈 <b>Dare:</b> " + pick(c.DARES))


@command("tod", description="Truth or dare, randomly chosen", category=CAT, aliases=("truthordare",), cooldown=3)
async def tod(ctx: Ctx) -> None:
    if secrets.randbelow(2):
        await ctx.reply("🫣 <b>Truth:</b> " + pick(c.TRUTHS))
    else:
        await ctx.reply("😈 <b>Dare:</b> " + pick(c.DARES))


@command("8ball", description="Ask the magic 8-ball", category=CAT, usage="/8ball <question>", aliases=("eightball",), cooldown=3)
async def eight_ball(ctx: Ctx) -> None:
    if not ctx.raw_args:
        await ctx.reply("Ask a question: <code>/8ball will it rain?</code>")
        return
    await ctx.reply(f"🎱 <i>{escape(ctx.raw_args[:200])}</i>\n<b>{pick(c.EIGHT_BALL)}</b>")


@command("pickup", description="A cheesy (and clean) pickup line", category=CAT, cooldown=3)
async def pickup(ctx: Ctx) -> None:
    await ctx.reply("😏 " + pick(c.PICKUP))


@command("roast", description="A light-hearted roast", category=CAT, usage="/roast [user]", cooldown=5)
async def roast(ctx: Ctx) -> None:
    await ctx.reply("🔥 " + pick(c.ROASTS).replace("{name}", await _name(ctx)) + "\n<i>(All in good fun!)</i>")


@command("compliment", description="Give someone a compliment", category=CAT, usage="/compliment [user]", cooldown=5)
async def compliment(ctx: Ctx) -> None:
    await ctx.reply("💖 " + pick(c.COMPLIMENTS).replace("{name}", await _name(ctx)))


@command("ship", description="Compatibility score for two names", category=CAT, aliases=("compatibility",),
         usage="/ship <name1> <name2>  (or reply to someone)", examples=("/ship Anna Ben",), cooldown=3)
async def ship(ctx: Ctx) -> None:
    a = ctx.args
    r = ctx.message.reply_to_message
    if len(a) >= 2:
        n1, n2 = a[0], a[1]
    elif r and r.from_user and a:
        n1, n2 = r.from_user.first_name, a[0]
    elif r and r.from_user:
        n1, n2 = ctx.user.first_name or "You", r.from_user.first_name
    else:
        await ctx.reply("Usage: <code>/ship Anna Ben</code> or reply to someone.")
        return
    pct = digest_int(*sorted([n1.lower(), n2.lower()])) % 101
    bar = "❤️" * (pct // 10) + "🤍" * (10 - pct // 10)
    verdict = "A match made in heaven!" if pct >= 80 else "Looking promising." if pct >= 55 else "Could be friends." if pct >= 30 else "Maybe next lifetime."
    await ctx.reply(f"💘 <b>{escape(n1)}</b> × <b>{escape(n2)}</b>\n{bar} <b>{pct}%</b>\n{verdict}\n<i>(Just for fun!)</i>")


@command("rate", description="Rate anything out of 10", category=CAT, usage="/rate <thing>", cooldown=3)
async def rate(ctx: Ctx) -> None:
    t = _text(ctx)
    if not t:
        await ctx.reply("Usage: <code>/rate pineapple pizza</code>")
        return
    score = digest_int(t.strip()) % 11
    await ctx.reply(f"⭐ I rate <b>{escape(t[:80])}</b> a <b>{score}/10</b>.")


@command("vibe", description="Today's vibe check", category=CAT, cooldown=5)
async def vibe(ctx: Ctx) -> None:
    n = digest_int(str(ctx.user_id), today(), "vibe")
    moods = ["chill 😌", "energetic ⚡", "creative 🎨", "cosy ☕", "unstoppable 🚀", "mysterious 🌙", "cheerful 🌞", "focused 🎯"]
    await ctx.reply(f"✨ Your vibe today: <b>{moods[n % len(moods)]}</b> ({n // 10 % 41 + 60}% accuracy, trust me)")


@command("luck", description="Your luck for today", category=CAT, cooldown=5)
async def luck(ctx: Ctx) -> None:
    pct = digest_int(str(ctx.user_id), today(), "luck") % 101
    await ctx.reply(f"🍀 Your luck today: <b>{pct}%</b> " + ("🌟" if pct >= 80 else "🙂" if pct >= 40 else "☔"))


@command("horoscope", description="A light-hearted daily horoscope", category=CAT, usage="/horoscope <sign>",
         examples=("/horoscope leo",), cooldown=5)
async def horoscope(ctx: Ctx) -> None:
    sign = ctx.args[0].lower() if ctx.args else ""
    if sign not in c.SIGNS:
        await ctx.reply("Pick a sign: " + ", ".join(c.SIGNS))
        return
    line = c.HOROSCOPE[digest_int(sign, today()) % len(c.HOROSCOPE)]
    await ctx.reply(f"🔮 <b>{sign.title()}</b> — {today()}\n{line}\n<i>For entertainment only.</i>")


@command("fortune", description="Open a fortune cookie", category=CAT, cooldown=3)
async def fortune(ctx: Ctx) -> None:
    await ctx.reply("🥠 " + pick(c.FORTUNES))


@command("advice", description="A bit of friendly advice", category=CAT, cooldown=3)
async def advice(ctx: Ctx) -> None:
    await ctx.reply("💡 " + pick(c.ADVICE))


@command("meme", description="A meme-style text joke", category=CAT, cooldown=3)
async def meme(ctx: Ctx) -> None:
    await ctx.reply("🖼 " + pick(c.MEMES))


@command("riddle", description="Get a riddle (tap to reveal the answer)", category=CAT, scope="group", cooldown=10)
async def riddle(ctx: Ctx) -> None:
    q, a = pick(c.RIDDLES)
    gid = gamestate.new_id()
    await gamestate.save(ctx.cache, gid, {"tenant": ctx.tenant.id, "chat": ctx.message.chat.id, "answer": a, "q": q}, 1800)  # type: ignore[union-attr]
    await ctx.reply(f"🧩 <b>Riddle:</b> {escape(q)}", rows([("👀 Reveal answer", Gm(g="rd", id=gid, a="show"))]))


@command("mock", description="sPoNgEbOb cAsE", category=CAT, usage="/mock <text|reply>", cooldown=3)
async def mock(ctx: Ctx) -> None:
    t = _text(ctx)
    if not t:
        await ctx.reply("Give me text or reply to a message.")
        return
    out, up = [], False
    for ch in t[:300]:
        if ch.isalpha():
            out.append(ch.upper() if up else ch.lower())
            up = not up
        else:
            out.append(ch)
    await ctx.reply(escape("".join(out)))


@command("clap", description="Add 👏 between 👏 words", category=CAT, usage="/clap <text|reply>", cooldown=3)
async def clap(ctx: Ctx) -> None:
    t = _text(ctx)
    await ctx.reply(" 👏 ".join(escape(t[:300]).split()) + " 👏" if t else "Give me text or reply to a message.")


@command("emojify", description="Turn text into big emoji letters", category=CAT, usage="/emojify <text>", cooldown=3)
async def emojify(ctx: Ctx) -> None:
    t = (_text(ctx) or "")[:40].lower()
    if not t:
        await ctx.reply("Usage: <code>/emojify hello</code>")
        return
    keycaps = {str(d): f"{d}\ufe0f\u20e3" for d in range(10)}
    out = []
    for ch in t:
        if "a" <= ch <= "z":
            out.append(chr(0x1F1E6 + ord(ch) - 97) + "\u200b")
        elif ch.isdigit():
            out.append(keycaps[ch])
        else:
            out.append("   " if ch == " " else ch)
    await ctx.reply(" ".join(out))


@command("flip", description="Flip text upside down", category=CAT, usage="/flip <text>", cooldown=3)
async def flip(ctx: Ctx) -> None:
    t = (_text(ctx) or "")[:200]
    if not t:
        await ctx.reply("Usage: <code>/flip hello</code>")
        return
    await ctx.reply(escape("".join(c.FLIP_MAP.get(ch, ch) for ch in reversed(t.lower()))))


@command("choose", description="Pick one option for you", category=CAT, usage="/choose <a | b | c>",
         examples=("/choose pizza | sushi | tacos",), cooldown=2)
async def choose(ctx: Ctx) -> None:
    opts = [o.strip() for o in re.split(r"\||,| or ", ctx.raw_args) if o.strip()]
    if len(opts) < 2:
        await ctx.reply("Give me at least two options: <code>/choose pizza | sushi</code>")
        return
    await ctx.reply(f"🎯 I choose: <b>{escape(pick(opts)[:100])}</b>")


@command("roll", description="Roll dice like 2d6", category=CAT, usage="/roll [NdM]", examples=("/roll", "/roll 3d8"), cooldown=2)
async def roll(ctx: Ctx) -> None:
    m = re.fullmatch(r"(\d{1,2})d(\d{1,4})", (ctx.args[0].lower() if ctx.args else "1d6"))
    if not m or not 1 <= int(m.group(1)) <= 20 or not 2 <= int(m.group(2)) <= 1000:
        await ctx.reply("Use <code>/roll 2d6</code> (up to 20 dice, 2 to 1000 sides).")
        return
    n, sides = int(m.group(1)), int(m.group(2))
    rolls = [secrets.randbelow(sides) + 1 for _ in range(n)]
    await ctx.reply(f"🎲 {n}d{sides}: {', '.join(map(str, rolls))}" + (f" = <b>{sum(rolls)}</b>" if n > 1 else ""))


@command("fantasyname", description="Generate a fantasy character name", category=CAT, cooldown=2)
async def fantasyname(ctx: Ctx) -> None:
    names = [pick(c.NAME_START) + pick(c.NAME_END) for _ in range(3)]
    await ctx.reply("🧙 " + ", ".join(f"<b>{n}</b>" for n in names))


@command("randomcolor", description="A random colour with its hex code", category=CAT, aliases=("color",), cooldown=2)
async def randomcolor(ctx: Ctx) -> None:
    hexcode = f"#{secrets.randbits(24):06X}"
    await ctx.reply(f"🎨 <code>{hexcode}</code>")


async def _interact(ctx: Ctx, lines: list[str], emoji: str) -> None:
    target, _ = await ctx.resolve_target()
    me = escape(ctx.user.first_name or "Someone")
    other = escape(target.name) if target else None
    if other is None:
        await ctx.reply(f"Reply to someone or use <code>/{ctx.name} @user</code> {emoji}")
        return
    await ctx.reply(f"{emoji} <b>{me}</b> " + pick(lines).replace("{b}", f"<b>{other}</b>"))


@command("hug", description="Give someone a hug", category=CAT, usage="/hug <user>", cooldown=3)
async def hug(ctx: Ctx) -> None:
    await _interact(ctx, c.HUG, "🤗")


@command("slap", description="A playful, harmless slap", category=CAT, usage="/slap <user>", cooldown=3)
async def slap(ctx: Ctx) -> None:
    await _interact(ctx, c.SLAP, "👋")


@command("pat", description="Pat someone on the head", category=CAT, usage="/pat <user>", cooldown=3)
async def pat(ctx: Ctx) -> None:
    await _interact(ctx, c.PAT, "🐾")


@command("highfive", description="High-five someone", category=CAT, usage="/highfive <user>", cooldown=3)
async def highfive(ctx: Ctx) -> None:
    await _interact(ctx, c.HIGHFIVE, "🙌")


@command("cheers", description="Raise a toast with someone", category=CAT, usage="/cheers <user>", cooldown=3)
async def cheers(ctx: Ctx) -> None:
    await _interact(ctx, c.CHEERS, "🥂")
