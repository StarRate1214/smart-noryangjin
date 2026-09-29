"""저장된 원본과 생성된 페이지 검사. PR CI 와 매일 수집(커밋 직전) 모두에서 돈다."""
import json
import re

import pandas as pd
import pytest

import noryangjin
import pipeline
from tests.conftest import ROOT

DAILY_FILES = sorted(pipeline.DAILY_DIR.glob("*.csv"))
DATE_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def test_daily_files_exist():
    assert DAILY_FILES, "data/daily 에 원본이 없습니다"


@pytest.mark.parametrize("path", DAILY_FILES, ids=lambda p: p.stem)
def test_daily_file_shape(path):
    assert DATE_NAME.match(path.stem), f"파일 이름이 날짜 형식이 아닙니다: {path.name}"
    table = pd.read_csv(path)
    assert {"어종", "산지", "낙찰고가"} <= set(table.columns)
    assert len(table) < noryangjin.PAGE_UNIT, "요청 한도에 닿아 잘렸을 수 있습니다"
    # 전체 어종 요청이므로 한 종류만 들어 있으면 검색어가 잘못 들어간 것이다
    names = {noryangjin.split_name(v)[1] for v in table["어종"]}
    assert len(names) > 1, f"{path.name} 에 어종이 {names} 하나뿐입니다"


def test_kind_rule_origins_still_appear():
    """산지 표기가 바뀌면 자연산/양식 구분이 조용히 0 이 되므로, 규칙의 국내 산지가 원본에 남아 있는지 본다."""
    seen = {}
    for path in DAILY_FILES:
        table = pd.read_csv(path)
        for name, origin in zip(table["어종"], table["산지"].astype(str)):
            species = noryangjin.split_name(name)[1]
            if species in pipeline.KIND_RULES:
                seen.setdefault(species, set()).add(origin)
    for species, (_, rules) in pipeline.KIND_RULES.items():
        if species not in seen:
            continue  # 아직 거래 기록이 없는 어종
        for label, origins in rules.items():
            if origins is pipeline.FOREIGN:
                continue
            missing = [o for o in origins if o not in seen[species]]
            assert not missing, f"{species} {label} 산지 {missing} 가 원본에 없습니다"


def test_table_species_appear_in_data():
    """주간 표의 공식 표기가 실제 원본에 있는지 본다(표기가 바뀌면 행이 계속 0 이 된다)."""
    names = set()
    for path in DAILY_FILES:
        names |= {noryangjin.split_name(v)[1] for v in pd.read_csv(path)["어종"]}
    missing = sorted({official for _, official, _ in pipeline.TABLE_ROWS} - names)
    assert not missing, f"원본에 없는 표 어종: {missing}"


def test_index_html_has_data():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "/*__DATA__*/" not in html
    match = re.search(r"const DATA = (.*?);\n", html)
    assert match
    payload = json.loads(match.group(1))
    assert payload["fields"] == pipeline.ROW_FIELDS
    assert payload["rows"], "페이지에 시세 행이 없습니다"
    assert all(len(r) == len(payload["fields"]) for r in payload["rows"])
    assert set(payload["species"]) == {r[1] for r in payload["rows"]}
