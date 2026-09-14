"""Deterministic review rotation for the nightly research run.

Consumed by the scheduled briefing script ``~/.hermes/scripts/nightly-events-prepare.py``, which
turns these functions into the night's ranked worklist. Nothing in this repository calls them at
runtime, so do not read an empty in-repo grep as "dead code" — the consumer is the cron script.

Every event must keep a ``verified_at`` inside the validator's freshness gate, but reviewing the
whole catalog on one night does not fit the research run's 3-minute budget. Each retained event
gets a rotation phase and is reviewed once per rotation, so the load spreads across nights
instead of clumping when the catalog ages out together.

Measure the rotation ALONE: on a 24-event catalog with a 7-day gate, a 5-day rotation gives 4-5
reviews a night in steady state, against 24 in one night with no rotation. That flatness is a
steady-state property, not a general one, and it is not the nightly budget — the imminent rule
sits on top of it, so the real worklist is rotation plus every event whose occurrence falls
inside the imminent window. Replaying the shipped catalog, that total runs 7 tonight and peaks
at 16, which is why `split_worklist` bounds it. A staggered catalog converges within about one
rotation, and a multi-night outage genuinely clumps on the recovery night because by then the
work really is due; no schedule can invent the missed nights.

Every label except "carry" is a refresh instruction. An event that is both overdue and near-term
reports "nightly", so a caller acting on "due" alone would skip it — read both lists.
"""

from datetime import timedelta

from automation.publish_update import NYC, VERIFICATION_MAX_AGE_DAYS

# Each retained event is reviewed once per rotation. Keep the period shorter than the gate so a
# phase-scheduled review always lands before the freshness deadline.
# Measured: the load doubles at ROTATION_DAYS == VERIFICATION_MAX_AGE_DAYS (7), because the
# boundary rule then starts pulling events in a day early *in addition to* the phase bucket.
# Periods of 6 and below do not stack the two waves; 6 is the flat end of the safe range and 5
# is used here to keep a full day of slack against the gate.
ROTATION_DAYS = 5

# An occurrence this close is reverified every night regardless of rotation.
IMMINENT_DAYS = 7

# How many verifications a single research run can complete inside the scheduler's 3-minute
# interrupt. Measured against the shipped catalog: the imminent wave alone peaks at 9 events, so
# a budget of 10 keeps every time-critical event reviewable and still leaves room for rotation
# work on lighter nights.
IMMINENT_BUDGET = 10


def split_worklist(entries, *, budget=IMMINENT_BUDGET):
    """Split entries into (review, deferred), imminent work first.

    A run cannot always finish everything: the shipped catalog peaks at 16 due events on one
    night, which is roughly 256s of verification against a 180s interrupt. Time-critical work
    takes the budget first and rotation work yields, because a skipped rotation record keeps days
    of slack inside the gate and the boundary rule pulls it in before it can age out. A skipped
    imminent record is likewise safe — it was verified within the last day or two — so nothing
    here can push a record past the gate; only a run that fails to finish can, and that path is
    fail-closed in the publisher.
    """
    imminent = sorted(
        (entry for entry in entries if entry["status"] == "nightly"),
        key=lambda entry: entry["days_until"],
    )
    rotation = sorted(
        (entry for entry in entries if entry["status"] == "due"),
        key=lambda entry: -entry["age_days"],
    )

    review = imminent[:budget]
    deferred = imminent[budget:]
    room = max(0, budget - len(review))
    review += rotation[:room]
    deferred += rotation[room:]
    return review, deferred


def assign_review_phases(source_keys):
    """Spread a catalog evenly across rotation phases, in a stable order.

    Phases are ranked (``index % ROTATION_DAYS`` over sorted keys), not hashed. Per-key hashing
    looks more principled but leaves the buckets visibly uneven on a catalog this small — measured
    10 events on one night and 2 on another — which recreates the clumping the rotation exists to
    prevent. The trade is churn: adding or removing an event reshuffles later phases, so treat
    phases as stable for a fixed catalog only. The boundary rule absorbs the reshuffle.
    """
    return {key: index % ROTATION_DAYS for index, key in enumerate(sorted(source_keys))}


def review_status(phase, verified_at, next_starts_at, now):
    """Classify one event's review need as nightly, due, or carry."""
    for label, value in (
        ("verified_at", verified_at),
        ("next_starts_at", next_starts_at),
        ("now", now),
    ):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{label} must be an aware datetime.")

    if next_starts_at - now < timedelta(days=IMMINENT_DAYS):
        return "nightly"
    if verified_at > now:
        # A future stamp means the record is already wrong; re-reviewing is the only remedy and
        # the validator rejects the record either way, so never let it settle into "carry".
        return "due"

    age_days = (now - verified_at).days
    # At the boundary the event must be reviewed now, even off its phase. This also rescues a
    # single skipped night, because a record reviewed one day late still lands inside the gate.
    if age_days >= VERIFICATION_MAX_AGE_DAYS - 1:
        return "due"
    # Resolve the slot in New York time: a UTC-aware caller just after midnight would otherwise
    # land on the previous local day and shift the phase, skipping or repeating a slot.
    if age_days >= 1 and phase == now.astimezone(NYC).date().toordinal() % ROTATION_DAYS:
        return "due"
    return "carry"
