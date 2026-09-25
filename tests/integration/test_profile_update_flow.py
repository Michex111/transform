"""End-to-end tests for the profile endpoints: names, password, avatar, deletion.

Runs the real routers, real JWT auth and real SQL repositories against an
in-memory SQLite database, with only the database session and the email
transport replaced. Avatars are real images generated with Pillow, and the
stored result is decoded back out of the response to prove it was actually
re-encoded rather than echoed.
"""

import asyncio
import base64
import io
from contextlib import contextmanager
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.security.enitities.api_key import APIKeyStatus
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.config.settings import get_settings
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
from src.infrastructure.database.session import Base, get_db_session
from src.presentation.api.dependencies.service_dependencies import get_email_sender
from src.presentation.api.middleware.rate_limit import RateLimitMiddleware
from tests.fakes.fake_email_sender import FakeEmailSender

REGISTER = "/api/users/register"
LOGIN = "/api/users/token"
ME = "/api/users/me"
AVATAR = "/api/users/me/avatar"
PASSWORD_ENDPOINT = "/api/users/me/password"

PASSWORD = "Sup3rSecret!"
NEW_PASSWORD = "Ev3nBetterSecret!"

MAX_AVATAR_BYTES = 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@contextmanager
def profile_client(db_path: str) -> Generator[TestClient, None, None]:
    async def no_op_initialize_database() -> None:
        return None

    async def prepare_schema() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(prepare_schema())

    async def override_db():
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                yield session
        finally:
            await engine.dispose()

    original_init = api_main.initialize_database
    original_is_allowed = RateLimitMiddleware._is_allowed

    async def allow_all(self, key, limit, window=60):
        del self, key, limit, window
        return True

    api_main.initialize_database = no_op_initialize_database
    RateLimitMiddleware._is_allowed = allow_all
    api_main.app.dependency_overrides[get_db_session] = override_db
    api_main.app.dependency_overrides[get_email_sender] = lambda: FakeEmailSender()

    client = TestClient(api_main.app)
    try:
        yield client
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init
        RateLimitMiddleware._is_allowed = original_is_allowed


