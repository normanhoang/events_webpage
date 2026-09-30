import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from automation.publish_update import MAX_EVENTS, RUN_INTERVAL_DAYS, VERIFICATION_MAX_AGE_DAYS
from automation.review_schedule import (
    IMMINENT_BUDGET,
    ROTATION_DAYS,
    assign_review_phases,
    review_status,
    split_worklist,
)
from automation.theater_deals import VERIFICATION_MAX_AGE_DAYS as THEATER_GATE

NOW = datetime(2030, 5, 2, 12, tzinfo=ZoneInfo("America/New_York"))
KEYS = [f"event-{index}" for index in range(MAX_EVENTS)]
PHASES = assign_review_phases(KEYS)
# Far enough out that the imminent rule stays out of scope: every offset here is RUN_INTERVAL_DAYS
# of calendar time, so a ninety-day horizon is reached in thirteen runs, after which every event
# reads as "nightly" and each run becomes a whole-catalog sweep that has nothing to do with the
# rotation under test.
STARTS = NOW + timedelta(days=900)
CEILING = math.ceil(MAX_EVENTS / ROTATION_DAYS)

# Runs are ABSOLUTE offsets from NOW, one RUN_INTERVAL_DAYS apart: the card runs weekly, so a
# harness that advanced one day at a time would measure a schedule the pipeline no longer has.
# Skipping an offset is a missed RUN, which changes what is being measured.
CONVERGED_THROUGH = 8


def run(verified, offset, starts=STARTS):
    """Run one scheduled cycle at NOW + offset*RUN_INTERVAL_DAYS, refreshing the work labels.

    The real consumer refreshes both "nightly" and "due"; a harness that refreshed only "due"
    would model a caller that skips imminent events, so it would measure the wrong thing.
    """
    day = NOW + timedelta(days=offset * RUN_INTERVAL_DAYS)
    due = [
        key
        for key, stamp in verified.items()
        if review_status(PHASES[key], stamp, starts, day) in ("nightly", "due")
    ]
    for key in due:
        verified[key] = day
    return len(due), max((day - stamp).days for stamp in verified.values())


def converge():
    """Drive a synchronized catalog until the rotation reaches its steady ladder."""
    verified = {key: NOW for key in KEYS}
    for offset in range(1, CONVERGED_THROUGH + 1):
        run(verified, offset)
    return verified


def after_outage(missed):
    """State after skipping `missed` consecutive runs from the steady ladder."""
    verified = converge()
    return run(verified, CONVERGED_THROUGH + missed + 1)


def entries(count, *, days_until=0, status="nightly", age_days=0, offset=0):
    return [
        {
            "key": f"event-{offset + index}",
            "status": status,
            "days_until": days_until + index,
            "age_days": age_days,
        }
        for index in range(count)
    ]


def test_imminent_events_are_reviewed_every_run():
    starts = NOW + timedelta(days=3)

    for key in KEYS:
        assert review_status(PHASES[key], NOW, starts, NOW) == "nightly"


def test_phases_are_evenly_spread_and_order_independent():
    phases = assign_review_phases(KEYS)

    assert set(phases.values()) == set(range(ROTATION_DAYS))
    assert phases == assign_review_phases(list(reversed(KEYS)))
    # Even spread is the whole point: no phase may carry a disproportionate share.
    assert max(Counter(phases.values()).values()) <= CEILING


def test_the_gate_clears_two_weekly_run_intervals():
    # The cadence, not taste, sizes the gate: a record verified on one run is two intervals old at
    # the second run after it, and the boundary rule is what pulls it back in before it can fall
    # out. A gate of ONE interval marks every record in the catalog due on every run, so each
    # weekly run becomes a whole-catalog sweep that cannot finish inside the interrupt.
    assert VERIFICATION_MAX_AGE_DAYS == 2 * RUN_INTERVAL_DAYS
    # The rotation must fit the skip budget the gate allows, or the boundary rule clumps a whole
    # untouched cohort onto one run on top of its phase bucket.
    assert ROTATION_DAYS <= VERIFICATION_MAX_AGE_DAYS // RUN_INTERVAL_DAYS


def test_the_two_validators_share_one_freshness_gate():
    # Two validators that disagree about one field let one accept what the other rejects. The
    # theater seed keeps its own copy of the gate, so pin the copies together instead of trusting
    # two literals to stay in step.
    assert THEATER_GATE == VERIFICATION_MAX_AGE_DAYS


def test_steady_state_load_is_flat():
    verified = converge()
    loads = [run(verified, CONVERGED_THROUGH + step)[0] for step in range(1, 9)]

    # Flatness is a steady-state property, measured on consecutive runs: one weekly run carries a
    # phase's share of the catalog and the next carries the other. The whole catalog on one run is
    # the failure this rotation exists to prevent.
    assert max(loads) <= CEILING
    assert max(loads) < MAX_EVENTS


