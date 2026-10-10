from __future__ import annotations

import ast
import base64
import hashlib
import math
import operator
import secrets
import string
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta, timezone
from html import escape

from aiogram.exceptions import TelegramAPIError

from app.bot.context import Ctx
from app.bot.registry import command
from app.services import platform
from app.services.permissions import resolve_role
from app.utils.time import aware

CAT = "utility"


def _need_text(ctx: Ctx) -> str | None:
    """Text argument, or the text of the replied-to message."""
    if ctx.raw_args:
        return ctx.raw_args
    r = ctx.message.reply_to_message
    return (r.text or r.caption) if r and (r.text or r.caption) else None


@command("ping", description="Check the bot's response time", category=CAT)
async def ping(ctx: Ctx) -> None:
    import time

    t0 = time.perf_counter()
    m = await ctx.reply("🏓")
    ms = int((time.perf_counter() - t0) * 1000)
    await m.edit_text(f"🏓 Pong! <b>{ms} ms</b>")


@command("uptime", description="How long the bot has been running", category=CAT)
async def uptime(ctx: Ctx) -> None:
    await ctx.reply(f"⏱ Up for <b>{platform.uptime()}</b>")


@command("id", description="Show your ID, the chat ID and a replied user's ID", category=CAT)
async def id_cmd(ctx: Ctx) -> None:
    lines = [f"👤 Your ID: <code>{ctx.user_id}</code>", f"💬 Chat ID: <code>{ctx.message.chat.id}</code>"]
    r = ctx.message.reply_to_message
    if r and r.from_user:
        lines.append(f"↩️ Replied user: <code>{r.from_user.id}</code>")
    await ctx.reply("\n".join(lines))


@command(
    "userinfo", description="Info about a user (reply, ID or @username)", category=CAT,
    usage="/userinfo [reply|id|@username]", aliases=("whois",), cooldown=3,
)
async def userinfo(ctx: Ctx) -> None:
    target, _ = await ctx.resolve_target()
    uid, name = (target.id, target.name) if target else (ctx.user_id, ctx.user.first_name)
    from app.repositories import users as users_repo

    rec = await users_repo.get_user(ctx.session, uid)
    lines = [f"<b>👤 {escape(name or str(uid))}</b>", f"ID: <code>{uid}</code>"]
    if rec and rec.username:
        lines.append(f"Username: @{escape(rec.username)}")
    if ctx.tenant is not None:
        role = await resolve_role(ctx.session, ctx.bot, ctx.cache, ctx.settings, uid, ctx.tenant)
        lines.append(f"Role here: {role.label}")
    if rec:
        first = aware(rec.created_at)
        lines.append(f"First seen: {first:%Y-%m-%d}" if first else "")
        if rec.is_globally_banned:
            lines.append("⛔ Globally banned")
    await ctx.reply("\n".join(x for x in lines if x))


@command("chatinfo", description="Information about this chat", category=CAT, aliases=("groupinfo",))
async def chatinfo(ctx: Ctx) -> None:
    chat = ctx.message.chat
    lines = [f"<b>💬 {escape(chat.title or 'Private chat')}</b>", f"ID: <code>{chat.id}</code>", f"Type: {chat.type}"]
    if ctx.tenant is not None:
        try:
            lines.append(f"Members: {await ctx.bot.get_chat_member_count(chat.id)}")
        except TelegramAPIError:
            pass
        lines.append(f"Prefix: <code>{escape(ctx.tenant.prefix)}</code>")
    await ctx.reply("\n".join(lines))


@command("avatar", description="Get a user's profile picture", category=CAT,
         usage="/avatar [reply|id]", cooldown=5)
async def avatar(ctx: Ctx) -> None:
    target, _ = await ctx.resolve_target()
    uid = target.id if target else ctx.user_id
    try:
        photos = await ctx.bot.get_user_profile_photos(uid, limit=1)
    except TelegramAPIError:
        await ctx.reply("I couldn't fetch that profile picture.")
        return
    if photos.total_count == 0:
        await ctx.reply("No visible profile picture.")
        return
    await ctx.message.reply_photo(photos.photos[0][-1].file_id)


@command("time", description="Current time (UTC or an offset)", category=CAT,
         usage="/time [+3|-5:30]", examples=("/time", "/time +5:30"))
