from __future__ import annotations

from enum import IntEnum


class Role(IntEnum):
    USER = 0
    TRUSTED = 10
    MODERATOR = 20
    ADMIN = 30
    GROUP_OWNER = 40
    SUPER_ADMIN = 50
    DEVELOPER = 60
    OWNER = 70

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()


# Roles a group admin may hand out inside their own tenant.
TENANT_ASSIGNABLE = (Role.TRUSTED, Role.MODERATOR, Role.ADMIN)
# Roles the owner may hand out platform-wide.
GLOBAL_ASSIGNABLE = (Role.SUPER_ADMIN, Role.DEVELOPER)


def parse_role(text: str, allowed: tuple[Role, ...]) -> Role | None:
    key = text.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"mod": Role.MODERATOR, "trusted": Role.TRUSTED, "superadmin": Role.SUPER_ADMIN,
               "dev": Role.DEVELOPER, "super_admin": Role.SUPER_ADMIN}
    role = aliases.get(key)
    if role is None:
        try:
            role = Role[key.upper()]
        except KeyError:
            return None
    return role if role in allowed else None
