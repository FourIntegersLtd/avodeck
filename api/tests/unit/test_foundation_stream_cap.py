"""The cap on open event streams per person (scalability PR 12): in this process, self-healing.

A lease is taken when a stream starts and given back when it ends. One that is never given back (a client
that left before the body began) expires on its own, so a leak cannot lock a person out for good.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import ApiError
from app.core.rate_limits import StreamCap


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def test_a_fourth_stream_is_refused_with_too_many_streams_and_a_retry_after(clock: Clock) -> None:
    cap, person = StreamCap(3, clock=clock), uuid.uuid4()
    for _ in range(3):
        cap.acquire(person, lifetime=300)
    with pytest.raises(ApiError) as refused:
        cap.acquire(person, lifetime=300)
    assert refused.value.status == 429
    assert refused.value.code == "too_many_streams"
    assert int(refused.value.headers["Retry-After"]) >= 1


def test_closing_one_frees_a_place(clock: Clock) -> None:
    cap, person = StreamCap(3, clock=clock), uuid.uuid4()
    leases = [cap.acquire(person, lifetime=300) for _ in range(3)]
    leases[0].release()
    cap.acquire(person, lifetime=300)
    assert cap.open_count(person) == 3


def test_releasing_twice_frees_only_one_place(clock: Clock) -> None:
    cap, person = StreamCap(3, clock=clock), uuid.uuid4()
    leases = [cap.acquire(person, lifetime=300) for _ in range(3)]
    leases[0].release()
    leases[0].release()
    assert cap.open_count(person) == 2


def test_another_person_is_unaffected(clock: Clock) -> None:
    cap, busy, other = StreamCap(3, clock=clock), uuid.uuid4(), uuid.uuid4()
    for _ in range(3):
        cap.acquire(busy, lifetime=300)
    cap.acquire(other, lifetime=300)
    assert cap.open_count(other) == 1


def test_a_lease_never_given_back_expires_with_the_stream_it_was_for(clock: Clock) -> None:
    cap, person = StreamCap(3, clock=clock), uuid.uuid4()
    for _ in range(3):
        cap.acquire(person, lifetime=300)
    clock.now += 301
    cap.acquire(person, lifetime=300)
    assert cap.open_count(person) == 1


def test_a_limit_of_zero_is_no_limit(clock: Clock) -> None:
    cap, person = StreamCap(0, clock=clock), uuid.uuid4()
    for _ in range(10):
        cap.acquire(person, lifetime=300)


def test_a_person_with_nothing_open_leaves_nothing_behind(clock: Clock) -> None:
    cap, person = StreamCap(3, clock=clock), uuid.uuid4()
    cap.acquire(person, lifetime=300).release()
    assert cap.people_tracked() == 0
