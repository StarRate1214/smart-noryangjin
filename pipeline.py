"""노량진 수산시장 시세 수집 → 가성비 구간 계산 → 대화형 대시보드(index.html) 생성.

실행:
    python pipeline.py                       # 샘플 데이터(기본)
    DATA_SOURCE=live python pipeline.py      # 노량진수산물도매시장 공식 홈페이지에서 수집
    DATA_SOURCE=live BACKFILL_DAYS=30 python pipeline.py   # 비어 있는 과거 30일도 채움

동작 순서:
    1. 소스에서 경매 최고가를 가져와 data/prices.csv 에 누적(같은 날짜는 덮어씀)
       live 모드에서는 그날 받은 원본 표도 data/raw/YYYY-MM-DD.csv 로 남긴다.
    2. 최근 WINDOW_DAYS 일만 잘라 70%/80% 목표가를 계산
    3. Plotly 차트를 포함한 index.html 을 저장소 루트에 생성(GitHub Pages 가 그대로 서빙)

환경 변수(GitHub Actions 에서는 저장소 Variables 로 지정):
    ITEM_NAME      화면에 표시할 품목명 (기본: 방어)
    SEARCH_NAME    공식 홈페이지 어종 검색어 (기본: 방어)
    NAME_CONTAINS  응답 표의 어종명에 반드시 포함될 글자, 예: (활)
    ITEM_SIZES     쉼표로 구분한 규격 목록, 예: 소,중. 비우면 모든 규격 중 최고가
    BACKFILL_DAYS  live 모드에서 기록이 없는 과거 날짜를 며칠까지 채울지 (기본: 0)
"""

import datetime
import html
import os
import random
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

import noryangjin

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "prices.csv"
RAW_DIR = ROOT / "data" / "raw"
DEBUG_DIR = ROOT / "debug"
OUTPUT_FILE = ROOT / "index.html"


def _env_list(name):
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


ITEM_NAME = os.environ.get("ITEM_NAME") or "방어"
SEARCH_NAME = os.environ.get("SEARCH_NAME") or "방어"
NAME_CONTAINS = os.environ.get("NAME_CONTAINS") or None
ITEM_SIZES = _env_list("ITEM_SIZES")
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS") or 0)

WINDOW_DAYS = 30
LOWER_RATIO = 0.7
UPPER_RATIO = 0.8
COLUMNS = ["Date", "Item", "High_Price", "Unit", "Source"]

KST = datetime.timezone(datetime.timedelta(hours=9))


def today_kst():
    return datetime.datetime.now(KST).date()


# ---------------------------------------------------------------------------
# 1. 데이터 수집
# ---------------------------------------------------------------------------


def fetch_sample(date):
    """날짜를 시드로 쓰는 결정적 샘플 값. 같은 날짜는 항상 같은 가격을 돌려준다."""
    rng = random.Random(date.toordinal())
    return rng.randrange(15000, 21000, 100)


def sample_rows(dates):
    return [
        {"Date": d.isoformat(), "Item": ITEM_NAME, "High_Price": fetch_sample(d), "Unit": "kg", "Source": "sample"}
        for d in dates
    ]


def live_rows(dates):
    """공식 홈페이지에서 날짜별 최고 낙찰가를 가져온다. 휴장일(행 없음)은 건너뛴다."""
    session = noryangjin.new_session()
    rows = []
    for i, date in enumerate(dates):
        if i:
            time.sleep(1)  # 서버 부하를 줄이기 위한 간격
        try:
            table = noryangjin.fetch_day(session, SEARCH_NAME, date)
        except noryangjin.FetchError as exc:
            if exc.content is not None:
                DEBUG_DIR.mkdir(exist_ok=True)
                (DEBUG_DIR / f"{date}.bin").write_bytes(exc.content)
            raise SystemExit(f"{date}: {exc}")

        if table.empty:
            print(f"{date}: 경락 기록 없음(휴장일 등)")
            continue

        RAW_DIR.mkdir(parents=True, exist_ok=True)
        table.to_csv(RAW_DIR / f"{date}.csv", index=False)

        picked = noryangjin.select_rows(table, NAME_CONTAINS, ITEM_SIZES)
        price, unit = noryangjin.high_price(picked)
        sizes = sorted(table["규격"].astype(str).unique()) if "규격" in table.columns else []
        print(f"{date}: 전체 {len(table)}행, 선택 {len(picked)}행, 최고가 {price}, 단위 {unit}, 규격 {sizes}")
        if price is not None:
            rows.append({"Date": date.isoformat(), "Item": ITEM_NAME, "High_Price": price, "Unit": unit, "Source": "live"})
    return rows


