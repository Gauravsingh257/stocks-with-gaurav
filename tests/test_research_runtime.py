from dashboard.backend.services.research_runtime import _should_activate


def test_should_activate_with_tolerance():
    assert _should_activate(100.0, 100.2) is True
    assert _should_activate(100.0, 101.0) is False


def test_should_activate_with_entry_zone():
    assert _should_activate(100.0, 96.0, [95.0, 97.0]) is True
    assert _should_activate(100.0, 98.5, [95.0, 97.0]) is False
