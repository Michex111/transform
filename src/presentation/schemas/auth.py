from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    #: Optional on the wire, required by the sign-up form.
    #:
    #: The API keeps them optional because every account created before this
    #: feature existed has no name — the columns are nullable, so a required
    #: field would make the request schema reject a body the database is happy
    #: with, and would break every existing API client. The SPA always sends
    #: them, and the UI is where "please tell us your name" belongs.
    first_name: str | None = Field(default=None, max_length=50)
    last_name: str | None = Field(default=None, max_length=50)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    is_active: bool
    created_at: datetime
    #: False until the address is confirmed through the emailed link. Defaults
    #: to True so any caller constructing this without the field (older code,
    #: tests, an account created before the feature) reports the safe value:
    #: it drives a sign-in gate, and a missing field must never be the reason a
    #: working account is refused.
    email_verified: bool = True

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------
    first_name: str | None = None
    last_name: str | None = None
    #: ``"Ada Lovelace"``, whichever single name is set, else the username.
    #: Computed, never an ORM attribute — build this model with
    #: ``to_user_response()`` (``application/services/user_profile.py``) rather
    #: than ``model_validate(orm_row)``, which cannot supply it.
    display_name: str
    #: ``"AL"`` from the names, else the first two letters of the username.
    #: Never empty.
    initials: str
    #: A ready-to-render ``data:image/webp;base64,…`` URL, or null.
    #:
    #: A data URL rather than a serve endpoint on purpose: an ``<img>`` cannot
    #: carry the bearer token, so a ``GET``-able avatar would have to be either
    #: unauthenticated (user-id enumerable) or fetched into a blob URL. Avatars
    #: are tiny after downscaling, so inlining removes the whole problem along
    #: with its caching and CORS surface.
    avatar_url: str | None = None
    #: E.164. Set as soon as a number is submitted, whether or not it is verified.
    phone_number: str | None = None
    phone_verified: bool = False
    #: Folder completed conversions are saved into by default; null = root.
    default_save_folder_id: str | None = None


class UpdateProfileRequest(BaseModel):
    """Editable profile fields.

    Every field is optional and defaults to None, which means "leave
    unchanged" — an explicit empty string (or whitespace-only) clears it
    instead. That distinction is what lets the SPA send a partial patch without
    wiping the fields it did not mention.

    ``default_save_folder_id`` follows the same rule: absent leaves the stored
    value untouched, while an explicit ``null``/``""`` clears it back to root.
    """

    first_name: str | None = Field(default=None, max_length=50)
    last_name: str | None = Field(default=None, max_length=50)
    #: Must be a folder owned by the caller; a foreign/unknown id is a 404.
    default_save_folder_id: str | None = Field(default=None, max_length=64)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class RequestPhoneVerificationRequest(BaseModel):
    """The number to send a code to.

    Only a length bound here: the real validation is
    ``normalize_phone_number``, which returns a structured 400 the SPA can
    explain ("use international format, e.g. +1415…") instead of a generic
    Pydantic 422 whose message is a regex.
    """

    phone_number: str = Field(min_length=6, max_length=20)


class VerifyPhoneRequest(BaseModel):
    """The code the user read out of the SMS.

    Bounds only — the shape (6 digits) is checked against the stored HMAC, so a
    wrong-shaped guess costs an attempt exactly like a wrong-length one.
    """

    code: str = Field(min_length=4, max_length=10)


class PhoneVerificationStatusResponse(BaseModel):
    """The state of phone verification after any phone-related call.

    One shape for every outcome (requested, resent, verified, or queried) so the
    settings UI has a single source of truth for what to render.
    """

    phone_number: str | None = None
    phone_verified: bool = False
    #: Seconds until the code that was just sent stops working.
    expires_in_seconds: int | None = None
    #: Seconds until another code may be sent. The API suppresses a too-early
    #: resend silently (202, nothing sent) so the endpoint cannot be used to
    #: probe, so the UI must run its own countdown from this value.
    resend_available_in_seconds: int | None = None


class DeleteHistoryPreviewResponse(BaseModel):
    """What a bulk history delete would remove, for the confirmation dialog."""

    range: str
    #: ISO timestamp of the lower bound actually used; null for ``all``.
    since: datetime | None = None
    count: int
    #: Jobs in the window that are still running and will therefore be KEPT.
    active_count: int


class DeleteHistoryRangeResponse(BaseModel):
    """Outcome of a bulk history delete."""

    deleted_count: int
    #: Jobs inside the window that were left alone because they are still running.
    skipped_active: int
    range: str


class DeleteAccountRequest(BaseModel):
    """Confirm-to-delete body.

    Both fields are required and both are checked: the password because a stolen
    live session must not be enough to destroy an account, and ``confirm``
    because one stray click must not either. ``confirm`` must be exactly
    ``DELETE``.
    """

    password: str = Field(min_length=1, max_length=128)
    confirm: str = Field(min_length=1, max_length=20)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    expires_in: int | None = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class VerifyEmailRequest(BaseModel):
    """The token extracted from the verification link's query string."""

    token: str = Field(min_length=1, max_length=512)


class VerifyEmailResponse(BaseModel):
    """Outcome of a verification attempt.

    ``already_verified`` distinguishes "this token was just consumed" from "this
    account was already verified" so the SPA can show an accurate message
    instead of a misleading error when a user clicks the link twice (or opened
    it in two tabs, which is common — the link is easily opened twice by
    accident).
    """

    ok: bool
    already_verified: bool = False
    #: Returned so the sign-in form can pre-fill it; saves the user from having
    #: to remember which of their addresses they signed up with.
    username: str | None = None
    message: str


class ResendVerificationRequest(BaseModel):
    """Request a fresh verification email.

    Keyed on email rather than username so a user who has forgotten their
    username can still recover — they know the address, and it is the only
    thing that can actually receive the link.
    """

    email: str = Field(min_length=3, max_length=255)


class ForgotPasswordRequest(BaseModel):
    """Request a password-reset email.

    Keyed on the address, for the same reason as ``ResendVerificationRequest``:
    a user who cannot sign in is precisely the user who may not remember their
    username, and the address is what actually receives the link.

    Bounds only — deliberately no format validation. The response is identical
    whether or not the address matches an account, so a stricter schema would
    add nothing except a 422 that leaks which shapes the server considers
    "valid". Matching is a case-insensitive comparison against the stored value.
    """

    email: str = Field(min_length=3, max_length=255)


class ResetPasswordRequest(BaseModel):
    """The token from the reset link, plus the password to set.

    ``new_password`` carries the same 8..128 bounds as ``UserCreateRequest`` and
    ``ChangePasswordRequest``: the minimum is the account's actual password
    policy, and enforcing it here means a too-short password is a schema 422
    rather than a link-consuming request that fails at the last step.
    """

    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=8, max_length=128)


class ResetPasswordResponse(BaseModel):
    """Outcome of a successful reset.

    ``username`` is echoed so the SPA can pre-fill the sign-in form — the user
    arrived from an email and may not remember which account they used, and
    making them guess after a successful reset is a pointless dead end.

    ``username`` is optional because only the success path constructs this
    model; the failure paths raise an HTTPException with a plain string detail.
    """

    ok: bool
    username: str | None = None
    message: str
