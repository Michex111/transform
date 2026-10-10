"""How far an agent's consent reaches: which folder, and whose history.

WHY these are separate from :mod:`agent_scope`: a scope answers *what kind of
action* an agent may take (read, convert, write, delete). These answer *over
what* — which part of the Drive, and how much of the conversion history. They
are the parameters of a grant rather than capabilities of their own, which is
why they live beside the grant and are decided on the same consent screen.

Keeping them in one module for the same reason as the scopes: the consent screen
that offers them, the repository that persists them, and the toolbox that
enforces them must agree, and there is exactly one place that decides what an
unrecognised stored value means.
"""

from enum import StrEnum


class FolderAccess(StrEnum):
    """The part of a user's Drive an agent may work in.

    The two values are deliberately not a nullable folder id: "the whole Drive"
    and "a folder, but the folder is gone" must be distinguishable. Collapsing
    them into one nullable column is exactly how a deleted folder would silently
    promote an agent to full-Drive access.
    """

    #: The whole Drive — the behaviour a grant had before scoping existed.
    ALL = "ALL"
    #: Only the bound folder and everything beneath it.
    FOLDER = "FOLDER"


class HistoryScope(StrEnum):
    """How much conversion history an agent may read."""

    #: Only conversions this grant started. The default, and the least an
    #: agent can be given: an agent asked to "convert my report" has no need
    #: for the user's unrelated history.
    AGENT = "AGENT"
    #: The user's entire conversion history, chosen explicitly on the consent
    #: screen so it is a visible decision rather than a default.
    ALL = "ALL"


#: Human wording for the consent screen. Kept next to the values so a new one
#: cannot be introduced without a sentence a user can read.
FOLDER_ACCESS_DESCRIPTIONS: dict[FolderAccess, str] = {
    FolderAccess.ALL: "All folders in your Drive",
    FolderAccess.FOLDER: "Only one folder you choose",
}

HISTORY_SCOPE_DESCRIPTIONS: dict[HistoryScope, str] = {
    HistoryScope.AGENT: "Only conversions it started",
    HistoryScope.ALL: "Your entire conversion history",
}


def coerce_folder_access(value: object) -> FolderAccess:
    """Read a stored folder-access value, failing closed.

    An unrecognised value reads as :attr:`FolderAccess.FOLDER`, which without a
    bound folder grants nothing. That direction matters: defaulting an unreadable
    value to ``ALL`` would turn a corrupted or future-written column into full
    Drive access for an agent the user had deliberately restricted.
    """
    if isinstance(value, FolderAccess):
        return value
    try:
        return FolderAccess(str(value))
    except ValueError:
        return FolderAccess.FOLDER


def coerce_history_scope(value: object) -> HistoryScope:
    """Read a stored history-scope value, failing closed to agent-only.

    Defaulting to ``ALL`` would hand an agent the user's whole history from a
    value we could not understand, so the narrow reading wins.
    """
    if isinstance(value, HistoryScope):
        return value
    try:
        return HistoryScope(str(value))
    except ValueError:
        return HistoryScope.AGENT


def is_folder_restricted(folder_access: FolderAccess, folder_id: str | None) -> bool:
    """Whether this binding must be enforced against a specific folder.

    ``True`` means every operation has to prove the target sits inside
    ``folder_id``. A restricted grant with **no** folder is not "unrestricted" —
    :func:`folder_scope_is_usable` is what the toolbox uses to deny it outright.
    """
    return folder_access is FolderAccess.FOLDER


def folder_scope_is_usable(folder_access: FolderAccess, folder_id: str | None) -> bool:
    """Whether a binding names a folder that can actually be enforced.

    A ``FOLDER`` grant whose folder is missing is unusable and must be denied.
    This arises legitimately: deleting a folder clears the reference (the column
    is ``ON DELETE SET NULL``), and the user has not thereby consented to the
    agent roaming the whole Drive. Denying is the only safe reading, and the user
    recovers by reconnecting the application.
    """
    if folder_access is FolderAccess.ALL:
        return True
    return bool(folder_id)