async def time_cmd(ctx: Ctx) -> None:
    now = datetime.now(UTC)
    if ctx.args:
        raw = ctx.args[0]
        try:
            sign = -1 if raw.startswith("-") else 1
            h, _, m = raw.lstrip("+-").partition(":")
            delta = timedelta(hours=int(h), minutes=int(m or 0)) * sign
            if abs(delta) > timedelta(hours=14):
                raise ValueError
            now = now.astimezone(timezone(delta))
        except ValueError:
            await ctx.reply("Use an offset like <code>+3</code> or <code>-5:30</code>.")
            return
    await ctx.reply(f"🕒 <b>{now:%Y-%m-%d %H:%M:%S}</b> (UTC{now:%z})")


# --- calculator (AST based, no eval) ------------------------------------------
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "sin": math.sin, "cos": math.cos,
          "tan": math.tan, "log": math.log, "log10": math.log10, "floor": math.floor, "ceil": math.ceil}
_CONST = {"pi": math.pi, "e": math.e}


def safe_eval(expr: str) -> float | int:
    if len(expr) > 200:
        raise ValueError("expression too long")

    def ev(node: ast.AST) -> float | int:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.Name) and node.id in _CONST:
            return _CONST[node.id]
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow):
                if abs(right) > 100 or abs(left) > 1e6:
                    raise ValueError("power too large")
                return left ** right
            op = _BIN.get(type(node.op))
            if op is None:
                raise ValueError("unsupported operator")
            return op(left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            if node.keywords or len(node.args) > 2:
                raise ValueError("bad call")
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError("unsupported expression")

    return ev(ast.parse(expr.replace("^", "**"), mode="eval").body)


@command("calc", description="Evaluate a math expression", category=CAT, aliases=("math",),
         usage="/calc <expression>", examples=("/calc 2*(3+4)", "/calc sqrt(144)"))
async def calc(ctx: Ctx) -> None:
    if not ctx.raw_args:
        await ctx.reply("Usage: <code>/calc 2*(3+4)</code>")
        return
    try:
        result = safe_eval(ctx.raw_args)
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError):
        await ctx.reply("❌ I couldn't evaluate that expression.")
        return
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        result = int(result)
    await ctx.reply(f"🧮 <code>{escape(ctx.raw_args)}</code> = <b>{result}</b>")


# --- unit conversion -------------------------------------------------------------
_UNITS: dict[str, tuple[str, float]] = {
    "mm": ("len", 0.001), "cm": ("len", 0.01), "m": ("len", 1), "km": ("len", 1000),
    "in": ("len", 0.0254), "ft": ("len", 0.3048), "yd": ("len", 0.9144), "mi": ("len", 1609.344),
    "g": ("mass", 0.001), "kg": ("mass", 1), "oz": ("mass", 0.028349523125), "lb": ("mass", 0.45359237),
    "ml": ("vol", 0.001), "l": ("vol", 1), "gal": ("vol", 3.785411784), "floz": ("vol", 0.0295735295625),
    "kmh": ("speed", 1 / 3.6), "ms": ("speed", 1), "mph": ("speed", 0.44704),
}


def convert_value(value: float, src: str, dst: str) -> float:
    src, dst = src.lower(), dst.lower()
    temps = {"c", "f", "k"}
    if src in temps and dst in temps:
        celsius = value if src == "c" else value - 273.15 if src == "k" else (value - 32) * 5 / 9
        return celsius if dst == "c" else celsius + 273.15 if dst == "k" else celsius * 9 / 5 + 32
    if src not in _UNITS or dst not in _UNITS or _UNITS[src][0] != _UNITS[dst][0]:
        raise ValueError("incompatible units")
    return value * _UNITS[src][1] / _UNITS[dst][1]


@command("convert", description="Convert units (length, mass, volume, speed, temperature)",
         category=CAT, usage="/convert <value> <from> <to>", examples=("/convert 10 km mi", "/convert 72 f c"))
async def convert(ctx: Ctx) -> None:
    a = ctx.args
    try:
        if len(a) != 3:
            raise ValueError
        out = convert_value(float(a[0]), a[1], a[2])
    except ValueError:
        await ctx.reply("Usage: <code>/convert 10 km mi</code>\nUnits: mm cm m km in ft yd mi g kg oz lb ml l gal floz kmh ms mph c f k")
        return
    await ctx.reply(f"📏 {escape(a[0])} {escape(a[1])} = <b>{out:.6g}</b> {escape(a[2])}")


@command("base64", description="Encode or decode Base64", category=CAT,
         usage="/base64 <encode|decode> <text>", examples=("/base64 encode hello",))
