from __future__ import annotations

from dataclasses import dataclass

from app.database.models import Tenant


@dataclass(frozen=True)
class Theme:
    name: str
    divider: str
    spark: str
    description: str


THEMES: dict[str, Theme] = {
    "classic": Theme("classic", "━━━━━━━━━━━━━━", "✨", "Clean lines and a little sparkle"),
    "kawaii": Theme("kawaii", "✿･ﾟ:*✧･ﾟ:*✿", "🌸", "Soft, pink and cute"),
    "cyber": Theme("cyber", "▰▰▰▰▰▰▰▰▰▰", "⚡", "Neon and futuristic"),
    "minimal": Theme("minimal", "", "", "Plain and quiet"),
}
DEFAULT_THEME = "classic"


def theme_of(tenant: Tenant | None) -> Theme:
    name = (tenant.settings or {}).get("theme") if tenant is not None else None
    return THEMES.get(name or DEFAULT_THEME, THEMES[DEFAULT_THEME])


def header(theme: Theme, emoji: str, title: str) -> str:
    """A decorated screen title, e.g. '━━━\n✨ 🏠 Main menu ✨\n━━━'."""
    core = f"{theme.spark} <b>{emoji} {title}</b> {theme.spark}".strip() if theme.spark else f"<b>{emoji} {title}</b>"
    return f"{theme.divider}\n{core}\n{theme.divider}" if theme.divider else core
