import datetime
import json
import re

import pandas as pd
import pytest

import pipeline
from tests.conftest import FROZEN_TODAY

FIELDS = pipeline.ROW_FIELDS


def as_dicts(rows):
    return [dict(zip(FIELDS, r)) for r in rows]


BANGEO = pd.DataFrame(
    {
        "어종": ["(활)방어", "(활)방어", "(선)방어", "(선)방어", "(활)잿방어", "(활)우럭조개", "(활)우럭"],
        "산지": ["일본", "포항", "일본", "속초", "일본", "여수", "통영"],
        "규격": ["1미", "1미", "1미", "4미", "1미", "", "2미"],
        "포장": ["kg", "kg", "kg", "S/P", "kg", "kg", "kg"],
        "수량": [135.9, 506.6, 15.2, 25.0, 340.6, 12.0, 3.0],
        "중량": [1, 1, 1, 4, 1, 1, 1],
        "낙찰고가": [33000, 22000, 10000, 5000, 37000, 9000, 30000],
        "낙찰저가": [15000, 10000, 9000, 5000, 15000, 9000, 25000],
        "평균가": [29400, 14400, 9600, 5000, 30800, 9000, 27000],
    }
)


def test_table_rows_keep_every_species_with_official_names():
    rows = as_dicts(pipeline.table_rows("2026-09-29", BANGEO))
    assert len(rows) == len(BANGEO)
    by_species = {}
    for r in rows:
        by_species.setdefault(r["species"], []).append(r)
    # 상태 표기를 뗀 이름으로 정확히 나뉘어야 한다(잿방어·우럭조개가 방어·우럭에 섞이면 안 된다)
    assert sorted(by_species) == ["방어", "우럭", "우럭조개", "잿방어"]
    assert max(r["high"] for r in by_species["방어"] if r["pack"] == "kg") == 33000
    assert {r["state"] for r in by_species["방어"]} == {"활", "선"}


def test_box_lot_weight_is_kept():
    rows = as_dicts(pipeline.table_rows("2026-09-29", BANGEO))
    (box,) = [r for r in rows if r["pack"] == "S/P"]
    assert box["weight"] == 4.0 and box["qty"] * box["weight"] == 100.0


def test_missing_weight_defaults_to_one():
    table = pd.DataFrame({"어종": ["(활)방어"], "산지": ["일본"], "포장": ["kg"], "수량": [3.0], "낙찰고가": [1000]})
    (row,) = as_dicts(pipeline.table_rows("2026-09-29", table))
    assert row["weight"] == 1.0
    assert row["size"] == ""


def test_labels_and_table_use_official_names():
    officials = {official for _, official, _ in pipeline.TABLE_ROWS}
    assert {"감숭어", "줄돔", "전어"} <= officials
    assert pipeline.LABELS["왕게"] == "킹크랩"
    assert pipeline.priority_species()[:3] == ["넙치", "농어", "참돔"]
    assert "왕게" in pipeline.priority_species()


def test_species_order_puts_table_species_first_then_volume():
    rows = [
        ["2026-09-29", "전복", "", "활", "완도", "", "kg", 9000.0, 1.0, 1, 1, 1],
        ["2026-09-29", "개불", "", "활", "여수", "", "kg", 100.0, 1.0, 1, 1, 1],
        ["2026-09-29", "방어", "", "활", "포항", "", "kg", 1.0, 1.0, 1, 1, 1],
        ["2026-09-29", "넙치", "양식", "활", "제주도", "", "kg", 1.0, 1.0, 1, 1, 1],
    ]
    assert pipeline.species_order(rows) == ["넙치", "방어", "전복", "개불"]


@pytest.mark.parametrize(
    "species, origin, kind",
    [
        ("넙치", "제주도", "양식"),
        ("넙치", "완도", "자연산"),
        ("참돔", "일본", "양식"),
        ("농어", "서천", "자연산"),
        ("줄돔", "일본", "수입"),
        ("감성돔", "여수", "국산"),
        ("방어", "일본", ""),
    ],
)
def test_kind_of(species, origin, kind):
    assert pipeline.kind_of(species, origin) == kind