def test_the_published_catalog_never_holds_a_record_older_than_one_interval():
    verified = converge()
    ages = [run(verified, CONVERGED_THROUGH + step)[1] for step in range(1, 9)]

    # Half the catalog is refreshed each run and the other half is one interval old, so the gate is
    # never the binding constraint in steady state — it only has to clear a missed run.
    assert max(ages) <= RUN_INTERVAL_DAYS


def test_a_staggered_catalog_converges_within_one_rotation():
    # Deliberately unsynchronised ages, as after a manual edit or an off-cycle addition.
    verified = {
        key: NOW - timedelta(days=index % VERIFICATION_MAX_AGE_DAYS)
        for index, key in enumerate(KEYS)
    }
    loads = [run(verified, offset)[0] for offset in range(1, ROTATION_DAYS * 2 + 1)]

    # The tail of the window is the converged state; the front is the transient.
    assert max(loads[ROTATION_DAYS:]) <= CEILING


def test_refreshing_every_due_event_keeps_the_catalog_inside_the_gate():
    for missed in (1, 2, 3, 4, 5):
        load, age = after_outage(missed)

        # The boundary rule marks every overdue event due, so a completed run always lands back
        # inside the gate — even when the recovery night is the whole catalog. Production safety
        # therefore rests on the run completing: a run cut off by the scheduler writes no prepare
        # state, and the publisher refuses to act at all rather than shipping a stale catalog.
        # The tightened bound is what the boundary rule actually delivers: carried events sit at
        # most at gate-2, so loosening the boundary would now fail the suite instead of passing.
        assert age <= VERIFICATION_MAX_AGE_DAYS - 2
        assert load <= MAX_EVENTS


def test_an_outage_clumps_on_the_recovery_night():
    # Honest characterisation, not a target. After ANY missed run the work is genuinely due, so
    # the recovery run is heavier than the steady ceiling; at four missed runs the whole catalog
    # is due at once. No schedule can invent the runs that were skipped.
    for missed in (1, 2, 3, 4):
        load, _ = after_outage(missed)

        assert load > CEILING


def test_the_recovery_clump_saturates_after_one_missed_run():
    # A weekly cadence has no ladder of outages to climb: one missed run already leaves every
    # record two intervals old, so the whole catalog comes due in the same run and stays there
    # however long the gap. The clump is bounded by the catalog, not by the number of missed runs.
    for missed in (1, 2, 3, 4):
        assert after_outage(missed)[0] == MAX_EVENTS


def test_naive_timestamps_are_rejected_rather_than_classified():
    naive = datetime(2030, 5, 2, 12)

    with pytest.raises(ValueError, match="aware datetime"):
        review_status(0, naive, STARTS, NOW)
    with pytest.raises(ValueError, match="aware datetime"):
        review_status(0, NOW, naive, NOW)
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


def test_a_clustered_catalog_cannot_overrun_the_run_budget():
    # Replaying the shipped catalog reaches 16 due events on its heaviest night — roughly 256s of
    # verification against the scheduler's 180s interrupt. The budget is what keeps it runnable.
    heavy = entries(16)

    review, deferred = split_worklist(heavy)

    assert len(review) == IMMINENT_BUDGET
    assert len(deferred) == len(heavy) - IMMINENT_BUDGET


def test_imminent_work_takes_the_budget_before_rotation_work():
    imminent = entries(4)
    rotation = entries(20, days_until=20, status="due", age_days=5, offset=200)

    review, deferred = split_worklist(imminent + rotation)

    reviewed = {entry["key"] for entry in review}
    # Every imminent entry is reviewed, and rotation work fills only the leftover budget.
    assert {entry["key"] for entry in imminent} <= reviewed
    assert {entry["key"] for entry in review if entry["status"] == "due"} == {
        entry["key"] for entry in rotation[: IMMINENT_BUDGET - 4]
    }
    assert len(deferred) == len(rotation) - (IMMINENT_BUDGET - 4)


def test_rotation_work_yields_entirely_on_a_fully_loaded_night():
    # If imminent work already exhausts the budget, no rotation work is scheduled at all — it
    # keeps gate slack and the boundary rule pulls it in later.
    imminent = entries(IMMINENT_BUDGET)
    rotation = entries(6, days_until=20, status="due", age_days=5, offset=300)

    review, deferred = split_worklist(imminent + rotation)

    assert all(entry["status"] == "nightly" for entry in review)
    assert {entry["key"] for entry in deferred} == {entry["key"] for entry in rotation}


def test_worklist_orders_imminent_work_by_proximity():
    shuffled = list(reversed(entries(6)))

    review, _ = split_worklist(shuffled)

    days = [entry["days_until"] for entry in review]
    assert days == sorted(days)