async def b64(ctx: Ctx) -> None:
    mode, _, text = ctx.raw_args.partition(" ")
    text = text.strip() or (_need_text(ctx) if not text else "")
    if mode not in {"encode", "decode"} or not text:
        await ctx.reply("Usage: <code>/base64 encode hello</code>")
        return
    try:
        out = (base64.b64encode(text.encode()).decode() if mode == "encode"
               else base64.b64decode(text.encode(), validate=True).decode())
    except (ValueError, UnicodeDecodeError):
        await ctx.reply("❌ That isn't valid Base64 text.")
        return
    await ctx.reply(f"<code>{escape(out)}</code>")


@command("hash", description="Hash text (sha256 default)", category=CAT,
         usage="/hash [sha256|sha512|sha1|md5] <text>")
async def hash_cmd(ctx: Ctx) -> None:
    a = ctx.raw_args.split(None, 1)
    algo = "sha256"
    if a and a[0].lower() in {"sha256", "sha512", "sha1", "md5"}:
        algo, a = a[0].lower(), a[1:]
    text = a[0] if a else _need_text(ctx)
    if not text:
        await ctx.reply("Usage: <code>/hash sha256 hello</code>")
        return
    digest = hashlib.new(algo, text.encode()).hexdigest()
    await ctx.reply(f"<b>{algo}</b>\n<code>{digest}</code>")


@command("uuid", description="Generate a random UUID", category=CAT)
async def uuid_cmd(ctx: Ctx) -> None:
    await ctx.reply(f"<code>{uuid.uuid4()}</code>")


@command("random", description="Random number between two values", category=CAT,
         usage="/random [min] [max]", examples=("/random", "/random 1 6"))
async def random_cmd(ctx: Ctx) -> None:
    try:
        lo, hi = (1, 100) if not ctx.args else (int(ctx.args[0]), int(ctx.args[1])) if len(ctx.args) > 1 else (1, int(ctx.args[0]))
        if lo > hi or hi - lo > 10**12:
            raise ValueError
    except ValueError:
        await ctx.reply("Usage: <code>/random 1 100</code>")
        return
    await ctx.reply(f"🎲 <b>{lo + secrets.randbelow(hi - lo + 1)}</b>")


@command("genpass", description="Generate a strong random password", category=CAT,
         usage="/genpass [length 8-64]", cooldown=3)
async def genpass(ctx: Ctx) -> None:
    try:
        n = int(ctx.args[0]) if ctx.args else 16
        if not 8 <= n <= 64:
            raise ValueError
    except ValueError:
        await ctx.reply("Length must be a number from 8 to 64.")
        return
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_"
    pw = "".join(secrets.choice(alphabet) for _ in range(n))
    await ctx.reply(f"🔑 <code>{escape(pw)}</code>\n<i>Generated randomly; don't reuse it elsewhere after sharing.</i>")


@command("upper", description="Convert text to UPPERCASE", category=CAT, usage="/upper <text|reply>")
async def upper(ctx: Ctx) -> None:
    t = _need_text(ctx)
    await ctx.reply(escape(t.upper()) if t else "Give me some text or reply to a message.")


@command("lower", description="Convert text to lowercase", category=CAT, usage="/lower <text|reply>")
async def lower(ctx: Ctx) -> None:
    t = _need_text(ctx)
    await ctx.reply(escape(t.lower()) if t else "Give me some text or reply to a message.")


@command("reverse", description="Reverse text", category=CAT, usage="/reverse <text|reply>")
async def reverse(ctx: Ctx) -> None:
    t = _need_text(ctx)
    await ctx.reply(escape(t[::-1]) if t else "Give me some text or reply to a message.")


@command("wordcount", description="Count words and characters", category=CAT, aliases=("count",),
         usage="/wordcount <text|reply>")
async def wordcount(ctx: Ctx) -> None:
    t = _need_text(ctx)
    if not t:
        await ctx.reply("Give me some text or reply to a message.")
        return
    await ctx.reply(f"📝 Words: <b>{len(t.split())}</b> · Characters: <b>{len(t)}</b> · Lines: <b>{len(t.splitlines())}</b>")


@command("urlencode", description="Percent-encode text for URLs", category=CAT, usage="/urlencode <text>")
async def urlencode(ctx: Ctx) -> None:
    t = _need_text(ctx)
    await ctx.reply(f"<code>{escape(urllib.parse.quote(t, safe=''))}</code>" if t else "Give me some text.")


@command("urldecode", description="Decode percent-encoded text", category=CAT, usage="/urldecode <text>")
async def urldecode(ctx: Ctx) -> None:
    t = _need_text(ctx)
    await ctx.reply(f"<code>{escape(urllib.parse.unquote(t))}</code>" if t else "Give me some text.")
