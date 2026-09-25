from typing import Annotated

import asyncio
import io
import logging
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.security import OAuth2PasswordRequestForm
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exceptions.file_system_exceptions import FolderNotFoundError
from src.application.ports.email_port import EmailPort
from src.application.ports.sms_port import SmsPort
from src.application.services.email_templates import (
    build_password_reset_email,
    build_verification_email,
)
from src.application.services.file_magic import validate_upload_signature
from src.application.services.file_service import FileService
from src.application.services.sms_templates import build_phone_verification_sms
from src.application.services.user_profile import to_user_response
from src.domain.security.enitities.email_verification import (
    cooldown_elapsed,
    hash_verification_token,
    issue_verification_token,
)
# The reset flow borrows the verification flow's primitives rather than
# reimplementing them: a reset token has the same digest, expiry and cooldown
# semantics, so `token_is_expired` is deliberately the *same* function both
# flows rely on and the two can never disagree at the boundary.
from src.domain.security.enitities.one_time_token import hash_token, token_is_expired
from src.domain.security.enitities.password_reset import issue_password_reset_token
from src.domain.security.enitities.phone_verification import (
    attempts_exhausted,
    code_is_expired,
    code_matches,
    cooldown_elapsed as phone_cooldown_elapsed,
    hash_verification_code,
    issue_phone_code,
    normalize_phone_number,
    seconds_until_resend_allowed,
)
from src.domain.security.exceptions import InvalidPhoneNumber
from src.infrastructure.adapters.storage.sanitize import extension_from_filename
from src.infrastructure.auth.jwt_provider import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
    verify_refresh_token,
)
from src.infrastructure.config.settings import Settings, get_settings
from src.infrastructure.database.models import (
    APIKeyModel,
    ConversionJobModel,
    CreditTransactionModel,
    MonthlyCreditModel,
    UserFileModel,
    UserFolderModel,
    UserModel,
    UserSubscriptionModel,
)
from src.infrastructure.database.session import get_db_session
from src.infrastructure.logging.audit import log_data_access
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import (
    get_email_sender,
    get_file_service,
    get_sms_sender,
)
from src.presentation.schemas.auth import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    ForgotPasswordRequest,
    PhoneVerificationStatusResponse,
    RefreshTokenRequest,
    RequestPhoneVerificationRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    ResetPasswordResponse,
    TokenResponse,
    UpdateProfileRequest,
    UserCreateRequest,
    UserResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
    VerifyPhoneRequest,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])

# A valid Argon2 hash for a throwaway password. On a login attempt for a
# non-existent username we still run one verification against this hash so the
# response time does not reveal whether the account exists (user enumeration).
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$2SAM5hX5kD0fhZyVF+L3/Q"
    "$+x3jU3P9gEMIZVEGPutfCS3UmAEaZg31jFuFCwYvGgs"
)

#: Machine-readable marker the SPA keys on to show the "check your inbox"
#: recovery UI instead of a generic error. Sent as a structured ``detail``
#: object so the human-readable message travels with it.
EMAIL_NOT_VERIFIED_CODE = "EMAIL_NOT_VERIFIED"

#: A deactivated account. Not a credential problem — the password was correct —
#: so it is a 403 with its own code rather than a 401, and the message says what
#: to do about it.
ACCOUNT_INACTIVE_CODE = "ACCOUNT_INACTIVE"

#: Deliberately identical for "no such token", "already used", and "expired".
#: Distinguishing them would tell an attacker probing with guessed tokens which
#: guesses were structurally valid, and none of the three cases is actionable to
#: a legitimate user beyond "request a new link".
_INVALID_TOKEN_MESSAGE = (
    "This verification link is invalid or has expired. Request a new one and "
    "try again."
)

#: Always returned by the resend endpoint, whether or not the address exists.
#: Returning 404 for an unknown address would turn this into an account
#: enumeration oracle.
_RESEND_ACCEPTED_MESSAGE = (
    "If an account with that email address needs verification, a new link is on "
    "its way."
)

#: Deliberately identical for "no such token", "already used", and "expired".
#: Same reasoning as ``_INVALID_TOKEN_MESSAGE``: distinguishing them would tell
#: an attacker probing with guessed tokens which guesses were structurally
#: valid, and none of the three is actionable to a legitimate user beyond
#: "request a new link".
_INVALID_RESET_TOKEN_MESSAGE = (
    "This password reset link is invalid or has expired. Request a new one and "
    "try again."
)

#: Always returned by ``forgot-password``, whatever the state of the address —
#: unknown, already verified, deactivated, or suppressed by the resend cooldown.
#: It is a statement about what *may* have happened, never about whether an
#: account exists: this endpoint is unauthenticated, so anything that varied
#: with account state would turn it into an enumeration oracle.
_FORGOT_PASSWORD_ACCEPTED_MESSAGE = (
    "If an account with that email address exists, we've sent instructions for "
    "resetting your password."
)

#: The message on a successful reset. Echoed to the SPA alongside the username
#: so the sign-in form can be pre-filled with the account the user just reset.
_PASSWORD_UPDATED_MESSAGE = (
    "Your password has been updated. Sign in with your new password."
)

#: Hard cap on an avatar upload. Enforced *before* decoding: a 2 MB ceiling is
#: what makes it safe to keep the bytes on the row and to run Pillow inside a
#: request (a few hundred milliseconds, not several seconds).
MAX_AVATAR_UPLOAD_BYTES = 2 * 1024 * 1024

#: Avatars are re-encoded to a square WebP at this size. 256px covers the
#: largest place one is rendered (a 2x 128px tile) without carrying detail
#: nobody sees.
AVATAR_SIZE_PX = 256

#: WebP quality/speed. 82 is visually indistinguishable from lossless at this
#: size; ``method=4`` is the default-effort/quality balance.
AVATAR_WEBP_QUALITY = 82
AVATAR_WEBP_METHOD = 4

#: Extensions accepted for an avatar. Narrower than the converter's supported
#: formats on purpose — these are the three types every browser can render and
#: Pillow can re-encode.
_AVATAR_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})

#: The exact word a user must type (and the API must compare against) before an
#: account is destroyed. A live session plus one stray click must not be enough.
DELETE_ACCOUNT_CONFIRMATION = "DELETE"

#: A single user-facing message for every wrong-code case that is *not*
#: distinguishable to the user, keeps the SPA copy stable.
_PHONE_CODE_INVALID_MESSAGE = "That code is not correct. Check the message and try again."


