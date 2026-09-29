"""저장된 원본과 생성된 페이지 검사. PR CI 와 매일 수집(커밋 직전) 모두에서 돈다."""
import json
import re

import pandas as pd
import pytest

import noryangjin
import pipeline
from tests.conftest import REAL_RAW, ROOT

RAW_FILES = sorted(REAL_RAW.glob("*/*.csv"))
DATE_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def test_raw_files_exist():
    assert RAW_FILES, "data/raw 에 원본이 없습니다"


@pytest.mark.parametrize("path", RAW_FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_raw_file_shape(path):
    assert DATE_NAME.match(path.stem), f"파일 이름이 날짜 형식이 아닙니다: {path.name}"
    table = pd.read_csv(path)
    assert {"어종", "낙찰고가"} <= set(table.columns)
    names = {noryangjin.split_name(v)[1] for v in table["어종"]}
    # 별칭을 쓰기 전에는 표시 이름으로 검색해 받았으므로 둘 중 하나가 들어 있으면 된다
    wanted = {path.parent.name, pipeline.search_name(path.parent.name)}
    assert any(w in n for w in wanted for n in names), f"{path} 에 {wanted} 이(가) 들어간 어종이 없습니다"


def test_kind_rule_origins_still_appear():
    """산지 표기가 바뀌면 자연산/양식 구분이 조용히 0 이 되므로, 규칙의 국내 산지가 원본에 남아 있는지 본다."""
    seen = {}
    for path in RAW_FILES:
        species = path.parent.name
        if species in pipeline.KIND_RULES:
            seen.setdefault(species, set()).update(pd.read_csv(path)["산지"].dropna().astype(str))
    for species, (_, rules) in pipeline.KIND_RULES.items():
        if species not in seen:
            continue  # 아직 수집 기록이 없는 어종
        for label, origins in rules.items():
            if origins is pipeline.FOREIGN:
                continue
            missing = [o for o in origins if o not in seen[species]]
            assert not missing, f"{species} {label} 산지 {missing} 가 원본에 없습니다"


def test_index_html_has_data():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "/*__DATA__*/" not in html
    match = re.search(r"const DATA = (.*?);\n", html)
    assert match
    payload = json.loads(match.group(1))
    assert payload["fields"] == pipeline.ROW_FIELDS
    assert payload["rows"], "페이지에 시세 행이 없습니다"
    assert all(len(r) == len(payload["fields"]) for r in payload["rows"])
