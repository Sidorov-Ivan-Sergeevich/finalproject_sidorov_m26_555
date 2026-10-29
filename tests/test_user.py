"""Tests for the User domain model."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from valutatrade_hub.core.models import User


@pytest.fixture
def user() -> User:
    """Create a user for an individual test."""
    return User.create(
        user_id=1,
        username="alice",
        password="1234",
    )


def test_create_user(user: User) -> None:
    """A new user contains the expected identity and registration date."""
    assert user.user_id == 1
    assert user.username == "alice"
    assert isinstance(user.registration_date, datetime)
    assert user.registration_date.tzinfo == UTC


def test_password_hash_matches_required_scheme(user: User) -> None:
    """The stored hash follows the educational password-plus-salt scheme."""
    expected_hash = hashlib.sha256(("1234" + user.salt).encode("utf-8")).hexdigest()

    assert user.hashed_password == expected_hash
    assert user.hashed_password != "1234"


def test_verify_password(user: User) -> None:
    """Only the correct password passes verification."""
    assert user.verify_password("1234")
    assert not user.verify_password("wrong")
    assert not user.verify_password("")


def test_same_password_has_different_salts(user: User) -> None:
    """Different users receive separate randomly generated salts."""
    another_user = User.create(2, "bob", "1234")

    assert user.salt != another_user.salt
    assert user.hashed_password != another_user.hashed_password


def test_public_info_excludes_credentials(user: User) -> None:
    """Public user information contains no password hash or salt."""
    info = user.get_user_info()

    assert info == {
        "user_id": 1,
        "username": "alice",
        "registration_date": user.registration_date.isoformat(),
    }


def test_change_password(user: User) -> None:
    """Changing a password replaces its hash and salt."""
    old_hash = user.hashed_password
    old_salt = user.salt

    user.change_password("new-password")

    assert user.verify_password("new-password")
    assert not user.verify_password("1234")
    assert user.hashed_password != old_hash
    assert user.salt != old_salt


def test_invalid_password_change_preserves_credentials(user: User) -> None:
    """A rejected password change leaves existing credentials intact."""
    old_hash = user.hashed_password
    old_salt = user.salt

    with pytest.raises(ValueError):
        user.change_password("123")

    assert user.hashed_password == old_hash
    assert user.salt == old_salt
    assert user.verify_password("1234")


@pytest.mark.parametrize("password", ["", "1", "123", None, 1234])
def test_create_rejects_invalid_password(password: object) -> None:
    """Registration rejects short passwords and non-string values."""
    with pytest.raises(ValueError):
        User.create(1, "alice", password)


@pytest.mark.parametrize("username", ["", "   ", None, 123])
def test_create_rejects_invalid_username(username: object) -> None:
    """A username must be a nonempty string."""
    with pytest.raises(ValueError):
        User.create(1, username, "1234")


@pytest.mark.parametrize("user_id", [0, -1, True, 1.5, "1"])
def test_create_rejects_invalid_id(user_id: object) -> None:
    """An identifier must be a positive integer, excluding bool."""
    with pytest.raises(ValueError):
        User.create(user_id, "alice", "1234")


def test_username_setter(user: User) -> None:
    """The setter trims whitespace and accepts a valid new name."""
    user.username = " bob "

    assert user.username == "bob"


def test_invalid_username_does_not_replace_existing_name(user: User) -> None:
    """A rejected rename preserves the previous username."""
    with pytest.raises(ValueError):
        user.username = "   "

    assert user.username == "alice"


def test_user_id_is_read_only(user: User) -> None:
    """The public identifier property cannot be reassigned."""
    with pytest.raises(AttributeError):
        user.user_id = 2


def test_json_round_trip(user: User) -> None:
    """JSON serialization preserves identity, date, and credentials."""
    serialized = json.dumps(user.to_dict())
    restored = User.from_dict(json.loads(serialized))

    assert restored.to_dict() == user.to_dict()
    assert restored.registration_date == user.registration_date
    assert restored.verify_password("1234")