def _error(code: str, message: str) -> dict[str, str]:
    """Build the structured ``detail`` object the SPA keys on.

    A machine-readable ``code`` travels with the human-readable ``message`` so
    the client can show localised/contextual copy for the cases it knows and
    fall back to the server's text otherwise — instead of matching on prose.
    """
    return {"code": code, "message": message}


def _clean_optional_name(value: str | None) -> str | None:
    """Trim a submitted name, mapping "empty" to ``None``.

    Stored as NULL rather than ``""`` so "is a name set?" is one check
    everywhere (including ``display_name_for``) and so an accidental
    whitespace-only submission cannot render as a blank line where the username
    fallback belongs.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def email_verification_is_enforced(settings: Settings) -> bool:
    """Whether an unverified account is actually refused at sign-in.

    Enforcement needs two things: the operator asked for it
    (``EMAIL_VERIFICATION_REQUIRED``) *and* a transport exists that can deliver
    the link. The second condition is what keeps the gate honest.

    Requiring confirmation for a link that can never be sent would lock out
    every new sign-up permanently, with no self-service way out — the user
    cannot verify an address they never receive mail at. So when delivery is not
    configured the gate stays open and ``Settings.validate()`` logs an ERROR
    naming the missing setting. Accounts are still created unverified and marked
    correctly, so enforcement switches on by itself the moment a transport is
    configured — no data migration, no code change.

    The inverse (enforcing while delivery is broken) is the failure this
    function exists to prevent: it turns "email is misconfigured" into "nobody
    can sign up", and it stays invisible until a real user complains.
    """
    return (
        settings.EMAIL_VERIFICATION_REQUIRED
        and settings._resolve_email_backend() != "console"
    )


def build_verification_url(token: str, settings: Settings) -> str:
    """The link that lands the user back on the SPA's verification page.

    The token is query-encoded: it is URL-safe base64, but ``-``/``_`` still
    appear in it, and percent-encoding prevents a client that re-encodes or
    normalises the URL from mangling the credential.
    """
    base = settings.APP_BASE_URL.rstrip("/")
    return f"{base}/verify-email?token={quote(token, safe='')}"


async def issue_and_send_verification_email(
    *,
    user: UserModel,
    db: AsyncSession,
    settings: Settings,
    email_sender: EmailPort,
    respect_cooldown: bool,
) -> bool:
    """Mint a verification token for ``user`` and email it.

    Returns True when the message was handed to the transport.

    The token is persisted *before* the send, so a crash between the two cannot
    leave a link in the user's inbox that the database has never heard of. The
    recoverable failure is "an email was not sent", not "the link we just sent
    is dead". Overwriting the previous hash also makes a newly requested link
    invalidate the old one, so a stale link cannot be used afterwards.

    ``respect_cooldown`` is False for the initial registration email (there is
    nothing to throttle yet) and True for explicit resends.
    """
    if respect_cooldown and not cooldown_elapsed(
        user.email_verification_sent_at,
        cooldown_seconds=settings.EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
    ):
        # Not an error: the caller answers 202 regardless, so a caller cannot
        # use the response to probe account state or to time a mail-bomb.
        logger.info("Verification resend suppressed by cooldown for user %s", user.id)
        return False

    token = issue_verification_token(ttl_hours=settings.EMAIL_VERIFICATION_TTL_HOURS)
    user.email_verification_token_hash = token.hashed
    user.email_verification_expires_at = token.expires_at
    await db.commit()

    message = build_verification_email(
        to=user.email,
        username=user.username,
        verification_url=build_verification_url(token.raw, settings),
        ttl_hours=settings.EMAIL_VERIFICATION_TTL_HOURS,
        app_base_url=settings.APP_BASE_URL,
        from_name=settings.EMAIL_FROM_NAME,
    )

    try:
        await email_sender.send(message)
    except Exception as exc:  # noqa: BLE001 - delivery must not fail the request
        # A registration that succeeds but cannot email is still the correct
        # outcome: the account exists, and the SPA surfaces a "resend the email"
        # affordance from `email_verified: false`. Raising here would return 500
        # *after* the row was committed, so the user could neither retry the
        # signup (409) nor see why it failed.
        #
        # `sent_at` is intentionally left untouched so the resend path is not
        # throttled for a message that never went out.
        logger.error(
            "Failed to send the verification email to user %s: %s", user.id, exc
        )
        return False

    user.email_verification_sent_at = datetime.now(UTC)
    await db.commit()
    return True


def build_password_reset_url(token: str, settings: Settings) -> str:
    """The link that lands the user back on the SPA's reset page.

    Query-encoded for the same reason as ``build_verification_url``: the token
    is URL-safe base64 but is a credential, and percent-encoding stops a client
    that re-encodes or normalises the URL from mangling it. ``safe=''`` encodes
    every reserved character, so nothing in the token can be read as a URL
    delimiter.
    """
    base = settings.APP_BASE_URL.rstrip("/")
    return f"{base}/reset-password?token={quote(token, safe='')}"


async def issue_and_send_password_reset_email(
    *,
    user: UserModel,
    db: AsyncSession,
    settings: Settings,
    email_sender: EmailPort,
    respect_cooldown: bool,
) -> bool:
    """Mint a password-reset token for ``user`` and email it.

    Returns True when the message was handed to the transport.

    The hash is persisted *before* the send, so a crash between the two cannot
    leave a link in the user's inbox that the database has never heard of. The
    recoverable failure is "no email arrived", not "the link we just sent is
    dead" — the same reasoning as the verification path. Writing a new hash also
    invalidates any previous reset link, so an earlier (possibly attacker-
    triggered) request cannot race the user to the newer one.

    ``respect_cooldown`` is a caller's decision rather than a property of this
    helper, mirroring ``issue_and_send_verification_email``: every current
    caller passes True, because the only way a reset email is sent is an
    explicit ``forgot-password`` request — the path that can be aimed at a third
    party's inbox. A caller that legitimately has nothing to throttle (a future
    first-send at registration time, say) can pass False without adding a
    second code path.
    """
    if respect_cooldown and not cooldown_elapsed(
        user.password_reset_sent_at,
        cooldown_seconds=settings.PASSWORD_RESET_RESEND_COOLDOWN_SECONDS,
    ):
        # Not an error: the caller answers 202 regardless, so the response
        # cannot be used to probe account state or to time a mail-bomb.
        logger.info("Password reset email suppressed by cooldown for user %s", user.id)
        return False

    token = issue_password_reset_token(ttl_minutes=settings.PASSWORD_RESET_TTL_MINUTES)
    user.password_reset_token_hash = token.hashed
    user.password_reset_expires_at = token.expires_at
    await db.commit()

    message = build_password_reset_email(
        to=user.email,
        username=user.username,
        reset_url=build_password_reset_url(token.raw, settings),
        ttl_minutes=settings.PASSWORD_RESET_TTL_MINUTES,
        app_base_url=settings.APP_BASE_URL,
        from_name=settings.EMAIL_FROM_NAME,
    )

    try:
        await email_sender.send(message)
    except Exception as exc:  # noqa: BLE001 - delivery must not fail the request
        # Same contract as the verification send: the token is already
        # persisted, so raising here would turn "the mail provider is down" into
        # a 500 for a request whose only recoverable failure is a missing email.
        #
        # `sent_at` is intentionally left untouched so the resend path is not
        # throttled for a message that never went out.
        logger.error(
            "Failed to send the password reset email to user %s: %s", user.id, exc
        )
        return False

    user.password_reset_sent_at = datetime.now(UTC)
    await db.commit()
    return True


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: UserCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    email_sender: Annotated[EmailPort, Depends(get_email_sender)],
) -> UserResponse:
    """Create an unverified account and email it a verification link."""
    query = select(UserModel).where(
        or_(UserModel.username == payload.username, UserModel.email == payload.email)
    )
    existing = await db.execute(query)
    if existing.scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        )

    user = UserModel(
        username=payload.username,
        email=payload.email,
        hashed_password=await asyncio.to_thread(hash_password, payload.password),
        is_active=True,
        # Names are optional on the wire; an omitted or blank one is stored as
        # NULL so the derived display name falls back to the username.
        first_name=_clean_optional_name(payload.first_name),
        last_name=_clean_optional_name(payload.last_name),
        # Explicit rather than relying on the column default: the verification
        # email is only meaningful for an account that is genuinely unverified.
        email_verified=False,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent registration with the same username/email can slip past
        # the pre-check above; surface it as a clean 409, not a 500.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        ) from None

    settings = get_settings()
    await issue_and_send_verification_email(
        user=user,
        db=db,
        settings=settings,
        email_sender=email_sender,
        respect_cooldown=False,
    )

    # ``expire_on_commit=False`` keeps every attribute populated after commit
    # (id, created_at, ...) so no refresh round-trip is needed. `email_verified`
    # is False here, which is how the SPA knows to show "check your inbox".
    return to_user_response(user)


@router.post("/verify-email", response_model=VerifyEmailResponse)
async def verify_email(
    payload: VerifyEmailRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> VerifyEmailResponse:
    """Consume a verification token and activate the account.

    The pre-read exists only to give an accurate message; the activation itself
    is a single conditional UPDATE, so two concurrent clicks of the same link
    cannot both succeed. The statement matches only rows whose stored hash is
    still set and unexpired, so exactly one request observes a row.
    """
    token_hash = hash_verification_token(payload.token)
    now = datetime.now(UTC)

    result = await db.execute(
        select(UserModel).where(UserModel.email_verification_token_hash == token_hash)
    )
    user = result.scalars().first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_TOKEN_MESSAGE,
        )

    if user.email_verified:
        # The token outlived a verification performed some other way. Report
        # success (this is a good outcome) and clear the now-pointless token.
        user.email_verification_token_hash = None
        user.email_verification_expires_at = None
        await db.commit()
        return VerifyEmailResponse(
            ok=True,
            already_verified=True,
            username=user.username,
            message="Your email address is already verified. You can sign in.",
        )

    if token_is_expired(user.email_verification_expires_at, now=now):
        # Clear the dead token so the row reflects reality.
        user.email_verification_token_hash = None
        user.email_verification_expires_at = None
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_TOKEN_MESSAGE,
        )

    consumed = await db.execute(
        update(UserModel)
        .where(
            UserModel.id == user.id,
            UserModel.email_verification_token_hash == token_hash,
            # `>` against a nullable column excludes NULL, so a token with no
            # recorded expiry can never be accepted (fail closed).
            UserModel.email_verification_expires_at > now,
        )
        .values(
            email_verified=True,
            email_verified_at=now,
            # Clearing the hash is what makes the token single-use: a replayed
            # link no longer matches any row.
            email_verification_token_hash=None,
            email_verification_expires_at=None,
        )
        .returning(UserModel.id)
        .execution_options(synchronize_session=False)
    )

    if consumed.first() is None:
        # Lost a race with a concurrent click, or the token expired between the
        # read and the write. Nothing was activated by this request.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_TOKEN_MESSAGE,
        )

    await db.commit()
    return VerifyEmailResponse(
        ok=True,
        username=user.username,
        message="Your email address is verified. You can now sign in.",
    )


@router.post(
    "/resend-verification",
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_verification_email(
    payload: ResendVerificationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    email_sender: Annotated[EmailPort, Depends(get_email_sender)],
) -> dict[str, str]:
    """Send a fresh verification link.

    Always answers 202 with the same body, whether or not the address belongs to
    an account, whether or not it is already verified, and whether or not the
    resend cooldown suppressed the send. Any of those distinctions would let an
    unauthenticated caller enumerate registered addresses, which is the main
    thing this endpoint must not do.
    """
    settings = get_settings()

    # Case-insensitive match: registration stores the address exactly as typed,
    # so a user who capitalised it differently when signing up could otherwise
    # never request a new link.
    result = await db.execute(
        select(UserModel).where(
            func.lower(UserModel.email) == payload.email.strip().lower()
        )
    )
    user = result.scalars().first()

    if user is not None and user.is_active and not user.email_verified:
        await issue_and_send_verification_email(
            user=user,
            db=db,
            settings=settings,
            email_sender=email_sender,
            respect_cooldown=True,
        )

    return {"message": _RESEND_ACCEPTED_MESSAGE}


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    email_sender: Annotated[EmailPort, Depends(get_email_sender)],
) -> dict[str, str]:
    """Email a password-reset link, without ever revealing whether it was sent.

    Always answers 202 with the identical body — for an unknown address, an
    already-verified account, a deactivated one, and a send suppressed by the
    resend cooldown alike. Any difference in status or body would make this
    unauthenticated endpoint an account-enumeration oracle, which is exactly
    what a recovery flow must not be.

    No email is sent for an inactive account: the operator disabled it on
    purpose, so handing its holder a working reset link would be a way back in.
    The response is still the generic 202, so that case is indistinguishable
    from the others.

    Deliberately *not* restricted to unverified accounts. A verified account
    needs reset just as much as any other; the "already verified" state only
    changes whether a send is useful, never what the caller is told.
    """
    settings = get_settings()

    # Case-insensitive match, like the resend endpoint: registration stores the
    # address exactly as typed, so a user who capitalised it differently when
    # signing up could otherwise never recover their account.
    result = await db.execute(
        select(UserModel).where(
            func.lower(UserModel.email) == payload.email.strip().lower()
        )
    )
    user = result.scalars().first()

    if user is not None and user.is_active:
        await issue_and_send_password_reset_email(
            user=user,
            db=db,
            settings=settings,
            email_sender=email_sender,
            respect_cooldown=True,
        )

    return {"message": _FORGOT_PASSWORD_ACCEPTED_MESSAGE}


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    payload: ResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResetPasswordResponse:
    """Consume a reset token, set the new password, and prove the mailbox.

    The pre-read exists only to give an accurate result; the change itself is a
    single conditional ``UPDATE``, so two concurrent submissions of the same
    link cannot both succeed (the second matches no row). ``rowcount`` is not
    used — aiosqlite does not report it reliably — so ``RETURNING users.id`` is
    the evidence that a row was actually updated. This is the same pattern as
    ``verify_email``, and the same reason for it.

    **This does not revoke other sessions**, and the API does not claim it does
    — the same honest limitation ``change_password`` documents. Access tokens
    are stateless JWTs and this deployment keeps no server-side token store, so
    a device that is already signed in stays signed in until its token expires
    (30 minutes) and then has to sign in with the new password. Revoking them
    would need a denylist or per-user token versioning, which is a real feature
    rather than something this endpoint can promise. The frontend copy mirrors
    this rather than implying "you have been signed out everywhere".

    Reaching the link proves control of the mailbox, which is exactly what email
    verification proves, so the address is marked verified here. That is
    deliberate rather than incidental: without it, a user who never clicked the
    original verification link would set a new password and still be refused at
    sign-in by the verification gate, with no self-service way out.
    """
    token_hash = hash_token(payload.token)
    now = datetime.now(UTC)

    result = await db.execute(
        select(UserModel).where(UserModel.password_reset_token_hash == token_hash)
    )
    user = result.scalars().first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_RESET_TOKEN_MESSAGE,
        )

    if token_is_expired(user.password_reset_expires_at, now=now):
        # Clear the dead token so the row reflects reality rather than holding
        # a hash that can never be consumed.
        user.password_reset_token_hash = None
        user.password_reset_expires_at = None
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_RESET_TOKEN_MESSAGE,
        )

    # Argon2 is CPU-heavy, so it runs off the event loop — and *before* the
    # statement, so the conditional UPDATE below stays a single atomic step.
    # Hashing in between the read and the write would widen the window in which
    # two requests both believe the token is still live.
    new_password_hash = await asyncio.to_thread(hash_password, payload.new_password)

    consumed = await db.execute(
        update(UserModel)
        .where(
            UserModel.id == user.id,
            UserModel.password_reset_token_hash == token_hash,
            # `>` against a nullable column excludes NULL, so a token with no
            # recorded expiry can never be accepted (fail closed).
            UserModel.password_reset_expires_at > now,
        )
        .values(
            hashed_password=new_password_hash,
            # Clearing the hash is what makes the token single-use: a replayed
            # link no longer matches any row.
            password_reset_token_hash=None,
            password_reset_expires_at=None,
            # Proving control of the mailbox is what the verification gate
            # checks for, so a completed reset satisfies it too. `coalesce`
            # preserves an existing verification timestamp instead of
            # overwriting the audit trail with "just now".
            email_verified=True,
            email_verified_at=func.coalesce(UserModel.email_verified_at, now),
        )
        .returning(UserModel.id)
        .execution_options(synchronize_session=False)
    )

    if consumed.first() is None:
        # Lost a race with a concurrent submission, or the token expired between
        # the read and the write. Nothing was changed by this request.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_RESET_TOKEN_MESSAGE,
        )

    await db.commit()
    # Audit the credential change. The token and the new password are never
    # logged — only the fact that this account's password was reset.
    log_data_access(
        user_id=str(user.id), action="update", resource="user_password"
    )
    return ResetPasswordResponse(
        ok=True,
        username=user.username,
        message=_PASSWORD_UPDATED_MESSAGE,
    )


async def _resolve_login_user(
    db: AsyncSession, identifier: str
) -> tuple[UserModel | None, str]:
    """Resolve what the user typed at sign-in to exactly one account.

    Sign-in accepts **either the username or the email address**, because the
    address is what the rest of the app shows as the account's identity (the
    shell prints it under the display name, verification mail goes to it) and
    typing it at the sign-in form is the obvious thing to do. It used to match
    ``username`` only, so a correct address with a correct password answered
    "Incorrect username or password" — indistinguishable from a wrong password.

    Returns the account and how it matched, or ``(None, reason)``.

    Resolution is deliberately two-stage, and the second stage **refuses** an
    ambiguous identifier rather than picking a row:

    1. An exact username match, first. This is the original behaviour and it
       stays first, so every existing account resolves exactly as it always has
       — including the case where one account's username is another account's
       address, which stays deterministic instead of becoming a coin toss.
    2. Only if nothing matched exactly, a case-insensitive match across the
       username *and* the email. This is what makes `Michael` reachable by
       typing `michael`, and `Ada@Example.com` reachable by typing it in any
       case.

    Stage 2 is the reason ambiguity has to be handled explicitly: this database
    genuinely holds `michael` and `Michael` as two separate accounts, so
    resolving a case-insensitive hit by picking the first row would sign the
    user into an account they did not name. Two or more candidates therefore
    resolve to nothing, and the caller answers the same generic 401 — the user
    has to type the case they registered with.

    Enumeration: stage 2 attempts no password check of its own, and the caller
    burns the dummy hash whenever this returns no user, so a miss costs the same
    as a hit. The failure text is identical for "no such account" and "wrong
    password", so nothing here distinguishes them.
    """
    # 1. Exact username — unchanged behaviour, and it must win any tie.
    exact = (
        await db.execute(select(UserModel).where(UserModel.username == identifier))
    ).scalars().first()
    if exact is not None:
        return exact, "username"

    # 2. Case-insensitive, across both identifying fields at once, so an
    #    identifier that matches one account by address and a different account
    #    by username is caught as ambiguous rather than silently resolved.
    lowered = identifier.strip().lower()
    candidates = (
        await db.execute(
            select(UserModel).where(
                or_(
                    func.lower(UserModel.username) == lowered,
                    func.lower(UserModel.email) == lowered,
                )
            )
        )
    ).scalars().all()

    if len(candidates) != 1:
        return None, "ambiguous" if len(candidates) > 1 else "not_found"

    matched = candidates[0]
    # Report which field actually matched, for the message the caller may log.
    method = "email" if (matched.email or "").lower() == lowered else "username"
    return matched, method


@router.post("/token", response_model=TokenResponse)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> TokenResponse:
    user, matched_by = await _resolve_login_user(db, form_data.username)

    # Argon2 is CPU-heavy, so run it off the event loop. When the identifier
    # resolves to no account we still verify against a dummy hash so both
    # branches cost the same (prevents username enumeration via response timing).
    # An ambiguous identifier lands here too, and on purpose: it must be
    # indistinguishable from a miss.
    if user is None:
        await asyncio.to_thread(verify_password, form_data.password, _DUMMY_PASSWORD_HASH)
        if matched_by == "ambiguous":
            # Worth a line: the credentials may well be right and the user is
            # stuck on a case-sensitivity they cannot see. Never log the
            # identifier itself — it is an address or a username, and the audit
            # trail must not start collecting either verbatim.
            logger.warning(
                "Sign-in identifier matched more than one account "
                "(case-insensitive collision); refusing to guess."
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not await asyncio.to_thread(verify_password, form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Checked after the password, like the verification gate below, so neither
    # can be used to work out which accounts exist.
    #
    # Without this, a deactivated account was issued a perfectly valid token and
    # the SPA navigated into the app — where every single request then failed,
    # because `get_current_user` refuses inactive users. That half-signed-in
    # state reads as a broken application rather than a disabled account.
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": ACCOUNT_INACTIVE_CODE,
                "message": (
                    "This account has been deactivated. Contact support if you "
                    "think that is a mistake."
                ),
            },
        )

    settings = get_settings()
    if email_verification_is_enforced(settings) and not user.email_verified:
        # 403, not 401: the credentials were correct, so the SPA must not
        # discard them and bounce the user into a blank sign-in loop — it shows
        # the "check your inbox / resend" recovery instead. Checked *after* the
        # password so this cannot be used to discover which usernames exist.
        #
        # The address is included so the SPA can offer a working "resend" button.
        # That is not a disclosure: this branch is only reachable with a correct
        # username *and* password, so the caller has already proved they own the
        # account. Without it the resend endpoint (keyed on the address) would
        # be unusable from this screen when the user signed in by username.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": EMAIL_NOT_VERIFIED_CODE,
                "email": user.email,
                "message": (
                    "Your email address is not verified yet. Open the link we "
                    "emailed you to activate your account, or request a new one."
                ),
            },
        )

    token = create_access_token(data={"sub": str(user.id)})
    return TokenResponse(
        access_token=token,
        refresh_token=create_refresh_token(data={"sub": str(user.id)}),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_access_token(
    payload: RefreshTokenRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> TokenResponse:
    """Exchange a valid refresh token for a fresh access token."""
    user_id = verify_refresh_token(payload.refresh_token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(UserModel).where(UserModel.id == user_id_int))
    user = result.scalars().first()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive user",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(data={"sub": str(user.id)}),
        refresh_token=create_refresh_token(data={"sub": str(user.id)}),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser) -> UserResponse:
    """Return the current user's profile.

    Built with ``to_user_response`` rather than ``model_validate`` because
    ``display_name``/``initials``/``avatar_url`` are computed, not ORM columns.
    """
    return to_user_response(current_user)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    payload: UpdateProfileRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> UserResponse:
    """Update the editable profile fields.

    Only the keys actually present in the request body are touched
    (``model_fields_set``), so a patch that sends ``first_name`` alone cannot
    wipe ``last_name``. An explicit empty (or whitespace-only) string clears the
    field, which is how the SPA offers "remove my name"; an explicit ``null``
    does the same, and the two are deliberately equivalent so a client using
    either convention gets the behaviour it expects.

    ``default_save_folder_id`` follows the same tri-state rule, with one extra
    check: a non-empty value must name a folder the caller owns, resolved
    through :class:`FileService`. An unknown folder and another account's folder
    are answered with the identical 404 ("Folder not found") so the endpoint
    cannot be used to probe for the existence of someone else's folder.
    """
    updates = payload.model_dump(exclude_unset=True)
    changed: list[str] = []
    for field in ("first_name", "last_name"):
        if field not in updates:
            continue
        setattr(current_user, field, _clean_optional_name(updates[field]))
        changed.append(field)

    if "default_save_folder_id" in updates:
        # ``None`` and blank both mean "clear back to root" (NULL), matching
        # the name fields' convention. Only a non-empty value needs an
        # ownership check, and it is done *before* mutating the row so a
        # rejected folder leaves the stored preference untouched.
        folder_id = _clean_optional_name(updates["default_save_folder_id"])
        if folder_id is not None:
            try:
                await file_service.get_folder(current_user.id, folder_id)
            except FolderNotFoundError as exc:
                # Same 404 for "no such folder" and "not yours" — never reveal
                # which, or the endpoint becomes an ownership oracle.
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Folder not found",
                ) from exc
        current_user.default_save_folder_id = folder_id
        changed.append("default_save_folder_id")

    if changed:
        await db.commit()
        log_data_access(
            user_id=str(current_user.id),
            action="update",
            resource="user_profile",
            fields=",".join(changed),
        )

    return to_user_response(current_user)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """Change the account password, re-authenticating with the current one.

    Requiring the current password is what stops a stolen access token (or a
    borrowed unlocked laptop) from turning into permanent account takeover.

    **This does not revoke other sessions**, and the API does not claim it does.
    This deployment keeps no server-side token store — access tokens are
    stateless JWTs — so a device that is already signed in stays signed in until
    its token expires (30 minutes) and then has to sign in with the new
    password. Implementing genuine revocation would need a token denylist (or
    short-lived tokens plus per-user token versioning), which is a real feature
    and not something this endpoint can honestly promise.

    Argon2 is CPU-heavy, so hashing and verification run off the event loop.
    """
    if not await asyncio.to_thread(
        verify_password, payload.current_password, current_user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_PASSWORD", "Current password is incorrect."),
        )

    current_user.hashed_password = await asyncio.to_thread(
        hash_password, payload.new_password
    )
    await db.commit()
    log_data_access(
        user_id=str(current_user.id), action="update", resource="user_password"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _render_avatar(data: bytes) -> bytes:
    """Downscale ``data`` into a square 256px WebP.

    Runs off the event loop (see the caller): decoding and resizing are CPU
    bound, and a 2 MB source can take a few hundred milliseconds.

    Re-encoding rather than storing what was uploaded is the point: it strips
    EXIF (including GPS coordinates a user did not mean to publish), normalises
    the format to one browser-safe type, and guarantees the stored blob is
    small enough to keep on the row.

    Raises ``UnidentifiedImageError``/``OSError`` for anything Pillow cannot
    decode and ``Image.DecompressionBombError`` for a pixel bomb; the caller
    maps all of them to a 400.
    """
    with Image.open(io.BytesIO(data)) as opened:
        # Honour the EXIF orientation tag first, or a portrait photo from a
        # phone is cropped sideways. `exif_transpose` returns a new image.
        image = ImageOps.exif_transpose(opened)
        # Drop alpha (WebP supports it, but a transparent PNG avatar behind a
        # dark UI reads as a broken image) and guarantee a known colour mode.
        image = image.convert("RGB")

        # Centre-crop to a square so the resize cannot distort the aspect
        # ratio — the client-side preview uses the same rule.
        width, height = image.size
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        image = image.crop((left, top, left + side, top + side))

        image = image.resize((AVATAR_SIZE_PX, AVATAR_SIZE_PX), Image.LANCZOS)

        buffer = io.BytesIO()
        image.save(
            buffer,
            format="WEBP",
            quality=AVATAR_WEBP_QUALITY,
            method=AVATAR_WEBP_METHOD,
        )
        return buffer.getvalue()


@router.post("/me/avatar", response_model=UserResponse)
async def upload_avatar(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    file: Annotated[UploadFile, File()],
) -> UserResponse:
    """Replace the account's avatar with a re-encoded 256px WebP.

    The upload is read under a hard cap (never an unbounded body), then
    validated three ways: a declared-extension allowlist, a magic-byte check
    against that extension (so a PDF named ``.png`` is refused before Pillow
    sees it), and finally a real decode. The declared content type is never
    trusted — it is attacker-controlled and trivially wrong.
    """
    if file is None or not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("AVATAR_INVALID", "Attach an image file to upload."),
        )

    # Read one byte past the cap so "exactly at the limit" is still accepted and
    # "over the limit" is detectable without buffering the rest.
    data = await file.read(MAX_AVATAR_UPLOAD_BYTES + 1)
    if len(data) > MAX_AVATAR_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=_error(
                "AVATAR_TOO_LARGE",
                "That image is larger than 2 MB. Choose a smaller file.",
            ),
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("AVATAR_INVALID", "The uploaded file is empty."),
        )

    # `validate_upload_signature` takes an *extension*, not a filename, so the
    # extension is derived here. Passing the raw filename would compare
    # "avatar.png" against the detected "png" and reject every valid upload.
    extension = extension_from_filename(file.filename)
    if extension not in _AVATAR_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error(
                "AVATAR_INVALID",
                "Avatars must be a PNG, JPEG or WebP image.",
            ),
        )
    if not validate_upload_signature(data, extension):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error(
                "AVATAR_INVALID",
                "That file's contents do not match its extension. Upload a real image.",
            ),
        )

    try:
        rendered = await asyncio.to_thread(_render_avatar, data)
    except Image.DecompressionBombError:
        # Pillow refuses images past ~2x MAX_IMAGE_PIXELS. Reported as a 400
        # (bad input), not a 500, and with a message that does not invite
        # retrying the same file.
        logger.warning("Rejected a decompression-bomb avatar for user %s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("AVATAR_INVALID", "That image is too large to process."),
        ) from None
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        # Not an image, truncated, or in a format Pillow cannot decode. Logged
        # with the reason for support; the client gets a generic message (an
        # exception string is internal detail).
        logger.warning("Rejected an undecodable avatar for user %s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("AVATAR_INVALID", "That file could not be read as an image."),
        ) from None

    current_user.avatar_data = rendered
    current_user.avatar_content_type = "image/webp"
    current_user.avatar_updated_at = datetime.now(UTC)
    await db.commit()
    log_data_access(
        user_id=str(current_user.id), action="update", resource="user_avatar"
    )
    return to_user_response(current_user)


@router.delete("/me/avatar", response_model=UserResponse)
async def delete_avatar(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    """Remove the avatar. Idempotent: deleting one that is absent is a no-op."""
    current_user.avatar_data = None
    current_user.avatar_content_type = None
    current_user.avatar_updated_at = None
    await db.commit()
    log_data_access(
        user_id=str(current_user.id), action="delete", resource="user_avatar"
    )
    return to_user_response(current_user)


# ---------------------------------------------------------------------------
# Phone verification
# ---------------------------------------------------------------------------


def _phone_status(
    user: UserModel,
    settings: Settings,
    *,
    sent: bool = False,
    now: datetime | None = None,
) -> PhoneVerificationStatusResponse:
    """Build the single status shape every phone endpoint answers with.

    ``resend_available_in_seconds`` is always populated so the SPA can run its
    own countdown. That is what lets a *suppressed* resend still look like a
    throttled one rather than a broken button — the response is otherwise
    identical to a successful send, deliberately, so the endpoint cannot be used
    to probe.
    """
    reference = now or datetime.now(UTC)
    expires_in: int | None = None
    if sent:
        expires_in = settings.PHONE_VERIFICATION_TTL_MINUTES * 60
    elif user.phone_verification_expires_at is not None and not code_is_expired(
        user.phone_verification_expires_at, now=reference
    ):
        expires_in = max(
            0,
            int(
                (
                    user.phone_verification_expires_at.replace(tzinfo=UTC)
                    if user.phone_verification_expires_at.tzinfo is None
                    else user.phone_verification_expires_at
                ).timestamp()
                - reference.timestamp()
            ),
        )

    return PhoneVerificationStatusResponse(
        phone_number=user.phone_number,
        phone_verified=user.phone_verified,
        expires_in_seconds=expires_in,
        resend_available_in_seconds=seconds_until_resend_allowed(
            user.phone_verification_sent_at,
            now=reference,
            cooldown_seconds=settings.PHONE_VERIFICATION_RESEND_COOLDOWN_SECONDS,
        ),
    )


async def _send_phone_code(
    *,
    user: UserModel,
    db: AsyncSession,
    settings: Settings,
    sms_sender: SmsPort,
    phone_number: str,
) -> None:
    """Mint, persist and send a code for ``phone_number``.

    The hash is committed *before* the send so a crash between the two cannot
    leave the user holding a code the database has never heard of — the
    recoverable failure is "no SMS arrived", not "the code you just received is
    dead".

    ``phone_verification_sent_at`` is written only after a *successful* send.
    That is what makes the cooldown throttle reality rather than intent: a
    delivery failure must not block the immediate retry the 503 tells the user
    to attempt.

    Raises whatever the transport raises; the caller maps it to a 503.
    """
    issued = issue_phone_code(
        secret=settings.SECRET_KEY.get_secret_value(),
        user_id=user.id,
        ttl_minutes=settings.PHONE_VERIFICATION_TTL_MINUTES,
    )
    user.phone_number = phone_number
    user.phone_verified = False
    user.phone_verified_at = None
    user.phone_verification_code_hash = issued.code_hash
    user.phone_verification_expires_at = issued.expires_at
    # A new code restarts the guess budget; otherwise a fresh code would inherit
    # the spent attempts of the one it replaced.
    user.phone_verification_attempts = 0
    await db.commit()

    message = build_phone_verification_sms(
        to=phone_number,
        code=issued.code,
        ttl_minutes=settings.PHONE_VERIFICATION_TTL_MINUTES,
    )
    await sms_sender.send(message)

    user.phone_verification_sent_at = issued.sent_at
    await db.commit()


@router.post(
    "/me/phone",
    response_model=PhoneVerificationStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_phone_verification(
    payload: RequestPhoneVerificationRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    sms_sender: Annotated[SmsPort, Depends(get_sms_sender)],
) -> PhoneVerificationStatusResponse:
    """Send a verification code to a phone number (or resend to the same one).

    Returns 202 rather than 200 because delivery is asynchronous: the code has
    been *accepted* for sending, not confirmed received.

    Unlike registration email — where a send failure must not 500 after the
    account is already committed — a failure here returns **503**. The user is
    actively waiting for an SMS they will never get; a silent 202 would be a lie
    that leaves them staring at a code box forever. The minted code is kept, so
    an immediate retry (or the resend endpoint) can try again.
    """
    settings = get_settings()
    now = datetime.now(UTC)

    try:
        phone_number = normalize_phone_number(payload.phone_number)
    except InvalidPhoneNumber as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_PHONE_NUMBER", str(exc)),
        ) from exc

    # Checked before writing so the user gets a clear message instead of a
    # unique-index IntegrityError surfacing as a 500. A number held by another
    # account is a conflict whether or not that account has verified it — the
    # unique index cannot distinguish, and silently taking the number over would
    # mean two accounts pointing at one phone.
    conflict = await db.execute(
        select(UserModel).where(
            UserModel.phone_number == phone_number,
            UserModel.id != current_user.id,
        )
    )
    other = conflict.scalars().first()
    if other is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error(
                "PHONE_IN_USE",
                "That phone number is already linked to another account."
                if not other.phone_verified
                else "That phone number is already verified on another account.",
            ),
        )

    # Silent suppression, exactly like the email resend: the answer is 202 and
    # nothing is sent. Anything else would make this endpoint a probe and an SMS
    # bomb (each message costs real money).
    if not phone_cooldown_elapsed(
        current_user.phone_verification_sent_at,
        now=now,
        cooldown_seconds=settings.PHONE_VERIFICATION_RESEND_COOLDOWN_SECONDS,
    ):
        logger.info(
            "Phone verification send suppressed by cooldown for user %s",
            current_user.id,
        )
        return _phone_status(current_user, settings, now=now)

    try:
        await _send_phone_code(
            user=current_user,
            db=db,
            settings=settings,
            sms_sender=sms_sender,
            phone_number=phone_number,
        )
    except IntegrityError:
        # Lost a race with a concurrent request for the same number.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error(
                "PHONE_IN_USE",
                "That phone number is already linked to another account.",
            ),
        ) from None
    except Exception as exc:  # noqa: BLE001 - delivery failure is a 503, not a 500
        logger.error(
            "Failed to send the phone-verification SMS to user %s: %s",
            current_user.id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_error(
                "SMS_DELIVERY_FAILED",
                "We could not send the verification code right now. Try again "
                "in a moment.",
            ),
        ) from None

    log_data_access(
        user_id=str(current_user.id), action="create", resource="phone_verification"
    )
    return _phone_status(current_user, settings, sent=True)


@router.post(
    "/me/phone/resend",
    response_model=PhoneVerificationStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_phone_verification(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    sms_sender: Annotated[SmsPort, Depends(get_sms_sender)],
) -> PhoneVerificationStatusResponse:
    """Send a fresh code to the number already on file.

    Same cooldown/silent-suppression rule as the request endpoint, and the same
    503 on a delivery failure.
    """
    settings = get_settings()
    now = datetime.now(UTC)

    if not current_user.phone_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error(
                "INVALID_PHONE_NUMBER",
                "Add a phone number before requesting a code.",
            ),
        )

    if current_user.phone_verified:
        # Nothing to verify, so no message is spent. Answered with the same 202
        # shape as a real send so the client sees a consistent status rather
        # than a confusing error for an action that is simply unnecessary.
        return _phone_status(current_user, settings, now=now)

    if not phone_cooldown_elapsed(
        current_user.phone_verification_sent_at,
        now=now,
        cooldown_seconds=settings.PHONE_VERIFICATION_RESEND_COOLDOWN_SECONDS,
    ):
        logger.info(
            "Phone verification resend suppressed by cooldown for user %s",
            current_user.id,
        )
        return _phone_status(current_user, settings, now=now)

    try:
        await _send_phone_code(
            user=current_user,
            db=db,
            settings=settings,
            sms_sender=sms_sender,
            phone_number=current_user.phone_number,
        )
    except Exception as exc:  # noqa: BLE001 - delivery failure is a 503, not a 500
        logger.error(
            "Failed to resend the phone-verification SMS to user %s: %s",
            current_user.id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_error(
                "SMS_DELIVERY_FAILED",
                "We could not send the verification code right now. Try again "
                "in a moment.",
            ),
        ) from None

    log_data_access(
        user_id=str(current_user.id), action="create", resource="phone_verification"
    )
    return _phone_status(current_user, settings, sent=True)


@router.post("/me/phone/verify", response_model=PhoneVerificationStatusResponse)
async def verify_phone(
    payload: VerifyPhoneRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PhoneVerificationStatusResponse:
    """Consume the outstanding code and mark the number verified.

    Ordering matters and is deliberate: expiry before the attempt budget before
    the comparison. A dead code reports expired (actionable: request a new one)
    rather than burning one of the few remaining guesses, and an exhausted code
    reports 429 rather than continuing to be guessable.

    The success path is a single conditional UPDATE … RETURNING, so two
    concurrent submissions of the same code cannot both succeed — the second
    finds no row because the first cleared the hash.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    max_attempts = settings.PHONE_VERIFICATION_MAX_ATTEMPTS

    if current_user.phone_verification_code_hash is None or code_is_expired(
        current_user.phone_verification_expires_at, now=now
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error(
                "PHONE_CODE_EXPIRED",
                "That code has expired. Request a new one.",
            ),
        )

    if attempts_exhausted(current_user.phone_verification_attempts, max_attempts=max_attempts):
        # The code is dead. Telling the user to request a new one is the only
        # useful answer — and 429 (not 400) so the SPA can distinguish "you are
        # out of attempts" from "that digit was wrong".
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_error(
                "PHONE_CODE_ATTEMPTS_EXCEEDED",
                "Too many incorrect codes. Request a new one.",
            ),
        )

    secret = settings.SECRET_KEY.get_secret_value()
    if not code_matches(
        payload.code,
        current_user.phone_verification_code_hash,
        secret=secret,
        user_id=current_user.id,
    ):
        # Counted in SQL rather than in Python so concurrent guesses cannot
        # overwrite each other's increment (a lost update would make the ceiling
        # evade-able by parallel requests).
        await db.execute(
            update(UserModel)
            .where(UserModel.id == current_user.id)
            .values(
                phone_verification_attempts=UserModel.phone_verification_attempts + 1
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        current_user.phone_verification_attempts += 1
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("PHONE_CODE_INVALID", _PHONE_CODE_INVALID_MESSAGE),
        )

    expected_hash = hash_verification_code(
        payload.code, secret=secret, user_id=current_user.id
    )
    consumed = await db.execute(
        update(UserModel)
        .where(
            UserModel.id == current_user.id,
            UserModel.phone_verification_code_hash == expected_hash,
            # `>` against a nullable column excludes NULL, so a code with no
            # recorded expiry can never be accepted (fail closed).
            UserModel.phone_verification_expires_at > now,
        )
        .values(
            phone_verified=True,
            phone_verified_at=now,
            # Clearing the hash makes the code single-use: a replay matches no
            # row, and a concurrent second submission finds nothing to update.
            phone_verification_code_hash=None,
            phone_verification_expires_at=None,
            phone_verification_attempts=0,
        )
        .returning(UserModel.id)
        .execution_options(synchronize_session=False)
    )

    # A RETURNING result on aiosqlite has no usable `rowcount` (this repo's
    # established convention), so the row sentinel is what is checked here.
    if consumed.first() is None:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error(
                "PHONE_CODE_EXPIRED",
                "That code has expired. Request a new one.",
            ),
        )

    await db.commit()
    current_user.phone_verified = True
    current_user.phone_verified_at = now
    current_user.phone_verification_code_hash = None
    current_user.phone_verification_expires_at = None
    current_user.phone_verification_attempts = 0
    log_data_access(
        user_id=str(current_user.id), action="update", resource="phone_verification"
    )
    return PhoneVerificationStatusResponse(
        phone_number=current_user.phone_number,
        phone_verified=True,
        expires_in_seconds=None,
        resend_available_in_seconds=None,
    )


