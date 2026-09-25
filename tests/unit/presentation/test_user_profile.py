"""Unit tests for the derived profile values (display name, initials, avatar URL).

``display_name``/``initials``/``avatar_url`` are computed rather than stored, so
these are pure-function tests over a stub record — no database, no ORM.
"""

from base64 import b64encode
from types import SimpleNamespace

from src.application.services.user_profile import (
    avatar_data_url_for,
    display_name_for,
    initials_for,
)


def _user(**overrides):
    base = {
        "username": "ada",
        "first_name": None,
        "last_name": None,
        "avatar_data": None,
        "avatar_content_type": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# display_name_for
# ---------------------------------------------------------------------------


def test_display_name_joins_both_names() -> None:
    assert display_name_for(_user(first_name="Ada", last_name="Lovelace")) == "Ada Lovelace"


def test_display_name_uses_the_single_name_that_is_set() -> None:
    assert display_name_for(_user(first_name="Ada")) == "Ada"
    assert display_name_for(_user(last_name="Lovelace")) == "Lovelace"


def test_display_name_falls_back_to_the_username() -> None:
    """Every account created before names existed lands here."""
    assert display_name_for(_user()) == "ada"


def test_display_name_treats_whitespace_only_names_as_absent() -> None:
    """A blank name would render as an invisible line instead of the fallback."""
    assert display_name_for(_user(first_name="   ", last_name="\t")) == "ada"
    assert display_name_for(_user(first_name="  ", last_name="Lovelace")) == "Lovelace"


def test_display_name_trims_the_parts_it_joins() -> None:
    assert display_name_for(_user(first_name=" Ada ", last_name=" Lovelace ")) == (
        "Ada Lovelace"
    )


def test_display_name_handles_non_latin_names() -> None:
    assert display_name_for(_user(first_name="Люба", last_name="Иванова")) == "Люба Иванова"
    assert display_name_for(_user(first_name="李", last_name="雷")) == "李 雷"


def test_display_name_is_never_empty() -> None:
    assert display_name_for(_user(username="")) == "Account"


# ---------------------------------------------------------------------------
# initials_for
# ---------------------------------------------------------------------------


def test_initials_use_the_first_letter_of_each_name() -> None:
    assert initials_for(_user(first_name="Ada", last_name="lovelace")) == "AL"


def test_initials_use_the_one_letter_when_only_one_name_is_set() -> None:
    assert initials_for(_user(first_name="Ada")) == "A"
    assert initials_for(_user(last_name="Lovelace")) == "L"


def test_initials_fall_back_to_the_first_two_letters_of_the_username() -> None:
    assert initials_for(_user(username="ada")) == "AD"


def test_initials_treat_whitespace_only_names_as_absent() -> None:
    assert initials_for(_user(first_name=" ", last_name="", username="ada")) == "AD"


def test_initials_are_never_empty() -> None:
    """An empty monogram collapses the avatar tile into a blank circle."""
    assert initials_for(_user(username="")) == "?"


def test_initials_handle_non_latin_names() -> None:
    # "Люба Иванова" -> Л + И. Uppercasing must work outside ASCII.
    assert initials_for(_user(first_name="люба", last_name="иванова")) == "ЛИ"


def test_initials_are_uppercased_and_bounded_to_two_characters() -> None:
    initials = initials_for(
        _user(first_name="Ada", last_name="Lovelace", username="whatever")
    )

    assert initials == "AL"
    assert len(initials) == 2


# ---------------------------------------------------------------------------
# avatar_data_url_for
# ---------------------------------------------------------------------------


def test_avatar_url_is_none_when_no_avatar_is_stored() -> None:
    assert avatar_data_url_for(_user()) is None


def test_avatar_url_is_none_for_empty_bytes() -> None:
    """A zero-length blob is not an image; report "no avatar", not a broken one."""
    assert avatar_data_url_for(_user(avatar_data=b"", avatar_content_type="image/webp")) is None


def test_avatar_url_is_a_base64_data_url() -> None:
    payload = b"RIFF....WEBPVP8 "

    url = avatar_data_url_for(
        _user(avatar_data=payload, avatar_content_type="image/webp")
    )

    assert url is not None
    assert url.startswith("data:image/webp;base64,")
    assert url.split(",", 1)[1] == b64encode(payload).decode("ascii")


def test_avatar_url_defaults_the_content_type_to_webp() -> None:
    """The endpoint always writes WebP, so a missing type must not produce "data:;base64,"."""
    url = avatar_data_url_for(_user(avatar_data=b"x"))

    assert url is not None
    assert url.startswith("data:image/webp;base64,")
