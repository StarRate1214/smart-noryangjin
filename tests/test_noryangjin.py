import io

import pandas as pd
import pytest

import noryangjin

HEADER = "<tr><th>어종</th><th>산지</th><th>규격</th><th>포장</th><th>수량</th><th>중량</th>" \
         "<th>낙찰고가</th><th>낙찰저가</th><th>평균가</th></tr>"


def html_table(*rows, title=True):
    body = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows)
    caption = "<tr><td colspan=9>어종별 경락시세</td></tr>" if title else ""
    return f"<html><body><table>{caption}{HEADER}{body}</table></body></html>"


def test_parse_html_cp949_with_title_row_and_commas():
    row = ("(활)방어", "일본", "1미", "kg", "1,234.5", "1,000", "33,000", "15,000", "29,400")
    content = html_table(row).encode("cp949")
    df = noryangjin.parse_table(content)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["어종"] == "(활)방어"
    assert row["수량"] == pytest.approx(1234.5)
    assert row["중량"] == 1000
    assert row["낙찰고가"] == 33000


def test_parse_xlsx_with_title_row():
    buf = io.BytesIO()
    pd.DataFrame([["제목", None, None], ["어종", "규격", "낙찰고가"], ["(활)넙치", "1미", "17,000"]]).to_excel(
        buf, header=False, index=False
    )
    df = noryangjin.parse_table(buf.getvalue())
    assert list(df.columns) == ["어종", "규격", "낙찰고가"]
    assert df.iloc[0]["낙찰고가"] == 17000


def test_header_only_table_is_empty():
    content = f"<table>{HEADER}<tr><td colspan=9>조회된 데이터가 없습니다.</td></tr></table>".encode()
    assert noryangjin.parse_table(content).empty


def test_empty_body_is_empty():
    assert noryangjin.parse_table(b"").empty
    assert noryangjin.parse_table(b"   ").empty


def test_block_page_raises_fetch_error_with_content():
    with pytest.raises(noryangjin.FetchError) as info:
        noryangjin.parse_table(b"<html><body>access denied</body></html>")
    assert info.value.content is not None


def test_rows_without_price_are_dropped():
    content = html_table(
        ("(활)방어", "일본", "1미", "kg", "10", "1", "33,000", "15,000", "29,400"),
        ("(활)방어", "포항", "1미", "kg", "10", "1", "", "", ""),
    ).encode()
    assert len(noryangjin.parse_table(content)) == 1


@pytest.mark.parametrize(
    "value, expected",
    [("(활)방어", ("활", "방어")), ("(선) 잿방어", ("선", "잿방어")), ("킹크랩", ("", "킹크랩"))],
)
def test_split_name(value, expected):
    assert noryangjin.split_name(value) == expected