def load_history():
    if DATA_FILE.exists():
        history = pd.read_csv(DATA_FILE, dtype={"Date": str, "Unit": str})
        return history.reindex(columns=COLUMNS)
    return pd.DataFrame(columns=COLUMNS)


def dates_to_collect(history, source):
    today = today_kst()
    if source == "sample":
        # 샘플 기록이 비어 있으면 차트가 비지 않도록 과거 WINDOW_DAYS 일을 함께 만든다.
        days = WINDOW_DAYS if history.empty else 1
    else:
        days = max(BACKFILL_DAYS, 1)
    known = set(history["Date"])
    dates = [today - datetime.timedelta(days=i) for i in range(days - 1, -1, -1)]
    # 오늘은 항상 다시 받고, 과거 날짜는 기록이 없는 것만 받는다.
    return [d for d in dates if d == today or d.isoformat() not in known]


def update_history(source):
    history = load_history()
    if source != "sample":
        # 실제 소스로 전환하면 샘플 기록은 섞이지 않게 버린다.
        history = history[history["Source"] != "sample"]

    dates = dates_to_collect(history, source)
    rows = sample_rows(dates) if source == "sample" else live_rows(dates)
    if rows:
        new = pd.DataFrame(rows, columns=COLUMNS)
        history = pd.concat([history[~history["Date"].isin(new["Date"])], new], ignore_index=True)

    history = history.sort_values("Date").reset_index(drop=True)
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    history.to_csv(DATA_FILE, index=False)
    return history


# ---------------------------------------------------------------------------
# 2. 데이터 가공
# ---------------------------------------------------------------------------


def add_targets(history):
    df = history.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    cutoff = df["Date"].max() - pd.Timedelta(days=WINDOW_DAYS - 1)
    df = df[df["Date"] >= cutoff].copy()
    df["High_Price"] = df["High_Price"].astype(int)
    df["Target_70"] = (df["High_Price"] * LOWER_RATIO).round().astype(int)
    df["Target_80"] = (df["High_Price"] * UPPER_RATIO).round().astype(int)
    return df


# ---------------------------------------------------------------------------
# 3. 시각화
# ---------------------------------------------------------------------------


def price_unit(df):
    units = df["Unit"].dropna().astype(str)
    return units.iloc[-1] if len(units) else "kg"


