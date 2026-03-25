"""Helpers that narrow optional strings for typed containment checks in tests."""


def assert_contains(text: str | None, substring: str) -> None:
    """Assert ``text`` is not None and ``substring`` appears in it."""
    assert text is not None
    assert substring in text
