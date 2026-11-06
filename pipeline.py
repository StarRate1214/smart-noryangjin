"""노량진 수산시장 경락시세 수집 → 대화형 대시보드(index.html) 생성.

실행:
    python pipeline.py                       # 공식 홈페이지에서 오늘·어제 시세 수집(기본)
    BACKFILL_DAYS=30 python pipeline.py      # 원본이 없는 지난 30일도 채움
    BACKFILL_FROM=2025-10-01 BACKFILL_TO=2026-08-31 python pipeline.py   # 지정 기간의 빠진 날짜를 채움
    DATA_SOURCE=sample python pipeline.py    # 사이트 접속 없이 샘플 데이터로 화면만 확인

동작 순서:
    1. 날짜마다 어종명을 비워 요청해 그날 거래된 모든 어종의 경락 표를 받아
       data/daily/YYYY-MM-DD.csv 로 저장한다(하루 1회 요청)
    2. 최근 WINDOW_DAYS 일 원본 행을 index.html 에 JSON 으로 넣는다. 작년 같은 달과 직전 3개월 비교용으로는
       그 기간의 어종·상태별 일별 요약(평균가·고가·저가·수량·경락량)만 넣는다
    3. 어종 선택·검색, 상태·산지·규격 필터(포함/제외), 즐겨찾기, 날짜별 고가·평균가·저가와
       가성비 구간 계산은 페이지의 스크립트(templates/dashboard.html)가 브라우저에서 처리한다

환경 변수(GitHub Actions 에서는 저장소 Variables 로 지정):
    BACKFILL_DAYS  원본이 없는 과거 날짜를 며칠까지 채울지 (기본: 0)
    REFRESH_RECENT 1 이면 오늘·어제를 늘 다시 받음(기본). 0 이면 원본이 없거나 경매 집계 전에 받은 것만 다시 받음
    BACKFILL_FROM, BACKFILL_TO  이 기간(YYYY-MM-DD)의 빠진 날짜를 오래된 날부터 채움
    BACKFILL_LIMIT 한 번 실행에서 BACKFILL_FROM~TO 로 받을 최대 날짜 수 (기본: 60)
    DATA_SOURCE    live(기본) 또는 sample
"""

import calendar
import datetime
import json
import os
import random
import time
from pathlib import Path

import pandas as pd

import noryangjin

ROOT = Path(__file__).resolve().parent
DAILY_DIR = ROOT / "data" / "daily"
# 거래가 없었던 지난 날짜. 기간 백필이 휴장일을 매번 다시 요청하지 않게 기록한다.
NO_TRADE_FILE = ROOT / "data" / "no_trade.txt"
# 날짜별로 원본을 언제 받았는지(KST). 경매 집계가 끝나기 전에 받은 원본을 알아보고 다시 받는 데 쓴다.
FETCH_LOG = ROOT / "data" / "fetched.json"
# 그날 경매 결과가 다 올라왔다고 보는 시각(KST). 2026-11-05 02:30 에 받은 원본은 300행으로 하루치(540~570행)의
# 절반이었다. 이 시각 전에 받은 그날 원본은 '집계 전'으로 보고 다음 실행이 다시 받으며, 알림도 보내지 않는다.
# 첫 정기 수집 시각(05:00)에 맞춘다. 늦게 올라온 경락분은 10시·17시 수집이 다시 받아 채운다.
COMPLETE_HOUR = 5
DEBUG_DIR = ROOT / "debug"
TEMPLATE_FILE = ROOT / "templates" / "dashboard.html"
OUTPUT_FILE = ROOT / "index.html"

# 공식 표기와 흔히 쓰는 이름이 다른 어종. 2026-09 조사에서 빈 검색어로 받은 전체 어종 목록(172종)과
# 공개 입하 통계 수치를 대조해 정했다. 데이터는 공식 표기(왼쪽)로 다루고, 화면에는 오른쪽 이름을 함께 보인다.
LABELS = {"감숭어": "숭어", "줄돔": "돌돔", "전어": "황전어", "왕게": "킹크랩"}

