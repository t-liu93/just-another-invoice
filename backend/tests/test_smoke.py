"""Trivial smoke test – verifies the test infrastructure works."""


def test_import_jai() -> None:
    """The ``jai`` package should be importable without error."""
    import jai  # noqa: F401

    assert jai.__file__ is not None