def build_figure(df):
    fig = go.Figure()
    hover = "%{y:,.0f}원"

    fig.add_trace(
        go.Scatter(
            x=df["Date"],
            y=df["High_Price"],
            mode="lines+markers",
            name="당일 경매 최고가 (100%)",
            line=dict(color="#e0443e", width=2.5),
            hovertemplate=hover,
        )
    )
    # 80% 선을 먼저 그리고, 다음 70% 트레이스의 fill="tonexty" 로 두 선 사이를 채운다.
    fig.add_trace(
        go.Scatter(
            x=df["Date"],
            y=df["Target_80"],
            mode="lines",
            name=f"가성비 상한선 ({UPPER_RATIO:.0%})",
            line=dict(color="#2e9e5b", width=1.5, dash="dash"),
            hovertemplate=hover,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["Date"],
            y=df["Target_70"],
            mode="lines",
            name=f"가성비 하한선 ({LOWER_RATIO:.0%})",
            line=dict(color="#1f7a45", width=1.5, dash="dash"),
            fill="tonexty",
            fillcolor="rgba(46, 158, 91, 0.15)",
            hovertemplate=hover,
        )
    )

    fig.update_layout(
        xaxis_title="경매 일자",
        yaxis_title=f"단가 (원 / {price_unit(df)})",
        hovermode="x unified",
        template="plotly_white",
        legend=dict(orientation="h", traceorder="normal", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        xaxis=dict(tickformat="%m/%d", hoverformat="%Y-%m-%d"),
        yaxis=dict(tickformat=",.0f"),
        margin=dict(l=60, r=20, t=40, b=50),
        font=dict(family="Pretendard, 'Noto Sans KR', sans-serif"),
    )
    return fig


PAGE_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>스마트 노량진</title>
<style>
  body {{ margin: 0; background: #fafafa; color: #1d1d1f;
         font-family: Pretendard, "Noto Sans KR", -apple-system, sans-serif; }}
  main {{ max-width: 960px; margin: 0 auto; padding: 24px 16px 40px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  .sub {{ color: #666; font-size: .9rem; margin: 0 0 20px; }}
  .notice {{ background: #fff4d6; border: 1px solid #f0d78c; border-radius: 8px;
             padding: 10px 14px; font-size: .9rem; margin-bottom: 16px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px; margin-bottom: 16px; }}
  .card {{ background: #fff; border: 1px solid #e5e5e5; border-radius: 10px; padding: 14px 16px; }}
  .card .label {{ color: #666; font-size: .82rem; }}
  .card .value {{ font-size: 1.35rem; font-weight: 700; margin-top: 4px;
                  font-variant-numeric: tabular-nums; }}
  .chart {{ background: #fff; border: 1px solid #e5e5e5; border-radius: 10px; padding: 8px; }}
  footer {{ color: #888; font-size: .8rem; margin-top: 16px; }}
</style>
</head>
<body>
<main>
  <h1>노량진 {item} 최근 {days}일 시세</h1>
  <p class="sub">당일 경매 최고가와 고가 대비 {lo:.0%}~{hi:.0%} 가성비 매수 구간</p>
  {notice}
  <div class="cards">
    <div class="card"><div class="label">최근 경매 최고가 ({last_date})</div><div class="value">{last_high:,}원</div></div>
    <div class="card"><div class="label">가성비 매수 구간</div><div class="value">{last_lo:,}~{last_hi:,}원</div></div>
    <div class="card"><div class="label">{days}일 최고가 평균</div><div class="value">{avg_high:,}원</div></div>
  </div>
  <div class="chart">{chart}</div>
  <footer>마지막 갱신: {updated} (KST) · 단가는 {unit} 기준 · 출처: {source}</footer>
</main>
</body>
</html>
"""


def render_page(df, fig):
    last = df.iloc[-1]
    is_sample = (df["Source"] == "sample").any()
    notice = (
        '<div class="notice">현재 표시된 값은 파이프라인 확인용 <b>샘플 데이터</b>입니다. '
        "실제 시세가 아닙니다.</div>"
        if is_sample
        else ""
    )
    chart = fig.to_html(full_html=False, include_plotlyjs="cdn", config={"displayModeBar": False, "responsive": True})
    return PAGE_TEMPLATE.format(
        item=html.escape(ITEM_NAME),
        days=WINDOW_DAYS,
        lo=LOWER_RATIO,
        hi=UPPER_RATIO,
        notice=notice,
        last_date=last["Date"].strftime("%m/%d"),
        last_high=int(last["High_Price"]),
        last_lo=int(last["Target_70"]),
        last_hi=int(last["Target_80"]),
        avg_high=int(round(df["High_Price"].mean())),
        chart=chart,
        updated=datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        unit=html.escape(price_unit(df)),
        source="샘플 데이터" if is_sample else '<a href="https://www.susansijang.co.kr/nsis/miw/ko/info/miw3130">노량진수산물도매시장 어종별 경락시세</a>',
    )


def main():
    source = os.environ.get("DATA_SOURCE") or "sample"
    if source not in ("sample", "live"):
        raise SystemExit(f"알 수 없는 DATA_SOURCE: {source} (가능: sample, live)")

    history = update_history(source)
    if history.empty:
        raise SystemExit("저장된 시세가 없어 대시보드를 만들 수 없습니다.")

    df = add_targets(history)
    OUTPUT_FILE.write_text(render_page(df, build_figure(df)), encoding="utf-8")
    print(f"완료: {OUTPUT_FILE.relative_to(ROOT)} 생성 ({len(df)}일치, 소스={source})")


if __name__ == "__main__":
    main()
