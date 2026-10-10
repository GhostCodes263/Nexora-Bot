from __future__ import annotations

# key -> (emoji, title, can a tenant toggle it?)
CATEGORIES: dict[str, tuple[str, str, bool]] = {
    "core": ("🏠", "Core", False),
    "admin": ("🛡", "Administration", False),
    "moderation": ("🛡", "Moderation", True),
    "management": ("👥", "Group tools", True),
    "economy": ("💰", "Economy", True),
    "games": ("🎮", "Games", True),
    "entertainment": ("🎉", "Entertainment", True),
    "utility": ("🧰", "Utilities", True),
    "rentals": ("💎", "Plans & rentals", False),
    "verification": ("✅", "Verification", False),
    "vip": ("💠", "VIP", False),
    "payments": ("💳", "Payments", False),
    "owner": ("👑", "Owner", False),
}
ORDER = list(CATEGORIES)


def info(category: str) -> tuple[str, str, bool]:
    return CATEGORIES.get(category, ("📦", category.title(), True))


def toggleable() -> list[str]:
    return [k for k, v in CATEGORIES.items() if v[2]]


def order_key(category: str) -> int:
    return ORDER.index(category) if category in ORDER else len(ORDER)
