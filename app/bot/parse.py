from __future__ import annotations

import re

_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def parse_command(text: str, bot_username: str, prefix: str = "/") -> tuple[str, str] | None:
    """Return (command_name, raw_args) or None.

    Accepts "/cmd", "/cmd@ThisBot args" and, if the tenant uses a custom prefix, "!cmd args".
    Commands addressed to a different bot (/cmd@OtherBot) are ignored.
    """
    text = text.strip()
    if not text:
        return None
    parts = text.split(None, 1)
    token = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if token.startswith("/"):
        body = token[1:]
    elif prefix != "/" and prefix and token.startswith(prefix):
        body = token[len(prefix):]
    else:
        return None
    name, _, target = body.partition("@")
    if target and target.lower() != bot_username.lower():
        return None
    name = name.lower()
    if not _NAME_RE.match(name):
        return None
    return name, rest.strip()