# 원본에는 자연산/양식, 국산/수입 구분이 없어 산지로 나눈다(공식 표기 기준).
# 2026년 9월 18~22일 공개 입하 통계와 산지별 경락 수량을 대조해 정한 기준이다.
FOREIGN = ["일본", "중국", "대만", "러시아", "노르웨이", "미국", "캐나다", "베트남", "호주", "칠레"]
KIND_RULES = {
    "넙치": ("자연산", {"양식": ["제주도"]}),
    "참돔": ("자연산", {"양식": ["일본", "통영"]}),
    "농어": ("자연산", {"양식": ["중국"]}),
    "감성돔": ("국산", {"수입": FOREIGN}),
    "줄돔": ("국산", {"수입": FOREIGN}),
    # 활어 입하량 표가 방어를 국산·수입으로 나눠 적어 같은 기준으로 나눈다
    "방어": ("국산", {"수입": FOREIGN}),
}

# 주간 경락량 표의 행: (화면 이름, 공식 표기, 구분). 이 순서가 어종 목록의 앞쪽 순서도 된다.
TABLE_ROWS = [
    ("넙치", "넙치", "자연산"), ("넙치", "넙치", "양식"), ("농어", "농어", "자연산"), ("농어", "농어", "양식"),
    ("참돔", "참돔", "자연산"), ("참돔", "참돔", "양식"), ("숭어", "감숭어", ""), ("부시리", "부시리", ""),
    ("도다리", "도다리", ""), ("능성어", "능성어", ""), ("방어", "방어", ""), ("잿방어", "잿방어", ""),
    ("흑점줄전갱이", "흑점줄전갱이", ""), ("민어", "민어", ""), ("벤자리", "벤자리", ""),
    ("감성돔", "감성돔", "국산"), ("돌돔", "줄돔", "국산"), ("돌돔", "줄돔", "수입"), ("황전어", "전어", ""),
    ("참숭어", "참숭어", ""), ("우럭", "우럭", ""),
]
# 표에는 없지만 목록 앞쪽에 두는 어종
EXTRA_PRIORITY = ["왕게"]

BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS") or 0)
# 0 이면 오늘·어제도 원본이 이미 있을 때 다시 받지 않는다. 하루 중 재시도 실행이 같은 요청을 되풀이하지 않게 한다.
REFRESH_RECENT = os.environ.get("REFRESH_RECENT", "1") != "0"
BACKFILL_FROM = os.environ.get("BACKFILL_FROM") or ""
BACKFILL_TO = os.environ.get("BACKFILL_TO") or ""
BACKFILL_LIMIT = int(os.environ.get("BACKFILL_LIMIT") or 60)
# 기간 백필은 요청이 몰리므로 평소(1초)보다 길게 쉰다. 사이트 방화벽이 비정상 접근으로 보지 않게 한다.
BACKFILL_PAUSE = 3

# 가격 비교 기준: 최근 거래일과 같은 달의 작년 거래, 그리고 최근 거래일 직전 3개월 거래
REF_MONTHS = 3

WINDOW_DAYS = 30
# 가성비 구간: 평균가(수량 가중) + (고가 - 평균가) x 60~80%.
# 수산물은 너무 싸면 원물 상태가 나쁘므로 구간은 평균가보다 위, 고가 가까이에 둔다. 평균가는 그날 저가보다 낮을 수
# 없어 구간이 저가선 아래로 내려가지 않는다(고가 x 70~80% 는 활어 어종·날짜의 19%에서 저가 아래로 내려갔다).
LOWER_RATIO = 0.6
UPPER_RATIO = 0.8

KST = datetime.timezone(datetime.timedelta(hours=9))

# 페이지에 넣는 행의 열 순서
ROW_FIELDS = ["date", "species", "kind", "state", "origin", "size", "pack", "qty", "weight", "high", "low", "avg"]


def today_kst():
    return datetime.datetime.now(KST).date()


def recent_dates(days):
    today = today_kst()
    return [today - datetime.timedelta(days=i) for i in range(days - 1, -1, -1)]


def priority_species():
    order = []
    for _, official, _ in TABLE_ROWS:
        if official not in order:
            order.append(official)
    return order + [s for s in EXTRA_PRIORITY if s not in order]


# ---------------------------------------------------------------------------
# 1. 수집
# ---------------------------------------------------------------------------


