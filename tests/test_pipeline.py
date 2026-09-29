import datetime
import json
import re

import pandas as pd
import pytest

import pipeline
from tests.conftest import FROZEN_TODAY, REAL_RAW

FIELDS = pipeline.ROW_FIELDS


def as_dicts(rows):
    return [dict(zip(FIELDS, r)) for r in rows]


def real_table(species, date):
    return pd.read_csv(REAL_RAW / species / f"{date}.csv")


def test_table_rows_excludes_similar_names_in_real_bangeo_data():
    rows = as_dicts(pipeline.table_rows("방어", "2026-09-29", real_table("방어", "2026-09-29")))
    assert len(rows) == 5
    assert {r["species"] for r in rows} == {"방어"}
    # 같은 파일의 (활)잿방어 37,000원이 섞이면 안 된다
    assert max(r["high"] for r in rows if r["pack"] == "kg") == 33000


def test_box_lot_weight_is_kept():
    rows = as_dicts(pipeline.table_rows("방어", "2026-09-29", real_table("방어", "2026-09-29")))
    box = [r for r in rows if r["pack"] == "S/P"]
    assert box and box[0]["weight"] == 4.0
    assert box[0]["qty"] * box[0]["weight"] == 100.0


@pytest.mark.parametrize("species, intruder", [("우럭", "우럭조개"), ("넙치", "찰넙치")])
def test_table_rows_excludes_intruders_in_real_data(species, intruder):
    checked = 0
    for path in sorted((REAL_RAW / species).glob("*.csv")):
        table = pd.read_csv(path)
        if not table["어종"].str.endswith(intruder).any():
            continue
        checked += 1
        rows = as_dicts(pipeline.table_rows(species, path.stem, table))
        assert {r["species"] for r in rows} <= {species}
        expected = table["어종"].map(lambda v: pipeline.noryangjin.split_name(v)[1] == species).sum()
        assert len(rows) == expected
    assert checked, f"{species} 원본에 {intruder} 행이 있는 파일이 없어 검사하지 못했습니다"


def test_alias_matches_official_name_and_keeps_display_name():
    table = pd.DataFrame({"어종": ["(활)왕게", "(활)대게"], "산지": ["러시아", "러시아"], "포장": ["kg", "kg"],
                          "수량": [5.0, 5.0], "낙찰고가": [113000, 50000]})
    rows = as_dicts(pipeline.table_rows("킹크랩", "2026-09-29", table))
    assert [(r["species"], r["high"]) for r in rows] == [("킹크랩", 113000)]
    assert pipeline.search_name("킹크랩") == "왕게"
    assert pipeline.search_name("방어") == "방어"


def test_missing_weight_defaults_to_one():
    table = pd.DataFrame({"어종": ["(활)방어"], "산지": ["일본"], "포장": ["kg"], "수량": [3.0], "낙찰고가": [1000]})
    (row,) = as_dicts(pipeline.table_rows("방어", "2026-09-29", table))
    assert row["weight"] == 1.0
    assert row["size"] == ""


@pytest.mark.parametrize(
    "species, origin, kind",
    [
        ("넙치", "제주도", "양식"),
        ("넙치", "완도", "자연산"),
        ("참돔", "일본", "양식"),
        ("농어", "서천", "자연산"),
        ("돌돔", "일본", "수입"),
        ("감성돔", "여수", "국산"),
        ("방어", "일본", ""),
    ],
)
def test_kind_of(species, origin, kind):
    assert pipeline.kind_of(species, origin) == kind


def write_raw(raw_dir, species, date, rows=1):
    folder = raw_dir / species
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"어종": [f"(활){species}"] * rows, "산지": ["일본"] * rows, "규격": ["1미"] * rows, "포장": ["kg"] * rows,
         "수량": [10.0] * rows, "중량": [1] * rows, "낙찰고가": [20000] * rows, "낙찰저가": [10000] * rows,
         "평균가": [15000] * rows}
    ).to_csv(folder / f"{date}.csv", index=False)


def test_collect_live_skips_existing_past_dates_but_always_refetches_recent(raw_dir, monkeypatch):
    monkeypatch.setattr(pipeline, "SPECIES", ["방어", "넙치"])
    monkeypatch.setattr(pipeline, "BACKFILL_DAYS", 5)
    day = lambda n: FROZEN_TODAY - datetime.timedelta(days=n)  # noqa: E731
    write_raw(raw_dir, "방어", day(3))
    write_raw(raw_dir, "방어", day(1))
    write_raw(raw_dir, "방어", day(0))
    calls = {}
    monkeypatch.setattr(pipeline, "fetch_live", lambda plan: calls.update(plan))

    pipeline.collect_live()

    assert calls["방어"] == [day(4), day(2), day(1), day(0)]
    assert calls["넙치"] == [day(4), day(3), day(2), day(1), day(0)]


def test_collect_live_default_fetches_today_and_yesterday(raw_dir, monkeypatch):
    monkeypatch.setattr(pipeline, "SPECIES", ["방어"])
    monkeypatch.setattr(pipeline, "BACKFILL_DAYS", 0)
    calls = {}
    monkeypatch.setattr(pipeline, "fetch_live", lambda plan: calls.update(plan))
    pipeline.collect_live()
    assert calls == {"방어": [FROZEN_TODAY - datetime.timedelta(days=1), FROZEN_TODAY]}


def test_live_rows_keeps_only_window(raw_dir, monkeypatch):
    monkeypatch.setattr(pipeline, "SPECIES", ["방어"])
    oldest_kept = FROZEN_TODAY - datetime.timedelta(days=pipeline.WINDOW_DAYS - 1)
    write_raw(raw_dir, "방어", oldest_kept - datetime.timedelta(days=1))
    write_raw(raw_dir, "방어", oldest_kept)
    write_raw(raw_dir, "방어", FROZEN_TODAY, rows=2)
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
    assert payload["sample"] is False


def test_sample_rows_have_all_fields():
    rows = pipeline.sample_rows()
    assert rows and all(len(r) == len(FIELDS) for r in rows)
