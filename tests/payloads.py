"""Synthetic Straightship result pages and parsed payloads shared by the tests.

Every value is invented: codes, cities, timestamps and names carry no real
parcel data. Only the page layout, labels and status sentences mirror the live
page.
"""
from __future__ import annotations

from html import escape

ACTIVE_CODE = "STRTD0000000000001"
DELIVERED_CODE = "STRTD0000000000002"

CITY = "Testville"
PROVINCE = "ON"

REGISTERED_TEXT = "Package information is created in the system"
ORIGIN_TEXT = "Package Received with Origin Scan warehouse"
STATION_TEXT = "Package is received at a Straightship station"
TRANSIT_TEXT = "Package is in transit to a Straightship station"
LOADING_TEXT = "Loading"
OFD_TEXT = "Package will be delivered before 9:00pm"
DELIVERED_TEXT = "Delivered"
FAILED_TEXT_HTML = (
    "An attempt was made to deliver your package-unable to leave package at safe "
    'location (please e-mail <a href="/cdn-cgi/l/email-protection" '
    'class="__cf_email__" data-cfemail="00">[email&#160;protected]</a> '
    "to make arrangement)"
)
FAILED_TEXT = (
    "An attempt was made to deliver your package-unable to leave package at safe "
    "location (please e-mail [email protected] to make arrangement)"
)


def row(timestamp: str, status_html: str, city: str | None = CITY, *, located=True):
    """One history row as ``(timestamp, status cell html, city)``."""
    suffix = f" ({city}, {PROVINCE})" if city and located else ""
    return (timestamp, status_html + suffix, city or "")


def page(
    code: str = ACTIVE_CODE,
    *,
    summary_status: str = "Delivered",
    rows: list[tuple[str, str, str]] | None = None,
    client_code: str = "-",
    package_id: str = "-",
    last_updated: str = "2026-01-05 15:04:05",
    column_layout: bool = False,
) -> str:
    """A synthetic result page in the live layout."""
    rows = rows if rows is not None else DELIVERED_ROWS
    labels = [
        ("Client Code", client_code),
        ("Tracking ID", code),
        ("Package ID", package_id),
        ("Package Status", summary_status),
    ]
    if column_layout:
        summary = (
            "<table><tr>"
            + "".join(f"<th>{label}</th>" for label, _ in labels)
            + "</tr><tr>"
            + "".join(f"<td>{escape(value)}</td>" for _, value in labels)
            + "</tr></table>"
        )
    else:
        summary = "<table>" + "".join(
            f"<tr><th>{label}</th><td>{escape(value)}</td></tr>"
            for label, value in labels
        ) + "</table>"
    body = "".join(
        f"<tr><td>{ts}</td><td>{status}</td><td>{escape(city)}</td></tr>"
        for ts, status, city in rows
    )
    return (
        "<html><head><script>var x = 'Package Status';</script></head><body>"
        '<div class="wrap"><div id="primary"><h3>Canada B2C Shipment Tracking</h3>'
        f"<p>Last Updated: {last_updated}</p>{summary}"
        '<div id="pod"><form name="po_form"><input name="order_name"></form></div>'
        "<h5>History</h5><table><thead><tr><th>Timestamp</th><th>Status</th>"
        f"<th>City</th></tr></thead><tbody>{body}</tbody></table>"
        "<p>Note: times are shown in UTC.</p></div></div></body></html>"
    )


DELIVERED_ROWS = [
    ("2026-01-02 09:00:00", REGISTERED_TEXT, ""),
    ("2026-01-02 12:30:00", ORIGIN_TEXT + f" ({CITY}, {PROVINCE})", CITY),
    ("2026-01-02 12:30:00", STATION_TEXT + f" ({CITY}, {PROVINCE})", CITY),
    ("2026-01-03 08:00:00", OFD_TEXT + f" ({CITY}, {PROVINCE})", CITY),
    ("2026-01-05 15:04:05", DELIVERED_TEXT + f" ({CITY}, {PROVINCE})", CITY),
]

ACTIVE_ROWS = DELIVERED_ROWS[:4]

NO_RESULT_PAGE = (
    '<html><body><div id="primary"><h3>Canada B2C Shipment Tracking</h3>'
    "<p>No results found!</p></div></body></html>"
)


def active_html(code: str = ACTIVE_CODE) -> str:
    return page(code, summary_status="Delivered", rows=ACTIVE_ROWS)


def _raw(code: str, summary_status: str, rows) -> dict:
    return {
        "tracking_code": code,
        "client_code": None,
        "tracking_id": code,
        "package_id": None,
        "package_status": summary_status,
        "last_updated": "2026-01-05 15:04:05",
        "history": [
            {"timestamp": ts, "status": status, "city": city or None}
            for ts, status, city in rows
        ],
        "html": "<html></html>",
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """The parsed payload of a delivered parcel."""
    return _raw(code, "Delivered", DELIVERED_ROWS)


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """The parsed payload of an out-for-delivery parcel.

    The summary word of an in-flight parcel is unobserved, so this uses an
    invented one and leans on the history fallback.
    """
    return _raw(code, "In Progress", ACTIVE_ROWS)
