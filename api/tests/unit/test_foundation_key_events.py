"""`core/key_events.KeyEvents`: how a destroyed case key reaches every cache that holds that case (PR 15).

Core cannot import a feature, so each cache (the key cache in core, the reader's search cache) subscribes
itself. The only thing passed around is the case id (rule 6).
"""

from __future__ import annotations

import gc
import uuid

from app.core.key_events import KeyEvents, key_events


class Spy:
    def __init__(self) -> None:
        self.cases: list[uuid.UUID] = []
        self.all_calls = 0

    def on_case(self, case_id: uuid.UUID) -> None:
        self.cases.append(case_id)

    def on_all(self) -> None:
        self.all_calls += 1


def test_a_destroyed_case_reaches_every_subscriber() -> None:
    events = KeyEvents()
    a, b = Spy(), Spy()
    events.subscribe(a.on_case, a.on_all)
    events.subscribe(b.on_case, b.on_all)
    case = uuid.uuid4()
    events.destroyed(case)
    assert a.cases == [case] and b.cases == [case]
    assert a.all_calls == 0


def test_forget_all_reaches_every_subscriber_that_asked_for_it() -> None:
    events = KeyEvents()
    a = Spy()
    only_cases = Spy()
    events.subscribe(a.on_case, a.on_all)
    events.subscribe(only_cases.on_case)
    events.forget_all()
    assert a.all_calls == 1 and only_cases.all_calls == 0


def test_a_subscriber_that_raises_does_not_stop_the_others() -> None:
    events = KeyEvents()
    spy = Spy()

    def broken(_case: uuid.UUID) -> None:
        raise RuntimeError("boom")

    events.subscribe(broken)
    events.subscribe(spy.on_case)
    case = uuid.uuid4()
    events.destroyed(case)
    assert spy.cases == [case]


def test_a_subscribed_method_does_not_keep_its_owner_alive() -> None:
    events = KeyEvents()
    spy = Spy()
    events.subscribe(spy.on_case, spy.on_all)
    assert events.subscribers == 1
    del spy
    gc.collect()
    events.destroyed(uuid.uuid4())  # nothing to call, and nothing raises
    events.forget_all()
    assert events.subscribers == 0


def test_there_is_one_shared_registry_for_the_process() -> None:
    assert isinstance(key_events, KeyEvents)
