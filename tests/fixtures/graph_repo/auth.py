"""Auth module — imports users."""

from users import get_user


def login(user_id: str):
    user = get_user(user_id)
    if user is None:
        return None
    return _issue_token(user)


def _issue_token(user) -> str:
    return f"token-{user.id}"
