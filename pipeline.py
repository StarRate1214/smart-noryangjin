"""노량진 수산시장 경락시세 수집 → 대화형 대시보드(index.html) 생성.

실행:
    python pipeline.py                       # 공식 홈페이지에서 오늘·어제 시세 수집(기본)
    BACKFILL_DAYS=30 python pipeline.py      # 원본이 없는 지난 30일도 채움
    DATA_SOURCE=sample python pipeline.py    # 사이트 접속 없이 샘플 데이터로 화면만 확인

동작 순서:
    1. SPECIES 의 어종마다 공식 홈페이지 경락 표를 받아 data/raw/<어종>/YYYY-MM-DD.csv 로 저장
    2. 최근 WINDOW_DAYS 일 원본 행을 index.html 에 JSON 으로 넣는다
    3. 어종 선택, 상태·산지·규격 필터, 즐겨찾기, 날짜별 최고가와 70~80% 구간 계산은
       페이지의 스크립트(templates/dashboard.html)가 브라우저에서 처리한다

환경 변수(GitHub Actions 에서는 저장소 Variables 로 지정):
    SPECIES        쉼표로 구분한 어종 목록. 앞에 둔 어종이 기본 선택 (기본: DEFAULT_SPECIES)
    BACKFILL_DAYS  원본이 없는 과거 날짜를 며칠까지 채울지 (기본: 0)
    DATA_SOURCE    live(기본) 또는 sample
"""

import datetime
import json
import os
import random
import time
from pathlib import Path

import pandas as pd

import noryangjin

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
DEBUG_DIR = ROOT / "debug"
TEMPLATE_FILE = ROOT / "templates" / "dashboard.html"
OUTPUT_FILE = ROOT / "index.html"

# 이미지(주간 입하 표, 킹크랩 시세)에 나오는 어종 + 기존 수집 어종. 앞의 어종이 기본 선택
DEFAULT_SPECIES = [
    "넙치", "농어", "참돔", "숭어", "부시리", "도다리", "능성어", "방어", "잿방어",
    "흑점줄전갱이", "민어", "벤자리", "감성돔", "돌돔", "황전어", "참숭어", "우럭", "킹크랩",
]
SPECIES = [s.strip() for s in os.environ.get("SPECIES", "").split(",") if s.strip()] or DEFAULT_SPECIES

# 화면 표기와 공식 홈페이지 표기가 다른 어종. 2026-09 조사에서 빈 검색어로 받은 전체 어종 목록(172종)과
# 입하 통계 수치를 대조해 정했다. 표에는 왼쪽 이름으로 보이고, 검색·어종 일치는 오른쪽 이름으로 한다.
ALIASES = {"숭어": "감숭어", "돌돔": "줄돔", "황전어": "전어", "킹크랩": "왕게"}


def search_name(species):
    return ALIASES.get(species, species)


# 원본에는 자연산/양식, 국산/수입 구분이 없어 산지로 나눈다.
# 2026년 9월 18~22일 공개 입하 통계와 산지별 경락 수량을 대조해 정한 기준이다.
FOREIGN = ["일본", "중국", "대만", "러시아", "노르웨이", "미국", "캐나다", "베트남", "호주", "칠레"]
KIND_RULES = {
    "넙치": ("자연산", {"양식": ["제주도"]}),
    "참돔": ("자연산", {"양식": ["일본", "통영"]}),
    "농어": ("자연산", {"양식": ["중국"]}),
    "감성돔": ("국산", {"수입": FOREIGN}),
    "돌돔": ("국산", {"수입": FOREIGN}),
}

# 주간 경락량 표의 행 순서: (어종, 구분)
TABLE_ROWS = [
    ("넙치", "자연산"), ("넙치", "양식"), ("농어", "자연산"), ("농어", "양식"),
    ("참돔", "자연산"), ("참돔", "양식"), ("숭어", ""), ("부시리", ""), ("도다리", ""),
    ("능성어", ""), ("방어", ""), ("잿방어", ""), ("흑점줄전갱이", ""), ("민어", ""),
    ("벤자리", ""), ("감성돔", "국산"), ("돌돔", "국산"), ("돌돔", "수입"), ("황전어", ""),
    ("참숭어", ""), ("우럭", ""),
]
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS") or 0)

WINDOW_DAYS = 30
LOWER_RATIO = 0.7
UPPER_RATIO = 0.8

KST = datetime.timezone(datetime.timedelta(hours=9))

# 페이지에 넣는 행의 열 순서
ROW_FIELDS = ["date", "species", "kind", "state", "origin", "size", "pack", "qty", "weight", "high", "low", "avg"]


def today_kst():
    return datetime.datetime.now(KST).date()


def recent_dates(days):
    today = today_kst()
    return [today - datetime.timedelta(days=i) for i in range(days - 1, -1, -1)]


# ---------------------------------------------------------------------------
# 1. 수집
# ---------------------------------------------------------------------------


