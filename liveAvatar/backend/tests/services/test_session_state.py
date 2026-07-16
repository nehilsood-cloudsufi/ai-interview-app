from app.services.session_state import SessionCounter


def test_starts_at_zero():
    counter = SessionCounter()
    assert counter.count == 0


def test_increment():
    counter = SessionCounter()
    counter.increment()
    counter.increment()
    assert counter.count == 2


def test_decrement():
    counter = SessionCounter()
    counter.increment()
    counter.increment()
    counter.decrement()
    assert counter.count == 1


def test_decrement_floors_at_zero():
    counter = SessionCounter()
    counter.decrement()
    counter.decrement()
    assert counter.count == 0
