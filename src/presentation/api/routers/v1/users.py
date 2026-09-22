from typing import Annotated

import asyncio
import logging
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.ports.email_port import EmailPort
from src.application.services.email_templates import build_verification_email
from src.domain.security.enitities.email_verification import (
    cooldown_elapsed,
    hash_verification_token,
    issue_verification_token,
    token_is_expired,
)
from src.infrastructure.auth.jwt_provider import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
    verify_refresh_token,
)
from src.infrastructure.config.settings import Settings, get_settings
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import get_db_session
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import get_email_sender
from src.presentation.schemas.auth import (
    RefreshTokenRequest,
    ResendVerificationRequest,
    TokenResponse,
    UserCreateRequest,
    UserResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
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
    return UserResponse.model_validate(user)


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


@router.post("/token", response_model=TokenResponse)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> TokenResponse:
    result = await db.execute(select(UserModel).where(UserModel.username == form_data.username))
    user = result.scalars().first()

    # Argon2 is CPU-heavy, so run it off the event loop. When the user is
    # missing we still verify against a dummy hash so both branches cost the
    # same (prevents username enumeration via response timing).
    if user is None:
        await asyncio.to_thread(verify_password, form_data.password, _DUMMY_PASSWORD_HASH)
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
    return UserResponse.model_validate(current_user)