@router.delete("/me/phone", status_code=status.HTTP_204_NO_CONTENT)
async def delete_phone_number(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """Unlink the phone number and clear everything about its verification.

    Every verification column goes, not just the number: leaving a live hash
    behind would allow a code sent to a *previous* number to verify the account
    after the user believed they had removed it.
    """
    current_user.phone_number = None
    current_user.phone_verified = False
    current_user.phone_verified_at = None
    current_user.phone_verification_code_hash = None
    current_user.phone_verification_sent_at = None
    current_user.phone_verification_expires_at = None
    current_user.phone_verification_attempts = 0
    await db.commit()
    log_data_access(
        user_id=str(current_user.id), action="delete", resource="phone_number"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------------


def _owned_row_deletes(user_id: int) -> list:
    """The scoped ``DELETE`` statements that remove everything a user owns.

    Explicit ``delete()`` statements rather than loading rows and deleting them
    one at a time: an account can own thousands of jobs and files, and this runs
    inside the deletion transaction where a per-row loop would hold the
    connection for an unbounded time.

    Order is children-before-parents. ``user_files`` references
    ``user_folders`` and is removed first so the FK's ``SET NULL`` never has to
    run (and so a backend with deferred constraints has nothing to order).
    ``monthly_credits`` is keyed by a *string* ``owner_id`` (``str(user_id)``),
    which is the one table that is not a plain ``user_id`` column.
    """
    return [
        delete(ConversionJobModel).where(ConversionJobModel.user_id == user_id),
        delete(UserFileModel).where(UserFileModel.user_id == user_id),
        delete(UserFolderModel).where(UserFolderModel.user_id == user_id),
        delete(APIKeyModel).where(APIKeyModel.user_id == user_id),
        delete(CreditTransactionModel).where(CreditTransactionModel.user_id == user_id),
        delete(MonthlyCreditModel).where(MonthlyCreditModel.owner_id == str(user_id)),
        delete(UserSubscriptionModel).where(UserSubscriptionModel.user_id == user_id),
    ]


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    payload: DeleteAccountRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """Permanently delete the account and everything it owns.

    Two confirmations are required, for two different threats: the password
    proves the caller is the account holder rather than someone holding a live
    session, and ``confirm == "DELETE"`` proves the click was deliberate rather
    than a mis-aimed button. Either one alone leaves a plausible accidental or
    hostile path to destroying an account.

    The database work is a single transaction, so a failure part-way cannot
    leave a half-deleted account (a user row with no jobs, or jobs with no
    user). The avatar bytes and phone columns disappear with the row.

    **Uploaded objects in bucket storage are not deleted here.** They are owned
    by the cleanup worker, which reclaims them on its normal retention sweep —
    a synchronous purge would have to enumerate and delete an unbounded number
    of objects while the user waits, and a failure halfway through would leave
    references already gone. So the account is gone immediately and the blobs
    follow shortly after, on the worker's schedule. Saying so plainly matters:
    this is a data-retention detail, not an implementation detail.
    """
    if payload.confirm != DELETE_ACCOUNT_CONFIRMATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error(
                "INVALID_CONFIRMATION",
                f'Type "{DELETE_ACCOUNT_CONFIRMATION}" to confirm deleting your account.',
            ),
        )

    if not await asyncio.to_thread(
        verify_password, payload.password, current_user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_PASSWORD", "Password is incorrect."),
        )

    user_id = current_user.id
    for statement in _owned_row_deletes(user_id):
        await db.execute(statement)
    await db.execute(delete(UserModel).where(UserModel.id == user_id))
    # One commit for the whole cascade: either the account and its data are all
    # gone, or nothing changed.
    await db.commit()

    log_data_access(user_id=str(user_id), action="delete", resource="user_account")
    return Response(status_code=status.HTTP_204_NO_CONTENT)