@pytest.fixture
def no_email_gate(monkeypatch):
    """Keep the email gate open: this feature is unrelated to it, and the local
    ``.env`` can point ``auto`` at a real transport, which would enforce it."""
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def register_and_sign_in(
    client: TestClient,
    *,
    username: str = "ada",
    email: str = "ada@example.com",
    password: str = PASSWORD,
    first_name: str | None = None,
    last_name: str | None = None,
) -> tuple[int, dict[str, str]]:
    body: dict[str, object] = {"username": username, "email": email, "password": password}
    if first_name is not None:
        body["first_name"] = first_name
    if last_name is not None:
        body["last_name"] = last_name
    response = client.post(REGISTER, json=body)
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]
    login = client.post(LOGIN, data={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return user_id, {"Authorization": f"Bearer {login.json()['access_token']}"}


def read_user_row(db_path: str, user_id: int) -> UserModel:
    async def _run() -> UserModel:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                return (
                    await session.execute(select(UserModel).where(UserModel.id == user_id))
                ).scalars().one()
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _png(width: int = 8, height: int = 4, colour=(200, 30, 40)) -> bytes:
    """A real PNG, generated rather than embedded (no binary fixture needed)."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def decode_data_url(url: str) -> bytes:
    assert url.startswith("data:image/webp;base64,"), url[:40]
    return base64.b64decode(url.split(",", 1)[1])


def seed_owned_rows(db_path: str, user_id: int, other_user_id: int) -> None:
    """Give both users one of everything, so scoping can be proven.

    The other user's rows are what makes the assertion meaningful: a delete that
    dropped every row in the database would otherwise pass.
    """

    async def _run() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            for owner, tag in ((user_id, "mine"), (other_user_id, "theirs")):
                session.add(
                    ConversionJobModel(
                        job_id=f"job-{tag}",
                        status=JobStatus.COMPLETED,
                        source_format="pdf",
                        target_format="docx",
                        input_file="a.pdf",
                        user_id=owner,
                    )
                )
                session.add(
                    UserFileModel(
                        id=f"file-{tag}",
                        user_id=owner,
                        file_key=f"files/{tag}.pdf",
                        file_name="a.pdf",
                        file_extension="pdf",
                        file_size_bytes=10,
                    )
                )
                session.add(
                    UserFolderModel(id=f"folder-{tag}", user_id=owner, name="Docs")
                )
                session.add(
                    APIKeyModel(
                        id=f"key-{tag}",
                        key=f"tr_{tag}",
                        user_id=owner,
                        name="default",
                        status=APIKeyStatus.ACTIVE,
                    )
                )
                session.add(
                    CreditTransactionModel(
                        id=f"txn-{tag}",
                        user_id=owner,
                        amount=10,
                        transaction_type="GRANT",
                    )
                )
                session.add(
                    MonthlyCreditModel(
                        owner_id=str(owner),
                        period_key="2026-09",
                        allowance=100,
                        remaining=90,
                    )
                )
                session.add(
                    UserSubscriptionModel(
                        actor_key=f"user:{owner}",
                        user_id=owner,
                        tier=SubscriptionTier.FREE,
                    )
                )
            await session.commit()
        await engine.dispose()

    asyncio.run(_run())


def count_owned_rows(db_path: str, user_id: int) -> dict[str, int]:
    async def _run() -> dict[str, int]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                async def count(model, *clauses) -> int:
                    stmt = select(func.count()).select_from(model).where(*clauses)
                    return int((await session.execute(stmt)).scalar_one())

                return {
                    "jobs": await count(
                        ConversionJobModel, ConversionJobModel.user_id == user_id
                    ),
                    "files": await count(
                        UserFileModel, UserFileModel.user_id == user_id
                    ),
                    "folders": await count(
                        UserFolderModel, UserFolderModel.user_id == user_id
                    ),
                    "api_keys": await count(
                        APIKeyModel, APIKeyModel.user_id == user_id
                    ),
                    "credit_transactions": await count(
                        CreditTransactionModel, CreditTransactionModel.user_id == user_id
                    ),
                    "monthly_credits": await count(
                        MonthlyCreditModel, MonthlyCreditModel.owner_id == str(user_id)
                    ),
                    "subscriptions": await count(
                        UserSubscriptionModel, UserSubscriptionModel.user_id == user_id
                    ),
                    "users": await count(UserModel, UserModel.id == user_id),
                }
        finally:
            await engine.dispose()

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Display name derivation
# ---------------------------------------------------------------------------


def test_registration_with_names_derives_the_display_name(
    tmp_path, no_email_gate
) -> None:
    with profile_client(str(tmp_path / "a.db")) as client:
        response = client.post(
            REGISTER,
            json={
                "username": "ada",
                "email": "ada@example.com",
                "password": PASSWORD,
                "first_name": " Ada ",
                "last_name": "Lovelace",
            },
        )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["first_name"] == "Ada"  # trimmed on the way in
    assert payload["last_name"] == "Lovelace"
    assert payload["display_name"] == "Ada Lovelace"
    assert payload["initials"] == "AL"


def test_registration_without_names_falls_back_to_the_username(
    tmp_path, no_email_gate
) -> None:
    with profile_client(str(tmp_path / "b.db")) as client:
        response = client.post(
            REGISTER,
            json={"username": "ada", "email": "ada@example.com", "password": PASSWORD},
        )

    assert response.status_code == 201, response.text
    assert response.json()["display_name"] == "ada"
    assert response.json()["initials"] == "AD"


def test_blank_registration_names_are_stored_as_absent(tmp_path, no_email_gate) -> None:
    db_path = str(tmp_path / "c.db")
    with profile_client(db_path) as client:
        response = client.post(
            REGISTER,
            json={
                "username": "ada",
                "email": "ada@example.com",
                "password": PASSWORD,
                "first_name": "   ",
                "last_name": "",
            },
        )
        stored = read_user_row(db_path, response.json()["id"])

    assert response.status_code == 201, response.text
    assert stored.first_name is None
    assert stored.last_name is None
    assert response.json()["display_name"] == "ada"


# ---------------------------------------------------------------------------
# PATCH /me
# ---------------------------------------------------------------------------


def test_patching_names_updates_the_derived_fields(tmp_path, no_email_gate) -> None:
    db_path = str(tmp_path / "d.db")
    with profile_client(db_path) as client:
        _, headers = register_and_sign_in(client)
        response = client.patch(ME, json={"first_name": "Ada", "last_name": "Lovelace"}, headers=headers)
        stored = read_user_row(db_path, 1)

    assert response.status_code == 200, response.text
    assert response.json()["first_name"] == "Ada"
    assert response.json()["display_name"] == "Ada Lovelace"
    assert response.json()["initials"] == "AL"
    assert stored.first_name == "Ada"
    assert stored.last_name == "Lovelace"


def test_an_empty_string_clears_a_name(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "e.db")) as client:
        _, headers = register_and_sign_in(client, first_name="Ada", last_name="Lovelace")
        response = client.patch(ME, json={"last_name": ""}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["last_name"] is None
    assert response.json()["display_name"] == "Ada"
    assert response.json()["initials"] == "A"


def test_a_patch_that_sends_one_name_leaves_the_other_alone(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "f.db")) as client:
        _, headers = register_and_sign_in(client, first_name="Ada", last_name="Lovelace")
        response = client.patch(ME, json={"first_name": "Grace"}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["first_name"] == "Grace"
    assert response.json()["last_name"] == "Lovelace"


def test_names_are_trimmed_before_storage(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "g.db")) as client:
        _, headers = register_and_sign_in(client)
        response = client.patch(ME, json={"first_name": "  Ada  "}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["first_name"] == "Ada"


def test_an_over_long_name_is_rejected(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "h.db")) as client:
        _, headers = register_and_sign_in(client)
        response = client.patch(ME, json={"first_name": "A" * 51}, headers=headers)

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Default save folder
# ---------------------------------------------------------------------------

FOLDERS = "/api/v1/files/folders"


def create_folder(client: TestClient, headers: dict[str, str], name: str) -> str:
    """Create a real folder for the caller and return its id."""
    response = client.post(FOLDERS, json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_default_save_folder_starts_as_null(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "j.db")) as client:
        _, headers = register_and_sign_in(client)
        response = client.get(ME, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["default_save_folder_id"] is None


def test_setting_the_default_save_folder_is_persisted(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "k.db")) as client:
        _, headers = register_and_sign_in(client)
        folder_id = create_folder(client, headers, "Projects")

        patched = client.patch(
            ME, json={"default_save_folder_id": folder_id}, headers=headers
        )
        fetched = client.get(ME, headers=headers)

    assert patched.status_code == 200, patched.text
    assert patched.json()["default_save_folder_id"] == folder_id
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["default_save_folder_id"] == folder_id


def test_patching_a_name_leaves_the_default_save_folder_alone(
    tmp_path, no_email_gate
) -> None:
    db_path = str(tmp_path / "l.db")
    with profile_client(db_path) as client:
        _, headers = register_and_sign_in(client)
        folder_id = create_folder(client, headers, "Projects")
        client.patch(ME, json={"default_save_folder_id": folder_id}, headers=headers)

        response = client.patch(ME, json={"first_name": "Ada"}, headers=headers)
        stored = read_user_row(db_path, 1)

    assert response.status_code == 200, response.text
    assert response.json()["default_save_folder_id"] == folder_id
    assert stored.default_save_folder_id == folder_id


def test_an_explicit_null_clears_the_default_save_folder(tmp_path, no_email_gate) -> None:
    db_path = str(tmp_path / "m.db")
    with profile_client(db_path) as client:
        _, headers = register_and_sign_in(client)
        folder_id = create_folder(client, headers, "Projects")
        client.patch(ME, json={"default_save_folder_id": folder_id}, headers=headers)

        response = client.patch(
            ME, json={"default_save_folder_id": None}, headers=headers
        )
        stored = read_user_row(db_path, 1)

    assert response.status_code == 200, response.text
    assert response.json()["default_save_folder_id"] is None
    assert stored.default_save_folder_id is None


def test_a_blank_default_save_folder_is_cleared_back_to_root(
    tmp_path, no_email_gate
) -> None:
    db_path = str(tmp_path / "n.db")
    with profile_client(db_path) as client:
        _, headers = register_and_sign_in(client)
        folder_id = create_folder(client, headers, "Projects")
        client.patch(ME, json={"default_save_folder_id": folder_id}, headers=headers)

        response = client.patch(
            ME, json={"default_save_folder_id": "   "}, headers=headers
        )
        stored = read_user_row(db_path, 1)

    assert response.status_code == 200, response.text
    assert response.json()["default_save_folder_id"] is None
    assert stored.default_save_folder_id is None


def test_an_unknown_folder_is_a_404_and_stores_nothing(tmp_path, no_email_gate) -> None:
    db_path = str(tmp_path / "o.db")
    with profile_client(db_path) as client:
        _, headers = register_and_sign_in(client)
        response = client.patch(
            ME, json={"default_save_folder_id": "does-not-exist"}, headers=headers
        )
        stored = read_user_row(db_path, 1)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Folder not found"
    assert stored.default_save_folder_id is None


def test_a_foreign_folder_is_a_404_and_leaves_the_stored_value_alone(
    tmp_path, no_email_gate
) -> None:
    """The foreign folder must be indistinguishable from a missing one.

    Both users and both folders are created here rather than hardcoded, so the
    test cannot pass because of a fixture id that happens to line up.
    """
    db_path = str(tmp_path / "p.db")
    with profile_client(db_path) as client:
        _, headers = register_and_sign_in(client, username="ada", email="ada@example.com")
        mine = create_folder(client, headers, "Projects")
        client.patch(ME, json={"default_save_folder_id": mine}, headers=headers)

        _, other_headers = register_and_sign_in(
            client, username="grace", email="grace@example.com"
        )
        theirs = create_folder(client, other_headers, "Their stuff")

        response = client.patch(
            ME, json={"default_save_folder_id": theirs}, headers=headers
        )
        stored = read_user_row(db_path, 1)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Folder not found"
    assert stored.default_save_folder_id == mine


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------


def test_changing_the_password_swaps_which_credential_works(
    tmp_path, no_email_gate
) -> None:
    with profile_client(str(tmp_path / "i.db")) as client:
        _, headers = register_and_sign_in(client)
        response = client.post(
            PASSWORD_ENDPOINT,
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            headers=headers,
        )
        old = client.post(LOGIN, data={"username": "ada", "password": PASSWORD})
        new = client.post(LOGIN, data={"username": "ada", "password": NEW_PASSWORD})

    assert response.status_code == 204, response.text
    assert old.status_code == 401, old.text
    assert new.status_code == 200, new.text
    assert new.json()["access_token"]


def test_changing_the_password_requires_the_current_one(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "j.db")) as client:
        _, headers = register_and_sign_in(client)
        response = client.post(
            PASSWORD_ENDPOINT,
            json={"current_password": "NotMyPassword1!", "new_password": NEW_PASSWORD},
            headers=headers,
        )
        # The password must be unchanged.
        still_works = client.post(LOGIN, data={"username": "ada", "password": PASSWORD})

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_PASSWORD"
    assert still_works.status_code == 200


def test_a_short_new_password_is_rejected(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "k.db")) as client:
        _, headers = register_and_sign_in(client)
        response = client.post(
            PASSWORD_ENDPOINT,
            json={"current_password": PASSWORD, "new_password": "short"},
            headers=headers,
        )

    assert response.status_code == 422


def test_changing_the_password_requires_authentication(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "l.db")) as client:
        register_and_sign_in(client)
        response = client.post(
            PASSWORD_ENDPOINT,
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Avatar
# ---------------------------------------------------------------------------


def test_uploading_an_avatar_stores_a_downscaled_webp(tmp_path, no_email_gate) -> None:
    db_path = str(tmp_path / "m.db")
    with profile_client(db_path) as client:
        _, headers = register_and_sign_in(client)
        response = client.post(
            AVATAR,
            files={"file": ("avatar.png", _png(800, 400), "image/png")},
            headers=headers,
        )
        stored = read_user_row(db_path, 1)

    assert response.status_code == 200, response.text
    url = response.json()["avatar_url"]
    assert url is not None and url.startswith("data:image/webp;base64,")

    # The served bytes really are a 256px square WebP, not the upload echoed back.
    with Image.open(io.BytesIO(decode_data_url(url))) as rendered:
        assert rendered.format == "WEBP"
        assert rendered.size == (256, 256)

    assert stored.avatar_content_type == "image/webp"
    assert stored.avatar_updated_at is not None
    # A non-square source is centre-cropped, so the result is smaller than the
    # original PNG would have been at full size.
    assert len(decoded := decode_data_url(url)) > 0
    assert decoded != _png(800, 400)


def test_an_avatar_upload_survives_a_reload_of_the_profile(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "n.db")) as client:
        _, headers = register_and_sign_in(client)
        uploaded = client.post(
            AVATAR,
            files={"file": ("avatar.png", _png(), "image/png")},
            headers=headers,
        )
        reloaded = client.get(ME, headers=headers)

    assert uploaded.status_code == 200, uploaded.text
    assert reloaded.json()["avatar_url"] == uploaded.json()["avatar_url"]
    assert reloaded.json()["avatar_url"] is not None


def test_an_avatar_over_two_megabytes_is_rejected_with_413(
    tmp_path, no_email_gate
) -> None:
    with profile_client(str(tmp_path / "o.db")) as client:
        _, headers = register_and_sign_in(client)
        oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_AVATAR_BYTES + 1)
        response = client.post(
            AVATAR,
            files={"file": ("big.png", oversized, "image/png")},
            headers=headers,
        )

    assert response.status_code == 413, response.text
    assert response.json()["detail"]["code"] == "AVATAR_TOO_LARGE"


def test_a_pdf_named_png_is_rejected_by_the_signature_check(
    tmp_path, no_email_gate
) -> None:
    """The declared extension is attacker-controlled; the magic bytes are not."""
    with profile_client(str(tmp_path / "p.db")) as client:
        _, headers = register_and_sign_in(client)
        response = client.post(
            AVATAR,
            files={"file": ("notreally.png", b"%PDF-1.4\n% fake pdf body", "image/png")},
            headers=headers,
        )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "AVATAR_INVALID"


def test_a_undecodable_file_named_png_is_rejected(tmp_path, no_email_gate) -> None:
    """Text has no signature, so the lenient magic check passes it — Pillow is
    the real gate and must not let it through as a 500."""
    with profile_client(str(tmp_path / "q.db")) as client:
        _, headers = register_and_sign_in(client)
        response = client.post(
            AVATAR,
            files={"file": ("text.png", b"this is not an image at all", "image/png")},
            headers=headers,
        )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "AVATAR_INVALID"


def test_an_unsupported_extension_is_rejected(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "r.db")) as client:
        _, headers = register_and_sign_in(client)
        response = client.post(
            AVATAR,
            files={"file": ("avatar.gif", b"GIF89a" + b"\x00" * 32, "image/gif")},
            headers=headers,
        )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "AVATAR_INVALID"


def test_deleting_the_avatar_returns_a_null_url(tmp_path, no_email_gate) -> None:
    db_path = str(tmp_path / "s.db")
    with profile_client(db_path) as client:
        _, headers = register_and_sign_in(client)
        client.post(
            AVATAR,
            files={"file": ("avatar.png", _png(), "image/png")},
            headers=headers,
        )
        response = client.delete(AVATAR, headers=headers)
        stored = read_user_row(db_path, 1)

    assert response.status_code == 200, response.text
    assert response.json()["avatar_url"] is None
    assert stored.avatar_data is None
    assert stored.avatar_content_type is None


def test_avatar_endpoints_require_authentication(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "t.db")) as client:
        response = client.post(
            AVATAR, files={"file": ("avatar.png", _png(), "image/png")}
        )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------------


def test_deleting_the_account_requires_the_confirmation_word(
    tmp_path, no_email_gate
) -> None:
    db_path = str(tmp_path / "u.db")
    with profile_client(db_path) as client:
        user_id, headers = register_and_sign_in(client)
        response = client.request(
            "DELETE", ME, json={"password": PASSWORD, "confirm": "delete"}, headers=headers
        )
        still_there = count_owned_rows(db_path, user_id)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_CONFIRMATION"
    assert still_there["users"] == 1


def test_deleting_the_account_requires_the_password(tmp_path, no_email_gate) -> None:
    db_path = str(tmp_path / "v.db")
    with profile_client(db_path) as client:
        user_id, headers = register_and_sign_in(client)
        response = client.request(
            "DELETE", ME, json={"password": "WrongPassword1!", "confirm": "DELETE"}, headers=headers
        )
        still_there = count_owned_rows(db_path, user_id)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_PASSWORD"
    assert still_there["users"] == 1


def test_deleting_the_account_removes_every_owned_row(tmp_path, no_email_gate) -> None:
    db_path = str(tmp_path / "w.db")
    with profile_client(db_path) as client:
        user_id, headers = register_and_sign_in(client, username="ada", email="ada@example.com")
        other_id, _ = register_and_sign_in(
            client, username="bob", email="bob@example.com"
        )
        seed_owned_rows(db_path, user_id, other_id)

        before = count_owned_rows(db_path, user_id)
        response = client.request(
            "DELETE", ME, json={"password": PASSWORD, "confirm": "DELETE"}, headers=headers
        )
        after = count_owned_rows(db_path, user_id)
        other_after = count_owned_rows(db_path, other_id)
        stale_token = client.get(ME, headers=headers)

    assert all(count > 0 for count in before.values()), before
    assert response.status_code == 204, response.text
    assert all(count == 0 for count in after.values()), after
    # Another account is untouched: the delete is scoped, not a table wipe.
    assert all(count > 0 for count in other_after.values()), other_after
    # The account is really gone: its token no longer resolves to a user.
    assert stale_token.status_code == 401


def test_deleting_the_account_requires_authentication(tmp_path, no_email_gate) -> None:
    with profile_client(str(tmp_path / "x.db")) as client:
        register_and_sign_in(client)
        response = client.request(
            "DELETE", ME, json={"password": PASSWORD, "confirm": "DELETE"}
        )

    assert response.status_code == 401
