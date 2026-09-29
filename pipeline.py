"""노량진 수산시장 시세 수집 → 가성비 구간 계산 → 대화형 대시보드(index.html) 생성.

실행:
    python pipeline.py                 # 기본: 샘플 데이터 소스
    DATA_SOURCE=live python pipeline.py  # fetch_live() 구현 후 실제 수집

동작 순서:
    1. 소스에서 당일 경매 최고가를 가져와 data/prices.csv 에 누적(같은 날짜는 덮어씀)
    2. 최근 WINDOW_DAYS 일만 잘라 70%/80% 목표가를 계산
    3. Plotly 차트를 포함한 index.html 을 저장소 루트에 생성(GitHub Pages 가 그대로 서빙)
"""

import datetime
import html
import os
import random
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "prices.csv"
OUTPUT_FILE = ROOT / "index.html"

ITEM_NAME = "소방어(이백이)"
WINDOW_DAYS = 30
LOWER_RATIO = 0.7
UPPER_RATIO = 0.8
COLUMNS = ["Date", "Item", "High_Price", "Source"]

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


def fetch_live(date):
    """실제 시세 수집 자리.

    노량진수산물도매시장(https://www.susansijang.co.kr/) 경락 시세 페이지나
    인어교주해적단(https://tpirates.com/) 시세 페이지에서 해당 날짜의
    1kg당 경매 최고가(int)를 찾아 반환하도록 구현한다.
    값을 찾지 못하면 None 을 반환한다(휴장일 등).
    """
    raise NotImplementedError(
        "fetch_live() 가 아직 구현되지 않았습니다. 대상 사이트의 HTML/API 구조를 확인한 뒤 작성하세요."
    )


SOURCES = {"sample": fetch_sample, "live": fetch_live}


def load_history():
    if DATA_FILE.exists():
        return pd.read_csv(DATA_FILE, dtype={"Date": str})
    return pd.DataFrame(columns=COLUMNS)


def seed_sample_history(history, days):
    """샘플 모드에서 저장된 기록이 없으면 과거 days 일치를 채워 차트가 비지 않게 한다."""
    if not history.empty:
        return history
    end = today_kst()
    rows = [
        {
            "Date": (end - datetime.timedelta(days=i)).isoformat(),
            "Item": ITEM_NAME,
            "High_Price": fetch_sample(end - datetime.timedelta(days=i)),
            "Source": "sample",
        }
        for i in range(days - 1, 0, -1)
    ]
    return pd.DataFrame(rows, columns=COLUMNS)


def update_history(source):
    history = load_history()
    if source == "sample":
        history = seed_sample_history(history, WINDOW_DAYS)
    else:
        # 실제 소스로 전환하면 샘플 기록은 섞이지 않게 버린다.
        history = history[history["Source"] != "sample"]

    date = today_kst()
    price = SOURCES[source](date)
    if price is None:
        print(f"{date}: 시세 없음(휴장일 등). 기존 기록만 사용합니다.")
    else:
        row = pd.DataFrame(
            [{"Date": date.isoformat(), "Item": ITEM_NAME, "High_Price": int(price), "Source": source}]
        )
        history = pd.concat([history[history["Date"] != date.isoformat()], row], ignore_index=True)

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
        yaxis_title="단가 (원 / 1kg)",
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
  <footer>마지막 갱신: {updated} (KST) · 단가는 1kg 기준</footer>
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
    )


def main():
    source = os.environ.get("DATA_SOURCE", "sample")
    if source not in SOURCES:
        raise SystemExit(f"알 수 없는 DATA_SOURCE: {source} (가능: {', '.join(SOURCES)})")

    history = update_history(source)
    if history.empty:
        raise SystemExit("저장된 시세가 없어 대시보드를 만들 수 없습니다.")

    df = add_targets(history)
    OUTPUT_FILE.write_text(render_page(df, build_figure(df)), encoding="utf-8")
    print(f"완료: {OUTPUT_FILE.relative_to(ROOT)} 생성 ({len(df)}일치, 소스={source})")


if __name__ == "__main__":
    main()