def write_daily(daily_dir, date, rows=2):
    daily_dir.mkdir(parents=True, exist_ok=True)
    names = ["(활)방어", "(활)넙치"] * rows
    pd.DataFrame(
        {"어종": names[:rows], "산지": ["일본"] * rows, "규격": ["1미"] * rows, "포장": ["kg"] * rows,
         "수량": [10.0] * rows, "중량": [1] * rows, "낙찰고가": [20000] * rows, "낙찰저가": [10000] * rows,
         "평균가": [15000] * rows}
    ).to_csv(daily_dir / f"{date}.csv", index=False)


def test_collect_live_skips_existing_past_dates_but_always_refetches_recent(daily_dir, monkeypatch):
    monkeypatch.setattr(pipeline, "BACKFILL_DAYS", 5)
    day = lambda n: FROZEN_TODAY - datetime.timedelta(days=n)  # noqa: E731
    for n in (3, 1, 0):
        write_daily(daily_dir, day(n))
    calls = []
    monkeypatch.setattr(pipeline, "fetch_live", calls.extend)

    pipeline.collect_live()

    assert calls == [day(4), day(2), day(1), day(0)]


def test_collect_live_default_fetches_today_and_yesterday(daily_dir, monkeypatch):
    monkeypatch.setattr(pipeline, "BACKFILL_DAYS", 0)
    calls = []
    monkeypatch.setattr(pipeline, "fetch_live", calls.extend)
    pipeline.collect_live()
    assert calls == [FROZEN_TODAY - datetime.timedelta(days=1), FROZEN_TODAY]


def test_fetch_live_requests_all_species_and_saves_by_date(daily_dir, monkeypatch):
    requested = []

    def fake_fetch_day(session, species, date):
        requested.append(species)
        return BANGEO if date == FROZEN_TODAY else pd.DataFrame()

    monkeypatch.setattr(pipeline.noryangjin, "new_session", lambda: None)
    monkeypatch.setattr(pipeline.noryangjin, "fetch_day", fake_fetch_day)
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    pipeline.fetch_live([FROZEN_TODAY - datetime.timedelta(days=1), FROZEN_TODAY])
    assert requested == ["", ""]  # 빈 검색어 = 전체 어종
    assert [p.stem for p in daily_dir.glob("*.csv")] == [FROZEN_TODAY.isoformat()]  # 휴장일은 파일을 만들지 않는다


def test_live_rows_keeps_only_window(daily_dir):
    oldest_kept = FROZEN_TODAY - datetime.timedelta(days=pipeline.WINDOW_DAYS - 1)
    write_daily(daily_dir, oldest_kept - datetime.timedelta(days=1))
    write_daily(daily_dir, oldest_kept)
    write_daily(daily_dir, FROZEN_TODAY, rows=2)
    dates = sorted({r[0] for r in pipeline.live_rows()})
    assert dates == [oldest_kept.isoformat(), FROZEN_TODAY.isoformat()]


def extract_payload(html):
    match = re.search(r"const DATA = (.*?);\n", html)
    assert match, "페이지에서 데이터 JSON 을 찾지 못했습니다"
    return json.loads(match.group(1))


def test_render_page_embeds_escaped_json():
    row = ["2026-09-29", "방어", "", "활", "</script><b>x", "1미", "kg", 1.0, 1.0, 1000.0, 500.0, 800.0]
    html = pipeline.render_page([row], "live")
    assert "/*__DATA__*/" not in html
    assert "</script><b>" not in html
    payload = extract_payload(html)
    assert payload["fields"] == FIELDS
    assert payload["rows"][0][4] == "</script><b>x"
    assert payload["table"] == [list(r) for r in pipeline.TABLE_ROWS]
    assert payload["species"] == ["방어"]
    assert payload["labels"] == pipeline.LABELS
    assert payload["sample"] is False


def test_sample_rows_have_all_fields():
    rows = pipeline.sample_rows()
    assert rows and all(len(r) == len(FIELDS) for r in rows)
