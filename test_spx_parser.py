"""Regression tests for SPX PDF text extraction."""

from parsers.spx_parser import _parse_page_text


def test_row_number_prefix_with_and_without_whitespace():
    text = """\
999 TH268950536506H 260808SA929W5V 2026-08-08 18:45:46
1000123456789 260808S81N8PAG 2026-08-08 18:45:46
1001SPX269804308344D 260808RPGT3CY4 2026-08-08 18:45:46
1002JT&2604879214589 260808RVMQH57A 2026-08-08 18:45:46
"""

    rows = _parse_page_text(text, page_num=7)

    assert [row["tracking"] for row in rows] == [
        "TH268950536506H",
        "123456789",
        "SPX269804308344D",
        "JT&2604879214589",
    ]
    assert [row["order_sn"] for row in rows] == [
        "260808SA929W5V",
        "260808S81N8PAG",
        "260808RPGT3CY4",
        "260808RVMQH57A",
    ]
    assert {row["page"] for row in rows} == {7}


def test_joined_numeric_tracking_can_continue_across_pages():
    rows = _parse_page_text(
        "10000123456789 260808NUMERIC1 2026-08-08 18:45:46",
        page_num=8,
        expected_row=10000,
    )

    assert rows == [{
        "tracking": "123456789",
        "order_sn": "260808NUMERIC1",
        "pickup_time": "2026-08-08 18:45:46",
        "page": 8,
    }]
