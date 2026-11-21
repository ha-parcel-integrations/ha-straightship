"""The parcel contract's checks, run over every normalised sample."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.straightship import parcels
from custom_components.straightship.const import ParcelStatus
from custom_components.straightship.parcels import (
    is_pickup,
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import FAILED_TEXT, active_sample, delivered_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]

ZONE = ZoneInfo("America/Toronto")


def _problem() -> dict:
    sample = active_sample()
    sample["package_status"] = FAILED_TEXT
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "problem": _problem,
    "bare": lambda: {"tracking_code": "STRTD0000000000003"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done(parcel):
    if parcel["status"] is ParcelStatus.DELIVERED:
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_no_pickup_point_name_is_invented(parcel):
    assert parcel["pickup_point"] is None
    assert parcel["pickup"] is False  # no pickup status seen in the samples


def test_the_delivered_parcel_carries_its_delivery_time():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is not None


def test_planned_is_emptied_for_done_parcels():
    for status in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert planned_for_status(status, "a", "b") == (None, None)
    assert planned_for_status(ParcelStatus.PROBLEM, "a", "b") == ("a", "b")


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=ZONE)
    assert start == "2099-04-29T13:00:00Z"
    stop = datetime.fromisoformat(end)
    assert (stop.hour, stop.minute, stop.second, stop.microsecond) == (23, 59, 59, 0)
    assert stop.date() == parse_iso(start).astimezone(ZONE).date()


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-04-29", None, zone=ZONE)
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end)
    assert (first.hour, first.minute, first.second) == (0, 0, 0)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
    assert first.date() == last.date()
    assert first.tzinfo is not None and first.utcoffset() == last.utcoffset()


def test_pickup_route_is_sticky_after_collection():
    assert is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.AT_PICKUP_POINT])
    assert is_pickup(True, ParcelStatus.DELIVERED)
    assert not is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.IN_TRANSIT])


def test_the_delivery_moment_stays_utc():
    parcel = normalize_parcel(delivered_sample())
    assert parse_iso(parcel["delivered_at"]).tzinfo == UTC


def test_normaliser_sets_pickup_once_a_pickup_status_is_in_the_history(monkeypatch):
    monkeypatch.setattr(
        parcels,
        "map_event_status",
        lambda text: ParcelStatus.AT_PICKUP_POINT if text == "AT_POINT" else None,
    )
    raw = dict(active_sample())
    raw["history"] = [{"status": "AT_POINT", "timestamp": "2026-01-05T10:00:00Z"}]
    assert normalize_parcel(raw)["pickup"] is True