def fetch_live(dates_by_species):
    """어종·날짜별 원본 표를 받아 data/raw/<어종>/YYYY-MM-DD.csv 로 저장한다."""
    session = noryangjin.new_session()
    first = True
    for species, dates in dates_by_species.items():
        for date in dates:
            if not first:
                time.sleep(1)  # 서버 부하를 줄이기 위한 간격
            first = False
            try:
                table = noryangjin.fetch_day(session, search_name(species), date)
            except noryangjin.FetchError as exc:
                if exc.content is not None:
                    DEBUG_DIR.mkdir(exist_ok=True)
                    (DEBUG_DIR / f"{species}-{date}.bin").write_bytes(exc.content)
                raise SystemExit(f"{species} {date}: {exc}")

            if table.empty:
                print(f"{species} {date}: 경락 기록 없음(휴장일 등)")
                continue
            folder = RAW_DIR / species
            folder.mkdir(parents=True, exist_ok=True)
            table.to_csv(folder / f"{date}.csv", index=False)
            print(f"{species} {date}: {len(table)}행")


def collect_live():
    # 오늘과 어제는 늦게 올라오는 경락분이 있을 수 있어 항상 다시 받고,
    # 그보다 앞선 날짜는 원본이 없는 것만 받는다.
    dates = recent_dates(max(BACKFILL_DAYS, 2))
    plan = {}
    for species in SPECIES:
        have = {p.stem for p in (RAW_DIR / species).glob("*.csv")}
        plan[species] = [d for d in dates[:-2] if d.isoformat() not in have] + dates[-2:]
    fetch_live(plan)


# ---------------------------------------------------------------------------
# 2. 페이지용 행 만들기
# ---------------------------------------------------------------------------


def _num(value):
    return None if pd.isna(value) else float(value)


def _text(value):
    return "" if pd.isna(value) else str(value).strip()


def kind_of(species, origin):
    default, rules = KIND_RULES.get(species, ("", {}))
    return next((label for label, origins in rules.items() if origin in origins), default)


def table_rows(species, date, table):
    """원본 표를 페이지용 행으로 바꾼다.

    이름이 검색어와 정확히 같은 어종만 남긴다('방어' 검색에 섞여 오는 '잿방어' 제외).
    행의 species 에는 화면 표기(예: 킹크랩)를 넣는다.
    """
    name_col = next((c for c in noryangjin.NAME_COLUMNS if c in table.columns), None)
    high_col = next((c for c in noryangjin.HIGH_COLUMNS if c in table.columns), None)
    if name_col is None or high_col is None:
        return []
    pack_col = "포장" if "포장" in table.columns else "단위"
    rows = []
    for rec in table.to_dict("records"):
        state, name = noryangjin.split_name(rec[name_col])
        if name != search_name(species):
            continue
        origin = _text(rec.get("산지"))
        weight = _num(rec.get("중량"))
        rows.append(
            [
                date,
                species,
                kind_of(species, origin),
                state,
                origin,
                _text(rec.get("규격")),
                _text(rec.get(pack_col)),
                _num(rec.get("수량")),
                weight if weight else 1.0,
                _num(rec.get(high_col)),
                _num(rec.get("낙찰저가")),
                _num(rec.get("평균가")),
            ]
        )
    return rows


def live_rows():
    cutoff = recent_dates(WINDOW_DAYS)[0].isoformat()
    rows = []
    for species in SPECIES:
        for path in sorted((RAW_DIR / species).glob("*.csv")):
            if path.stem >= cutoff:
                rows += table_rows(species, path.stem, pd.read_csv(path))
    return rows


def sample_rows():
    """사이트 접속 없이 화면을 확인하기 위한 결정적 샘플 행."""
    origins = ["일본", "포항", "통영", "제주", "완도"]
    sizes = ["1미", "2미", "3미"]
    rows = []
    for species_index, species in enumerate(SPECIES[:6]):
        for date in recent_dates(WINDOW_DAYS):
            if date.weekday() == 6:
                continue  # 일요일 휴장
            rng = random.Random(date.toordinal() * 31 + species_index)
            base = 12000 + species_index * 4000
            for origin in rng.sample(origins, 3):
                for state in ("활", "선"):
                    high = base + rng.randrange(0, 12000, 500) - (6000 if state == "선" else 0)
                    rows.append(
                        [date.isoformat(), species, kind_of(species, origin), state, origin, rng.choice(sizes),
                         "kg", round(rng.uniform(5, 300), 1), 1.0, high, high * 0.5, high * 0.8]
                    )
    return rows


# ---------------------------------------------------------------------------
# 3. 페이지 생성
# ---------------------------------------------------------------------------


def render_page(rows, source):
    payload = {
        "fields": ROW_FIELDS,
        "rows": rows,
        "species": SPECIES,
        "aliases": ALIASES,
        "table": TABLE_ROWS,
        "window": WINDOW_DAYS,
        "lower": LOWER_RATIO,
        "upper": UPPER_RATIO,
        "sample": source == "sample",
        "updated": datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE_FILE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)


def main():
    source = os.environ.get("DATA_SOURCE") or "live"
    if source not in ("sample", "live"):
        raise SystemExit(f"알 수 없는 DATA_SOURCE: {source} (가능: sample, live)")

    if source == "live":
        collect_live()
        rows = live_rows()
    else:
        rows = sample_rows()
    if not rows:
        raise SystemExit("표시할 시세가 없어 대시보드를 만들 수 없습니다.")

    OUTPUT_FILE.write_text(render_page(rows, source), encoding="utf-8")
    found = sorted({r[1] for r in rows}, key=SPECIES.index)
    print(f"완료: {OUTPUT_FILE.relative_to(ROOT)} 생성 ({len(rows)}행, 어종 {found}, 소스={source})")


if __name__ == "__main__":
    main()
