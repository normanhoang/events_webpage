import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from automation.publish_update import MAX_EVENTS, VERIFICATION_MAX_AGE_DAYS
from automation.review_schedule import ROTATION_DAYS, assign_review_phases, review_status

NOW = datetime(2030, 5, 2, 12, tzinfo=ZoneInfo("America/New_York"))
KEYS = [f"event-{index}" for index in range(MAX_EVENTS)]
PHASES = assign_review_phases(KEYS)
STARTS = NOW + timedelta(days=90)
CEILING = math.ceil(MAX_EVENTS / ROTATION_DAYS)

# Nights are ABSOLUTE offsets from NOW. Skipping an offset is a missed night, so a simulation
# must advance one night at a time — jumping a day silently changes what is being measured.
CONVERGED_THROUGH = 39


def night(verified, offset):
    """Run one night at NOW+offset: refresh everything due, return (load, oldest age after)."""
    day = NOW + timedelta(days=offset)
    due = [key for key, stamp in verified.items() if review_status(PHASES[key], stamp, STARTS, day) == "due"]
    for key in due:
        verified[key] = day
    return len(due), max((day - stamp).days for stamp in verified.values())


def converge():
    """Drive a synchronized catalog until the rotation reaches its steady ladder."""
    verified = {key: NOW for key in KEYS}
    for offset in range(1, CONVERGED_THROUGH + 1):
        night(verified, offset)
    return verified


def after_outage(missed):
    """State after skipping `missed` consecutive nights from the steady ladder."""
    verified = converge()
    return night(verified, CONVERGED_THROUGH + missed + 1)


def test_imminent_events_are_reviewed_every_night():
    starts = NOW + timedelta(days=3)

    for key in KEYS:
        assert review_status(PHASES[key], NOW, starts, NOW) == "nightly"


def test_phases_are_evenly_spread_and_order_independent():
    phases = assign_review_phases(KEYS)

    assert set(phases.values()) == set(range(ROTATION_DAYS))
    assert phases == assign_review_phases(list(reversed(KEYS)))
    # Even spread is the whole point: no phase may carry a disproportionate share.
    assert max(Counter(phases.values()).values()) <= CEILING


def test_rotation_period_must_be_shorter_than_the_freshness_gate():
    # Measured: the load doubles once the period equals the gate, because the boundary rule then
    # pulls events in early on top of the phase bucket. Keep the period strictly shorter.
    assert ROTATION_DAYS < VERIFICATION_MAX_AGE_DAYS


def test_steady_state_load_is_flat():
    verified = converge()
    loads = [night(verified, CONVERGED_THROUGH + step)[0] for step in range(1, 13)]

    # Flatness is a steady-state property, measured on consecutive nights.
    assert max(loads) <= CEILING


def test_a_staggered_catalog_converges_within_one_rotation():
    # Deliberately unsynchronised ages, as after a manual edit or an off-cycle addition.
    verified = {
        key: NOW - timedelta(days=index % VERIFICATION_MAX_AGE_DAYS)
        for index, key in enumerate(KEYS)
    }
    loads = [night(verified, offset)[0] for offset in range(1, ROTATION_DAYS * 2 + 1)]

    # The back half of the window is the converged state; the front half is the transient.
    assert max(loads[ROTATION_DAYS:]) <= CEILING


def test_refreshing_every_due_event_keeps_the_catalog_inside_the_gate():
    for missed in (1, 2, 3, 4, 5):
        load, age = after_outage(missed)

        # The boundary rule marks every overdue event due, so a completed run always lands back
        # inside the gate — even when the recovery night is the whole catalog. Production safety
        # therefore rests on the run completing: a run cut off by the scheduler writes no prepare
        # state, and the publisher refuses to act at all rather than shipping a stale catalog.
        assert age < VERIFICATION_MAX_AGE_DAYS
        assert load <= MAX_EVENTS


def test_an_outage_clumps_on_the_recovery_night():
    # Honest characterisation, not a target. After ANY missed night the work is genuinely due, so
    # the recovery night is heavier than the steady ceiling; at four missed nights the whole
    # catalog is due at once. No schedule can invent the nights that were skipped.
    for missed in (1, 2, 3, 4):
        load, _ = after_outage(missed)

        assert load > CEILING


def test_the_recovery_clump_grows_with_the_length_of_the_outage():
    loads = [after_outage(missed)[0] for missed in (1, 2, 3)]

    assert loads == sorted(loads) and loads[0] < loads[-1]


def test_naive_timestamps_are_rejected_rather_than_classified():
    naive = datetime(2030, 5, 2, 12)

    with pytest.raises(ValueError, match="aware datetime"):
        review_status(0, naive, STARTS, NOW)
    with pytest.raises(ValueError, match="aware datetime"):
        review_status(0, NOW, STARTS, naive)


def test_a_future_verified_at_is_never_left_on_carry():
    starts = NOW + timedelta(days=90)
    future = NOW + timedelta(days=2)

    # A future stamp means the record is already wrong; "carry" would silently keep it.
    assert review_status(PHASES[KEYS[0]], future, starts, NOW) == "due"


def test_the_rotation_slot_follows_new_york_time_not_the_caller_offset():
    # 00:30 UTC is still the previous calendar day in New York; a UTC-aware caller must not shift
    # the phase and skip or repeat a slot.
    utc = datetime(2030, 5, 3, 0, 30, tzinfo=timezone.utc)
    nyc = utc.astimezone(ZoneInfo("America/New_York"))
    assert (utc.day, nyc.day) == (3, 2)

    slot = nyc.date().toordinal() % ROTATION_DAYS
    key = next(candidate for candidate in KEYS if PHASES[candidate] == slot)
    stamp = nyc - timedelta(days=2)
    starts = nyc + timedelta(days=90)

    assert review_status(PHASES[key], stamp, starts, utc) == "due"
