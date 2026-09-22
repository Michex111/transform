from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)


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