def no_trade_dates():
    if not NO_TRADE_FILE.exists():
        return set()
    return {line.strip() for line in NO_TRADE_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}


def mark_no_trade(date):
    """이틀 넘게 지난 날짜만 휴장으로 기록한다. 오늘·어제는 경락분이 늦게 올라올 수 있다."""
    if date >= today_kst() - datetime.timedelta(days=1):
        return
    days = no_trade_dates() | {date.isoformat()}
    NO_TRADE_FILE.parent.mkdir(parents=True, exist_ok=True)
    NO_TRADE_FILE.write_text("".join(f"{d}\n" for d in sorted(days)), encoding="utf-8")


def fetch_log():
    if not FETCH_LOG.exists():
        return {}
    return json.loads(FETCH_LOG.read_text(encoding="utf-8"))


def record_fetch(date):
    log = fetch_log()
    log[date.isoformat()] = datetime.datetime.now(KST).strftime("%Y-%m-%dT%H:%M")
    keep = (today_kst() - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    log = {d: t for d, t in sorted(log.items()) if d >= keep}
    FETCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    FETCH_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def is_partial(date):
    """그날 경매 집계가 끝나기 전(그날 COMPLETE_HOUR 시 전)에 받은 원본인지.
    받은 시각 기록이 없는 지난 원본은 완전하다고 보고, 기록 없는 오늘 원본은 다시 받는다."""
    fetched = fetch_log().get(date.isoformat())
    if fetched is None:
        return date >= today_kst() and (DAILY_DIR / f"{date}.csv").exists()
    return fetched < f"{date.isoformat()}T{COMPLETE_HOUR:02d}:00"


def fetch_live(dates, pause=1):
    """날짜별로 전체 어종 경락 표를 받아 data/daily/YYYY-MM-DD.csv 로 저장한다."""
    if not dates:
        print("받을 날짜가 없습니다(원본이 모두 있음)")
        return
    try:
        session = noryangjin.new_session()
    except noryangjin.FetchError as exc:
        raise SystemExit(str(exc))
    for i, date in enumerate(dates):
        if i:
            time.sleep(pause)  # 서버 부하를 줄이기 위한 간격
        try:
            table = noryangjin.fetch_day(session, "", date)
        except noryangjin.FetchError as exc:
            if exc.content is not None:
                DEBUG_DIR.mkdir(exist_ok=True)
                (DEBUG_DIR / f"{date}.bin").write_bytes(exc.content)
            raise SystemExit(f"{date}: {exc}")

        if table.empty:
            print(f"{date}: 경락 기록 없음(휴장일 등)")
            mark_no_trade(date)
            continue
        DAILY_DIR.mkdir(parents=True, exist_ok=True)
        table.to_csv(DAILY_DIR / f"{date}.csv", index=False)
        record_fetch(date)
        names = {noryangjin.split_name(v)[1] for v in table.get("어종", [])}
        print(f"{date}: {len(table)}행, {len(names)}종")


def collect_live():
    # 오늘과 어제는 늦게 올라오는 경락분이 있을 수 있어 다시 받는다(REFRESH_RECENT). 재시도 실행(REFRESH_RECENT=0)은
    # 원본이 없거나 경매 집계 전에 받은 것만 다시 받는다. 그보다 앞선 날짜는 원본이 없는 것만 받는다.
    dates = recent_dates(max(BACKFILL_DAYS, 2))
    have = {p.stem for p in DAILY_DIR.glob("*.csv")} | no_trade_dates()
    recent = dates[-2:] if REFRESH_RECENT else [d for d in dates[-2:] if d.isoformat() not in have or is_partial(d)]
    fetch_live([d for d in dates[:-2] if d.isoformat() not in have] + recent)


def collect_range():
    """BACKFILL_FROM~TO 에서 원본도 휴장 기록도 없는 날짜를 오래된 날부터 BACKFILL_LIMIT 개 받는다."""
    start = datetime.date.fromisoformat(BACKFILL_FROM)
    end = datetime.date.fromisoformat(BACKFILL_TO) if BACKFILL_TO else today_kst()
    have = {p.stem for p in DAILY_DIR.glob("*.csv")} | no_trade_dates()
    days = (start + datetime.timedelta(days=i) for i in range((end - start).days + 1))
    missing = [d for d in days if d.isoformat() not in have]
    print(f"기간 백필 {start}~{end}: 빠진 날짜 {len(missing)}일 중 {min(len(missing), BACKFILL_LIMIT)}일을 받습니다")
    fetch_live(missing[:BACKFILL_LIMIT], pause=BACKFILL_PAUSE)


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


def table_rows(date, table):
    """원본 표의 모든 행을 페이지용 행으로 바꾼다. species 는 상태 표기를 뗀 공식 표기다."""
    name_col = next((c for c in noryangjin.NAME_COLUMNS if c in table.columns), None)
    high_col = next((c for c in noryangjin.HIGH_COLUMNS if c in table.columns), None)
    if name_col is None or high_col is None:
        return []
    pack_col = "포장" if "포장" in table.columns else "단위"
    rows = []
    for rec in table.to_dict("records"):
        state, species = noryangjin.split_name(rec[name_col])
        if not species:
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
    for path in sorted(DAILY_DIR.glob("*.csv")):
        if path.stem >= cutoff:
            rows += table_rows(path.stem, pd.read_csv(path))
    return rows


def species_order(rows):
    """표에 나오는 어종을 앞에 두고, 나머지는 최근 경락량이 많은 순으로 정렬한다."""
    volume = {}
    for r in rows:
        volume[r[1]] = volume.get(r[1], 0.0) + (r[7] or 0) * (r[8] or 1)
    first = [s for s in priority_species() if s in volume]
    rest = sorted((s for s in volume if s not in first), key=lambda s: (-volume[s], s))
    return first + rest


def add_months(date, months):
    """달을 더하고, 그 달에 없는 날(31일 등)은 말일로 맞춘다."""
    year, month = divmod(date.month - 1 + months, 12)
    year, month = date.year + year, month + 1
    return datetime.date(year, month, min(date.day, calendar.monthrange(year, month)[1]))


def reference_spans(latest):
    """비교 기준 계산에 필요한 날짜 범위. 직전 3개월과, 작년 같은 달 전체에 그 앞쪽 WINDOW_DAYS 일을 더한 범위다.
    앞쪽을 더하는 까닭은 차트의 '작년 같은 시기' 선이 표시 기간(최근 WINDOW_DAYS 일)의 1년 전 날짜를 쓰기 때문이다."""
    year_ago = add_months(latest, -12)
    month_start = year_ago.replace(day=1)
    month_end = year_ago.replace(day=calendar.monthrange(year_ago.year, year_ago.month)[1])
    return [
        (add_months(latest, -REF_MONTHS), latest),
        (min(month_start, year_ago - datetime.timedelta(days=WINDOW_DAYS)), month_end),
    ]


def in_spans(date, spans):
    return any(a.isoformat() <= date <= b.isoformat() for a, b in spans)


def reference_rows(latest):
    rows = []
    spans = reference_spans(latest)
    for path in sorted(DAILY_DIR.glob("*.csv")):
        if in_spans(path.stem, spans):
            rows += table_rows(path.stem, pd.read_csv(path))
    return rows


def summarize(rows):
    """원본 행을 '어종|상태'별 일별 요약으로 줄인다. 페이지의 시세 계산과 같은 기준이다:
    가격은 kg 단위 거래만, 평균가는 수량 가중, 경락량은 모든 거래의 수량 x 중량.
    결과: {"dates": [날짜...], "series": {"방어|활": [[날짜 번호, 평균가, 고가, 저가, 가중 수량, 경락량], ...]}}"""
    days = {}
    for date, species, _, state, _, _, pack, qty, weight, high, low, avg in rows:
        d = days.setdefault((f"{species}|{state}", date), {"pq": 0.0, "q": 0.0, "high": None, "low": None, "vol": 0.0})
        d["vol"] += (qty or 0) * (weight or 1)
        if (pack or "").lower() != "kg" or not high or high <= 0:
            continue
        d["high"] = high if d["high"] is None else max(d["high"], high)
        if low and low > 0:
            d["low"] = low if d["low"] is None else min(d["low"], low)
        if avg and avg > 0:
            q = qty if qty and qty > 0 else 1
            d["pq"] += avg * q
            d["q"] += q
    dates = sorted({date for _, date in days})
    index = {date: i for i, date in enumerate(dates)}
    series = {}
    for (key, date), d in sorted(days.items()):
        avg = round(d["pq"] / d["q"]) if d["q"] else None
        series.setdefault(key, []).append(
            [index[date], avg, d["high"] and round(d["high"]), d["low"] and round(d["low"]), round(d["q"], 1),
             round(d["vol"])]
        )
    return {"dates": dates, "series": series}


def sample_rows(dates=None):
    """사이트 접속 없이 화면을 확인하기 위한 결정적 샘플 행. dates 가 없으면 최근 WINDOW_DAYS 일."""
    origins = ["일본", "포항", "통영", "제주도", "완도"]
    sizes = ["1미", "2미", "3미"]
    rows = []
    for species_index, species in enumerate(priority_species()[:8]):
        for date in dates or recent_dates(WINDOW_DAYS):
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


def sample_reference(rows):
    latest = datetime.date.fromisoformat(max(r[0] for r in rows))
    spans = reference_spans(latest)
    dates = sorted({a + datetime.timedelta(days=i) for a, b in spans for i in range((b - a).days + 1)})
    return summarize(sample_rows(dates))


def alert_info(source):
    """알림 설정 탭에 넣을 값. 샘플 화면에서는 판정하지 않는다."""
    import alerts  # alerts 가 pipeline 을 불러 쓰므로 여기서 불러온다

    # alerts.json 이 잘못돼도 시세 수집과 페이지 생성은 막지 않는다. 오류는 알림 설정 탭에 보인다
    try:
        config = alerts.load_config()
        fired = alerts.run() if source == "live" else []
        error = ""
    except SystemExit as exc:
        config, fired, error = json.loads(json.dumps(alerts.DEFAULT_CONFIG)), [], str(exc)
    keep = ("date", "label", "metric", "text", "up", "id", "key")
    watch = [[alerts.watch_id(s, k), s, k] for s, k in alerts.WATCH_LIST]
    return {"config": config, "fired": [{k: a[k] for k in keep} for a in fired], "error": error,
            "date": fired[0]["date"] if fired else alerts.latest_date(), "watch": watch}


def render_page(rows, source, reference=None, alert_info=None):
    payload = {
        "fields": ROW_FIELDS,
        "rows": rows,
        "species": species_order(rows),
        "labels": LABELS,
        "table": TABLE_ROWS,
        "window": WINDOW_DAYS,
        "lower": LOWER_RATIO,
        "upper": UPPER_RATIO,
        "sample": source == "sample",
        "ref": reference or {"dates": [], "series": {}},
        "refMonths": REF_MONTHS,
        # 알림 설정 탭: 저장된 규칙, 최근 거래일에 걸린 알림, 규칙을 저장할 저장소
        "alerts": alert_info or {"config": {"auto": {"enabled": True}, "rules": []},
                                 "fired": [], "date": None, "watch": []},
        "repo": os.environ.get("GITHUB_REPOSITORY") or "StarRate1214/smart-noryangjin",
        "updated": datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE_FILE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)


def main():
    source = os.environ.get("DATA_SOURCE") or "live"
    if source not in ("sample", "live"):
        raise SystemExit(f"알 수 없는 DATA_SOURCE: {source} (가능: sample, live)")

    if source == "live":
        collect_range() if BACKFILL_FROM else collect_live()
        rows = live_rows()
    else:
        rows = sample_rows()
    if not rows:
        raise SystemExit("표시할 시세가 없어 대시보드를 만들 수 없습니다.")

    latest = datetime.date.fromisoformat(max(r[0] for r in rows))
    reference = summarize(reference_rows(latest)) if source == "live" else sample_reference(rows)
    OUTPUT_FILE.write_text(render_page(rows, source, reference, alert_info(source)), encoding="utf-8")
    print(f"완료: {OUTPUT_FILE.relative_to(ROOT)} 생성 ({len(rows)}행, {len({r[1] for r in rows})}종, 소스={source})")


if __name__ == "__main__":
    main()
