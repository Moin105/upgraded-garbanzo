"""Billing module — also depends on users."""

from users import get_user


def charge(user_id: str, amount: int):
    user = get_user(user_id)
    if user is None:
        raise ValueError("no user")
    return _send_to_processor(user, amount)


def _send_to_processor(user, amount: int):
    return {"user": user.id, "amount": amount}
