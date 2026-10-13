"""Derived user-profile presentation values.

``display_name``, ``initials`` and ``avatar_url`` are **computed**, not stored:
they are projections of ``first_name``/``last_name`` and of the avatar bytes.
They cannot be produced by ``model_config = ConfigDict(from_attributes=True)``
on ``UserResponse``, so every call site that returned a user had to stop doing
``UserResponse.model_validate(orm_row)`` — which would now fail on the missing
required fields — and go through ``to_user_response()`` instead.

The rules live here rather than in the router for the same reason the SPA keeps
an identical copy in ``web/src/lib/avatar.ts``: the sidebar, the mobile header
and the settings page all ask the same question and must never disagree. The
two implementations are deliberately the same rule so a client that falls back
locally (during an independent SPA/API deploy) cannot visibly diverge from the
server.

Everything here is pure and takes the ORM row (or any object with the same
attributes), so it is testable without a database.
"""

from base64 import b64encode
from typing import TYPE_CHECKING

from src.presentation.schemas.auth import UserResponse

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a runtime cycle
    from src.infrastructure.database.models import UserModel


def _clean_name(value: str | None) -> str | None:
    """``value`` trimmed, or ``None`` when absent or only whitespace.

    Whitespace-only counts as absent: ``" "`` renders as an invisible name, and
    treating it as present would put a blank line where the username fallback
    belongs.
    """
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def display_name_for(user: "UserModel") -> str:
    """The name to print beside the avatar.

    ``"Ada Lovelace"`` when both names are known, whichever single name is known
    otherwise, and finally the username — every account created before names
    existed, plus any account that has not filled them in yet. Never empty, so
    the caller never has to fall back to a placeholder.
    """
    parts = [
        part
        for part in (_clean_name(user.first_name), _clean_name(user.last_name))
        if part is not None
    ]
    if parts:
        return " ".join(parts)
    return _clean_name(user.username) or "Account"


def initials_for(user: "UserModel") -> str:
    """The monogram shown when there is no picture.

    First letter of each name when available (``"Ada Lovelace"`` → ``"AL"``),
    the single letter when only one name is set, then the first two letters of
    the username, and finally ``"?"``. Never empty: an empty string would
    collapse the avatar tile into an anonymous blank circle.
    """
    first = _clean_name(user.first_name)
    last = _clean_name(user.last_name)
    from_names = (first[:1] if first else "") + (last[:1] if last else "")
    if from_names:
        return from_names.upper()

    username = _clean_name(user.username)
    if username:
        return username[:2].upper()
    return "?"


def avatar_data_url_for(user: "UserModel") -> str | None:
    """The avatar as an inline ``data:`` URL, or ``None`` when unset.

    Inlined rather than served: see the note on ``UserResponse.avatar_url``.
    The content type is stored alongside the bytes precisely so this can be
    built without sniffing the payload again; it is never derived from
    user-supplied input, so it cannot be used to inject an unexpected MIME type
    into the URL.
    """
    data = getattr(user, "avatar_data", None)
    if not data:
        return None
    content_type = getattr(user, "avatar_content_type", None) or "image/webp"
    return f"data:{content_type};base64,{b64encode(data).decode('ascii')}"


def to_user_response(user: "UserModel") -> UserResponse:
    """Build the API representation of ``user`` explicitly.

    Explicit rather than ``model_validate``: three of the fields are computed,
    so attribute-based validation cannot produce a valid model. Listing every
    field here also means adding one to ``UserResponse`` fails loudly (it is a
    required constructor argument) instead of silently serialising as absent.
    """
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        # Reported from the row, not left to the schema default: an unverified
        # account must keep answering `false` so the SPA shows the right state.
        email_verified=user.email_verified,
        first_name=user.first_name,
        last_name=user.last_name,
        display_name=display_name_for(user),
        initials=initials_for(user),
        avatar_url=avatar_data_url_for(user),
        phone_number=user.phone_number,
        phone_verified=user.phone_verified,
        default_save_folder_id=user.default_save_folder_id,
    )
