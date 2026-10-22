"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance: ``parcels.py`` is free of I/O.
"""

from datetime import UTC, datetime, timedelta
import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.straightship.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    ParcelStatus,
)
from custom_components.straightship.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    normalize_status_text,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
    tracking_url,
)

from .payloads import (
    ACTIVE_CODE,
    CITY,
    DELIVERED_CODE,
    DELIVERED_ROWS,
    FAILED_TEXT,
    LOADING_TEXT,
    OFD_TEXT,
    ORIGIN_TEXT,
    REGISTERED_TEXT,
    STATION_TEXT,
    TRANSIT_TEXT,
    active_sample,
    delivered_sample,
)

# ---------------------------------------------------------------------------
# status mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (REGISTERED_TEXT, ParcelStatus.REGISTERED),
        (f"{ORIGIN_TEXT} ({CITY}, ON)", ParcelStatus.IN_TRANSIT),
        (f"{STATION_TEXT} ({CITY}, ON)", ParcelStatus.IN_TRANSIT),
        (f"{TRANSIT_TEXT} ({CITY}, ON)", ParcelStatus.IN_TRANSIT),
        (LOADING_TEXT, ParcelStatus.IN_TRANSIT),
        (f"{OFD_TEXT} ({CITY}, ON)", ParcelStatus.OUT_FOR_DELIVERY),
        (FAILED_TEXT, ParcelStatus.PROBLEM),
        (f"Delivered ({CITY}, ON)", ParcelStatus.DELIVERED),
        ("Delivered", ParcelStatus.DELIVERED),
    ],
)
def test_map_event_status_known_sentences(text, expected):
    assert map_event_status(text) == expected


def test_normalize_status_text_strips_location_and_collapses_whitespace():
    assert (
        normalize_status_text(
            f"  Package is  received\n at a Straightship station   ({CITY},  ON) "
        )
        == STATION_TEXT
    )


def test_location_strip_leaves_the_contact_parenthetical_alone():
    """Only a trailing "(City, PR)" is a location; the arrangement note is text."""
    text = normalize_status_text(FAILED_TEXT)
    assert text.endswith("(please e-mail <address> to make arrangement)")
    assert normalize_status_text(f"{FAILED_TEXT} ({CITY}, ON)") == text


def test_failed_attempt_maps_with_a_plain_contact_address():
    plain = FAILED_TEXT.replace("[email protected]", "help@example.invalid")
    assert map_event_status(plain) == ParcelStatus.PROBLEM


def test_map_event_status_missing_and_unmapped_are_none():
    assert map_event_status(None) is None
    assert map_event_status("") is None
    assert map_event_status("Something new happened") is None


def test_map_parcel_status_summary_wins():
    assert map_parcel_status("Delivered", [OFD_TEXT]) == ParcelStatus.DELIVERED


def test_map_parcel_status_missing_summary_is_silently_unknown(caplog):
    with caplog.at_level(logging.WARNING):
        assert map_parcel_status(None) == ParcelStatus.UNKNOWN
        assert map_parcel_status("") == ParcelStatus.UNKNOWN
    assert caplog.text == ""


def test_unmapped_summary_falls_back_to_newest_mappable_history_event(caplog):
    history = [REGISTERED_TEXT, f"{OFD_TEXT} ({CITY}, ON)", "Unlisted sentence"]
    with caplog.at_level(logging.WARNING):
        assert map_parcel_status("Mid Flight", history) == ParcelStatus.OUT_FOR_DELIVERY
        assert map_parcel_status("Mid Flight", history) == ParcelStatus.OUT_FOR_DELIVERY
    # The summary words of a parcel in flight are the gap we still need
    # reported, so a rescued fallback warns too — once, and without the text.
    assert caplog.text.count("Unrecognised Straightship summary status") == 1
    assert "out_for_delivery" in caplog.text
    assert "Mid Flight" not in caplog.text


def test_unmapped_everywhere_is_unknown_with_one_category_only_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert map_parcel_status("Zq-secret-word", ["Other"]) == ParcelStatus.UNKNOWN
        assert map_parcel_status("Zq-secret-word", ["Other"]) == ParcelStatus.UNKNOWN
    assert caplog.text.count("Unrecognised Straightship summary status") == 1
    assert "Zq-secret-word" not in caplog.text
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == UTC
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_reads_the_page_format_as_utc():
    assert to_iso_timestamp("2026-01-02 09:00:00") == "2026-01-02T09:00:00+00:00"
    assert to_iso_timestamp(" 2026-01-02 09:00:00 ") == "2026-01-02T09:00:00+00:00"
    assert to_iso_timestamp("02/01/2026") is None
    assert to_iso_timestamp("") is None
    assert to_iso_timestamp(None) is None


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def _events(*pairs):
    return [{"timestamp": ts, "status": status, "city": None} for ts, status in pairs]


def test_build_history_is_oldest_to_newest_with_stripped_sentences():
    history = build_history(delivered_sample()["history"])
    assert len(history) == 5
    assert history[0] == {
        "timestamp": "2026-01-02T09:00:00+00:00",
        "status": ParcelStatus.REGISTERED,
        "raw_status": REGISTERED_TEXT,
    }
    assert history[-1]["status"] == ParcelStatus.DELIVERED
    assert history[-1]["raw_status"] == "Delivered"
    assert CITY not in str(history)


def test_build_history_keeps_document_order_within_one_timestamp():
    history = build_history(
        _events(
            ("2026-01-02 12:30:00", f"{ORIGIN_TEXT} (A, ON)"),
            ("2026-01-02 12:30:00", f"{STATION_TEXT} (A, ON)"),
            ("2026-01-02 12:30:00", LOADING_TEXT),
        )
    )
    assert [e["raw_status"] for e in history] == [
        ORIGIN_TEXT,
        STATION_TEXT,
        LOADING_TEXT,
    ]


def test_build_history_sorts_an_out_of_order_page_stably():
    history = build_history(
        _events(
            ("2026-01-03 08:00:00", OFD_TEXT),
            ("2026-01-02 09:00:00", REGISTERED_TEXT),
            ("2026-01-03 08:00:00", LOADING_TEXT),
        )
    )
    assert [e["raw_status"] for e in history] == [
        REGISTERED_TEXT,
        OFD_TEXT,
        LOADING_TEXT,
    ]


def test_build_history_caps_to_the_newest_events():
    events = _events(
        *[(f"2026-01-{day:02d} 10:00:00", LOADING_TEXT) for day in range(1, 26)]
    )
    history = build_history(events, max_events=20)
    assert len(history) == 20
    assert history[0]["timestamp"].startswith("2026-01-06")
    assert history[-1]["timestamp"].startswith("2026-01-25")


def test_build_history_default_cap_is_twenty():
    events = _events(
        *[(f"2026-01-{day:02d} 10:00:00", LOADING_TEXT) for day in range(1, 26)]
    )
    assert len(build_history(events)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"status": LOADING_TEXT}]) == []  # no timestamp
    assert build_history([{"timestamp": "garbage", "status": LOADING_TEXT}]) == []
    assert build_history(["not-a-dict"]) == []


def test_build_history_leaves_unmapped_sentences_null_and_warns_once(caplog):
    events = _events(("2026-01-02 09:00:00", "A brand new sentence (Z, ON)"))
    with caplog.at_level(logging.WARNING):
        history = build_history(events)
        build_history(events)
    assert history[0]["status"] is None
    assert history[0]["raw_status"] == "A brand new sentence"
    assert caplog.text.count("Unrecognised Straightship history status") == 1
    assert "brand new" not in caplog.text


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

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


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES


def test_a_capability_is_never_both_populated_and_pending():
    assert not CAPABILITIES & PENDING_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Declared capabilities come true; everything else stays literally None."""
    delivered = normalize_parcel(delivered_sample())
    active = normalize_parcel(active_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None
    for field in ("weight", "dimensions", "pickup_point"):
        assert field not in CAPABILITIES
        assert delivered[field] is None and active[field] is None
    assert "delivery_window" not in CAPABILITIES
    assert active["planned_from"] is None and active["planned_to"] is None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Straightship"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Delivered"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-01-05T15:04:05+00:00"
    assert parcel["pickup"] is False
    assert parcel["url"] == (
        f"https://straightship.com/track-your-shipment/tracking-id/{DELIVERED_CODE}/"
    )
    assert parcel["history"] is None  # opt-in, default off


def test_delivered_at_comes_from_the_delivered_event_not_last_updated():
    raw = delivered_sample()
    raw["last_updated"] = "2030-01-01 00:00:00"
    assert normalize_parcel(raw)["delivered_at"] == "2026-01-05T15:04:05+00:00"


def test_delivered_summary_without_a_delivered_event_has_no_delivered_at():
    raw = delivered_sample()
    raw["history"] = raw["history"][:2]
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered_at"] is None


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 5
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_active_parcel_uses_history_fallback():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["raw_status"] == "In Progress"
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None
    assert parcel["barcode"] == ACTIVE_CODE


def test_failed_attempt_then_new_promise_is_not_a_return():
    raw = active_sample()
    raw["history"] = [
        *raw["history"][:3],
        {"timestamp": "2026-01-03 17:00:00", "status": FAILED_TEXT, "city": None},
    ]
    assert normalize_parcel(raw)["status"] == ParcelStatus.PROBLEM
    raw["history"].append(
        {"timestamp": "2026-01-04 08:00:00", "status": OFD_TEXT, "city": CITY}
    )
    assert normalize_parcel(raw)["status"] == ParcelStatus.OUT_FOR_DELIVERY


def test_normalize_pending_placeholder():
    """A tracked-but-unknown code still yields a full parcel dict."""
    parcel = normalize_parcel({"tracking_code": "STRTD0000000000009"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["history"] is None


def test_normalize_keeps_raw_payload_untrimmed():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw
    assert DELIVERED_ROWS  # the rows are not reduced by normalisation
    assert (
        normalize_parcel(delivered_sample(), include_history=True)["raw"]["history"][1][
            "city"
        ]
        == CITY
    )


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(UTC)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels


def test_tracking_url_needs_a_code_and_encodes_it():
    assert tracking_url(None) is None
    assert tracking_url("A/B").endswith("/tracking-id/A%2FB/")
