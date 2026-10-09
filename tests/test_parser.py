"""Tests for the result-page parser."""
import pytest

from custom_components.straightship.parser import (
    StraightshipParseError,
    clean_text,
    parse_page,
)

from .payloads import (
    ACTIVE_CODE,
    CITY,
    FAILED_TEXT,
    FAILED_TEXT_HTML,
    NO_RESULT_PAGE,
    page,
    row,
)


def test_parses_summary_and_history_in_document_order():
    parsed = parse_page(page())
    assert parsed["tracking_id"] == ACTIVE_CODE
    assert parsed["package_status"] == "Delivered"
    assert parsed["last_updated"] == "2026-01-05 15:04:05"
    stamps = [event["timestamp"] for event in parsed["history"]]
    assert stamps == sorted(stamps)
    assert stamps[1] == stamps[2]  # two rows share one second
    assert "Origin Scan" in parsed["history"][1]["status"]
    assert "Straightship station" in parsed["history"][2]["status"]
    assert parsed["history"][0]["city"] is None
    assert parsed["history"][1]["city"] == CITY


def test_dash_means_absent_and_values_are_kept():
    parsed = parse_page(page())
    assert parsed["client_code"] is None and parsed["package_id"] is None
    parsed = parse_page(page(client_code="CL-1", package_id="PK-1"))
    assert parsed["client_code"] == "CL-1"
    assert parsed["package_id"] == "PK-1"


def test_column_layout_summary_is_read_by_label():
    parsed = parse_page(page(column_layout=True))
    assert parsed["tracking_id"] == ACTIVE_CODE
    assert parsed["package_status"] == "Delivered"


def test_status_cell_text_content_includes_obfuscated_mail_link():
    parsed = parse_page(
        page(rows=[("2026-01-03 17:00:00", FAILED_TEXT_HTML, CITY)])
    )
    assert parsed["history"][0]["status"] == FAILED_TEXT


def test_script_text_and_breaks_are_handled():
    html = page(rows=[("2026-01-03 17:00:00", "Loading<br/>now<script>x()</script>", "")])
    assert parse_page(html)["history"][0]["status"] == "Loading now"


def test_labels_are_matched_case_insensitively_not_by_position():
    html = page().replace("Tracking ID", "tracking id")
    assert parse_page(html)["tracking_id"] == ACTIVE_CODE


def test_no_results_page_is_none():
    assert parse_page(NO_RESULT_PAGE) is None


def test_missing_summary_is_a_parse_error():
    with pytest.raises(StraightshipParseError):
        parse_page("<html><body><p>Maintenance</p></body></html>")


def test_missing_history_table_is_a_parse_error():
    html = page().replace("<th>Timestamp</th>", "<th>When</th>")
    with pytest.raises(StraightshipParseError):
        parse_page(html)


def test_missing_last_updated_is_none_and_short_rows_are_skipped():
    html = page().replace("<p>Last Updated: 2026-01-05 15:04:05</p>", "")
    html = html.replace("</tbody>", "<tr><td>stray</td></tr></tbody>")
    parsed = parse_page(html)
    assert parsed["last_updated"] is None
    assert len(parsed["history"]) == 5


def test_history_row_without_city_cell():
    html = page().replace("</tbody>", "<tr><td>2026-01-06 00:00:00</td><td>Loading</td></tr></tbody>")
    assert parse_page(html)["history"][-1]["city"] is None


def test_unclosed_markup_still_yields_rows():
    html = page().replace("</tbody></table>", "")
    html = html.replace("</td></tr><tr><td>2026-01-05", "</td><tr><td>2026-01-05")
    assert parse_page(html)["history"]


def test_self_closing_tags_and_row_helper():
    assert parse_page(page().replace("<h5>", "<br/><hr><h5>"))["tracking_id"] == ACTIVE_CODE
    assert row("t", "Loading", None)[2] == ""


def test_clean_text_collapses_whitespace():
    assert clean_text(" a \n\xa0b ") == "a b"


def test_empty_tables_and_self_closing_elements_are_ignored():
    html = page().replace("<h5>", "<table></table><span/><h5>")
    assert parse_page(html)["tracking_id"] == ACTIVE_CODE
