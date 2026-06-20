"""Fixture used by tests/test_chunker.py."""


def top_level_fn(x: int) -> int:
    return x + 1


class Greeter:
    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self) -> str:
        return f"hello, {self.name}"


def _another(y):
    return y * 2
