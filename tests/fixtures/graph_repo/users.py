"""Users module — fixture for graph tests."""


class UserStore:
    def __init__(self):
        self._users = {}

    def get(self, user_id: str):
        return self._users.get(user_id)

    def save(self, user) -> None:
        self._users[user.id] = user


def get_user(user_id: str):
    return _store.get(user_id)


def create_user(user_id: str):
    user = _User(user_id)
    _store.save(user)
    return user


class _User:
    def __init__(self, id):
        self.id = id


_store = UserStore()
