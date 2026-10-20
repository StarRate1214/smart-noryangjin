"""생성된 대시보드를 실제 브라우저로 여는 스모크 테스트.

외부 네트워크 없이 돌도록 cdn.plot.ly 요청은 plotly 파이썬 패키지에 든 plotly.js 로 돌려준다.
"""
import functools
import http.server
import os
import re
import threading
from pathlib import Path

import pytest

import pipeline
from tests.conftest import ROOT

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api")
plotly = pytest.importorskip("plotly")

PLOTLY_JS = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
ARTIFACTS = ROOT / "test-artifacts"


def template_plotly_version():
    html = pipeline.TEMPLATE_FILE.read_text(encoding="utf-8")
    return re.search(r"cdn\.plot\.ly/plotly-([\d.]+)\.min\.js", html).group(1)


def test_bundled_plotly_matches_template():
    head = PLOTLY_JS.read_text(encoding="utf-8")[:200]
    assert f"plotly.js v{template_plotly_version()}" in head, (
        "requirements-dev.txt 의 plotly 가 템플릿의 plotly.js 버전과 다릅니다"
    )


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        # 로컬에서 Playwright 가 받은 브라우저가 없으면 CHROMIUM_PATH 로 실행 파일을 지정할 수 있다
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        yield b
        b.close()


def build_rows(kind):
    return pipeline.sample_rows() if kind == "sample" else pipeline.live_rows()


@pytest.fixture(params=["sample", "real"])
def site(request, tmp_path, frozen_today):
    rows = build_rows(request.param)
    (tmp_path / "index.html").write_text(pipeline.render_page(rows, request.param), encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{server.server_port}/index.html", "rows": rows, "kind": request.param}
    server.shutdown()


@pytest.fixture
def page(request, browser, site):
    context = browser.new_context(viewport={"width": 1100, "height": 900}, locale="ko-KR", timezone_id="Asia/Seoul")
    js = PLOTLY_JS.read_bytes()
    context.route("https://cdn.plot.ly/**", lambda route: route.fulfill(body=js, content_type="application/javascript"))
    pg = context.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
    pg.goto(site["url"])
    pg.wait_for_selector("#weekTable tbody tr", state="attached")
    pg.errors = errors
    yield pg
    report = getattr(request.node, "rep_call", None)
    if report is not None and report.failed:
        ARTIFACTS.mkdir(exist_ok=True)
        pg.screenshot(path=str(ARTIFACTS / f"{request.node.name}.png"), full_page=True)
    context.close()
    assert not errors, f"브라우저 오류: {errors}"


def number(text):
    return float(text.replace(",", "").replace("−", "-").replace("+", "") or 0)


def expected_week_totals(rows):
    """페이지 스크립트와 같은 방식으로 마지막 5영업일의 열 합계를 계산한다."""
    every = [dict(zip(pipeline.ROW_FIELDS, r)) for r in rows]
    live = [r for r in every if r["state"] == "활"]
    # 영업일은 상태와 관계없이 거래가 있었던 날이다
    days = sorted({r["date"] for r in every})[-5:]
    wanted = {(official, kind) for _, official, kind in pipeline.TABLE_ROWS}
    kinds_by_species = {}
    for s, k in wanted:
        kinds_by_species.setdefault(s, set()).add(k)
    totals = []
    for d in days:
        total = 0.0
        for r in live:
            if r["date"] != d or r["species"] not in kinds_by_species:
                continue
            kinds = kinds_by_species[r["species"]]
            # 구분 없는 행("")은 모든 구분을 더하고, 구분 행은 해당 구분만 더한다
            counted = ("" in kinds) + (r["kind"] in kinds and r["kind"] != "")
            total += r["qty"] * r["weight"] * counted
        totals.append(total)
    return totals


def test_weekly_table(page, site):
    rows = page.locator("#weekTable tbody tr")
    assert rows.count() == len(pipeline.TABLE_ROWS)
    cells = page.locator("#weekTable tbody td:not(:first-child)").all_inner_texts()
    assert any(number(c) > 0 for c in cells if re.fullmatch(r"[\d,]+", c)), "주간 표가 모두 0 입니다"
    foot = page.locator("#weekTable tfoot td").all_inner_texts()
    expected = expected_week_totals(site["rows"])
    shown = [number(t) for t in foot[1 : 1 + len(expected)]]
    assert shown == pytest.approx(expected, abs=1.0)


def test_row_click_opens_species_chart(page, site):
    official = "줄돔" if site["kind"] == "real" else "넙치"
    row = page.locator(f'#weekTable tbody tr[data-species="{official}"]').first
    row.click()
    assert page.locator("#tab-price").get_attribute("aria-selected") == "true"
    if site["kind"] == "real":
        # 공식 표기(줄돔)로 선택되고, 화면에는 흔히 쓰는 이름(돌돔)이 함께 보여야 한다
        assert page.locator('#species .chip[aria-pressed="true"]').get_attribute("data-species") == "줄돔"
        assert "줄돔(돌돔)" in page.inner_text("#chartTitle")


def test_favorite_moves_species_first_after_reload(page):
    page.click("#tab-price")
    last = page.locator("#species .chip.species").last
    name = last.get_attribute("data-species")
    last.locator(".star").click()
    page.reload()
    page.wait_for_selector("#weekTable tbody tr", state="attached")
    page.click("#tab-price")
    first = page.locator("#species .chip.species").first
    assert "★" in first.inner_text() and first.get_attribute("data-species") == name


def test_origin_filter_updates_title(page):
    page.click("#tab-price")
    chip = page.locator("#origins .chip").nth(1)
    origin = chip.inner_text().split("\n")[0].strip()
    chip.click()
    assert origin in page.inner_text("#chartTitle")
    assert page.locator("#plot .main-svg").count() > 0


def test_no_horizontal_scroll_on_phone(page):
    page.set_viewport_size({"width": 390, "height": 844})
    for tab in ("#tab-week", "#tab-price"):
        page.click(tab)
        assert page.evaluate("document.documentElement.scrollWidth") <= 390


def trace_field(page, field):
    """차트의 각 트레이스 이름(상한선은 이름이 없어 '상한')별로 field 값을 돌려준다."""
    script = "f => Object.fromEntries(document.getElementById('plot').data.map(t => [t.name || '상한', t[f]]))"
    return page.evaluate(script, field)


def trace_visibility(page):
    return trace_field(page, "visible")


def test_chart_draws_high_avg_low_and_band(page):
    page.click("#tab-price")
    names = [n for n in trace_visibility(page)]
    for expected in ("고가", "평균가", "저가", "경락량"):
        assert expected in names
    assert any(n.startswith("가성비 구간") for n in names)
    ys = trace_field(page, "y")
    for high, avg, low in zip(ys["고가"], ys["평균가"], ys["저가"]):
        if None not in (high, avg, low):
            assert low <= avg <= high


def test_series_checkboxes_hide_traces_and_persist(page):
    page.click("#tab-price")
    page.uncheck('#series input[data-series="low"]')
    page.uncheck('#series input[data-series="volume"]')
    shown = trace_visibility(page)
    assert shown["저가"] is False and shown["경락량"] is False
    assert shown["고가"] is True and shown["평균가"] is True
    page.reload()
    page.wait_for_selector("#weekTable tbody tr", state="attached")
    page.click("#tab-price")
    assert not page.is_checked('#series input[data-series="low"]')
    assert trace_visibility(page)["저가"] is False


def test_origin_chip_cycles_include_exclude_clear(page):
    page.click("#tab-price")
    chip = page.locator("#origins .chip").nth(1)
    origin = chip.inner_text().split("\n")[0].strip()
    target = page.locator("#origins .chip", has_text=origin).first

    target.click()  # 포함
    assert target.get_attribute("data-state") == "in"
    assert page.locator("#origins .chip").first.get_attribute("aria-pressed") == "false"

    target.click()  # 제외
    target = page.locator("#origins .chip", has_text=origin).first
    assert target.get_attribute("data-state") == "out"
    assert f"({origin} 제외)" in page.inner_text("#chartTitle")
    # 제외만 있으면 '전체'는 여전히 선택된 상태다
    assert page.locator("#origins .chip").first.get_attribute("aria-pressed") == "true"

    target.click()  # 해제
    target = page.locator("#origins .chip", has_text=origin).first
    assert target.get_attribute("data-state") is None
    assert "제외" not in page.inner_text("#chartTitle")


def test_excluded_origin_is_left_out_of_chart(page):
    page.click("#tab-price")
    page.locator("#ranges .chip", has_text="30일").click()
    before = sum(v or 0 for v in trace_field(page, "y")["경락량"])
    page.locator("#origins .chip").nth(1).click()
    page.locator("#origins .chip").nth(1).click()
    after = sum(v or 0 for v in trace_field(page, "y")["경락량"])
    assert after < before


def test_species_search_filters_chips_and_enter_selects(page, site):
    page.click("#tab-price")
    query = "킹크랩" if site["kind"] == "real" else "방어"
    expected = "왕게" if site["kind"] == "real" else "방어"
    page.fill("#speciesSearch", query)
    chips = page.locator("#species .chip.species")
    assert chips.count() >= 1
    assert expected in [chips.nth(i).get_attribute("data-species") for i in range(chips.count())]
    page.press("#speciesSearch", "Enter")
    assert page.locator('#species .chip[aria-pressed="true"]').get_attribute("data-species") == expected
    page.fill("#speciesSearch", "없는어종명")
    assert page.locator("#species .chip.species").count() == 0
    assert "맞는 어종이 없습니다" in page.inner_text("#species")


def test_more_button_reveals_all_species(page, site):
    page.click("#tab-price")
    payload_species = page.evaluate("DATA.species.length")
    more = page.locator("#species .chip.more")
    if payload_species <= 21:
        assert more.count() == 0
        return
    before = page.locator("#species .chip.species").count()
    more.click()
    assert page.locator("#species .chip.species").count() == payload_species > before


def test_price_lines_use_stock_colors(page):
    page.click("#tab-price")
    colors = page.evaluate(
        "Object.fromEntries(document.getElementById('plot').data.map(t => [t.name || '상한', t.line && t.line.color]))"
    )
    assert colors["고가"] == "#E03131"
    assert colors["저가"] == "#1C7ED6"
    assert colors["평균가"] == "#F08C00"


def hover_items(page):
    drag = page.locator("#plot .nsewdrag").first
    drag.scroll_into_view_if_needed()  # 필터가 길면 차트가 화면 아래에 있다
    box = drag.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
    page.wait_for_selector("#plot .hoverlayer .legend text", state="attached")
    return page.evaluate(
        "Array.from(document.querySelectorAll('#plot .hoverlayer .legend text')).map(t => t.textContent)"
    )


def assert_order(items, labels):
    order = [next(i for i, item in enumerate(items) if item.startswith(label)) for label in labels]
    assert order == sorted(order), f"호버 순서가 다릅니다: {items}"


def test_hover_lists_high_band_avg_low_in_order(page):
    page.click("#tab-price")
    page.locator("#ranges .chip", has_text="30일").click()
    assert_order(hover_items(page), ["고가", "상한 80%", "하한 70%", "평균가", "저가"])


def expected_zone(rows, species):
    """페이지와 같은 규칙: 고가 x 70~80%, 고가의 70%가 저가보다 낮으면 폭(고가의 10%)을 유지한 채 저가선에서 시작."""
    by_date = {}
    for r in (dict(zip(pipeline.ROW_FIELDS, r)) for r in rows):
        if r["species"] == species and r["state"] == "활" and r["pack"].lower() == "kg" and (r["high"] or 0) > 0:
            by_date.setdefault(r["date"], []).append(r)
    out = {}
    for date, group in by_date.items():
        high = max(r["high"] for r in group)
        lows = [r["low"] for r in group if (r["low"] or 0) > 0]
        low = min(lows) if lows else 0
        lower = max(high * 0.7, low)
        out[date] = (round(lower), round(min(high, lower + high * 0.1)), low > high * 0.7)
    return out


def test_zone_is_70_80_of_high_shifted_above_low(page, site):
    page.click("#tab-price")
    page.locator("#ranges .chip", has_text="30일").click()
    if site["kind"] == "real":  # 고가·저가 차이가 작아 70%가 저가보다 낮은 날이 많은 어종
        page.fill("#speciesSearch", "흰다리새우")
        page.press("#speciesSearch", "Enter")
    species = page.evaluate("view.species")
    expected = expected_zone(site["rows"], species)
    ys = trace_field(page, "y")
    xs = page.evaluate("lastDays.map(d => d.date)")
    lower = dict(zip(xs, ys["가성비 구간 (고가 대비 70~80%)"]))
    upper = dict(zip(xs, ys["상한 80%"]))
    checked = shifted = 0
    for date, (lo, hi, was_shifted) in expected.items():
        if lower.get(date) is None:
            continue
        assert abs(lower[date] - lo) <= 1 and abs(upper[date] - hi) <= 1, (date, lower[date], upper[date], lo, hi)
        checked += 1
        shifted += was_shifted
    assert checked > 0
    if site["kind"] == "real":
        assert shifted > 0, "흰다리새우에서 저가선 때문에 올린 날을 확인하지 못했습니다"


def test_zoom_buttons_narrow_range_and_rescale(page):
    page.click("#tab-price")
    page.locator("#ranges .chip", has_text="30일").click()
    total = page.evaluate("lastDays.length")
    assert page.is_disabled("#zoomOut") and page.is_disabled("#zoomReset")
    page.click("#zoomIn")
    xr = page.evaluate("view.xr")
    assert xr and xr[1] - xr[0] + 1 < total
    layout = page.evaluate("document.getElementById('plot').layout")
    assert layout["xaxis"]["range"] == [xr[0] - 0.5, xr[1] + 0.5]
    # 세로축은 보이는 구간의 값에 맞춰 다시 잡힌다
    lo, hi = layout["yaxis"]["range"]
    visible_high = [v for v in trace_field(page, "y")["고가"][xr[0]:xr[1] + 1] if v is not None]
    assert lo <= min(visible_high) and hi >= max(visible_high)
    page.click("#panLeft")
    assert page.evaluate("view.xr")[0] <= xr[0]
    page.click("#zoomReset")
    assert page.evaluate("view.xr") is None
    assert "/" + str(total) + "일" in page.inner_text("#zoomInfo")


def test_week_table_modes_add_and_remove_species(page):
    page.select_option("#weekMode", "top30")
    rows = page.locator("#weekTable tbody tr")
    assert 0 < rows.count() <= 30
    sums = [number(t) for t in page.locator("#weekTable tbody td.sum").all_inner_texts()]
    assert sums == sorted(sums, reverse=True)  # 경락량 순서
    page.select_option("#weekMode", "base")
    base = page.locator("#weekTable tbody tr").count()
    assert base == len(pipeline.TABLE_ROWS)

    candidates = page.evaluate("DATA.species.filter(s => !DATA.table.some(r => r[1] === s))")
    if candidates:  # 샘플 데이터에는 기본 표에 없는 어종이 없다
        add_and_remove(page, candidates[0], base)

    page.fill("#weekAdd", "없는어종명")
    page.click("#weekAddBtn")
    assert "찾지 못했습니다" in page.inner_text("#weekAddMsg")


def add_and_remove(page, target, base):
    page.fill("#weekAdd", target)
    page.click("#weekAddBtn")
    assert page.locator("#weekTable tbody tr").count() == base + 1
    assert page.locator(f'#weekTable tbody tr[data-species="{target}"] .remove').count() == 1
    page.reload()
    page.wait_for_selector("#weekTable tbody tr", state="attached")
    page.click("#tab-week")
    assert page.locator(f'#weekTable tbody tr[data-species="{target}"]').count() == 1  # 새로고침 뒤에도 유지
    page.click(f'#weekTable tbody tr[data-species="{target}"] .remove')
    assert page.locator("#weekTable tbody tr").count() == base


def test_week_table_state_switch(page):
    page.locator("#weekStates .chip", has_text="선어").click()
    assert "선어" in page.inner_text("#weekTitle")
    page.locator("#weekStates .chip", has_text="전체").click()
    assert page.inner_text("#weekTitle") == "노량진 전체 경락량"


def test_band_never_drops_below_low_line(page, site):
    """고가·저가 차이가 작은 어종(예: 흰다리새우)에서 고가 x 70~80% 가 저가선 아래로 내려가던 문제를 막는다."""
    page.click("#tab-price")
    page.locator("#ranges .chip", has_text="30일").click()
    species = "흰다리새우" if site["kind"] == "real" else page.evaluate("view.species")
    page.fill("#speciesSearch", species)
    page.press("#speciesSearch", "Enter")
    assert page.evaluate("view.species") == species
    ys = trace_field(page, "y")
    lower_name = next(n for n in ys if n.startswith("가성비 구간"))
    checked = 0
    for low, lower in zip(ys["저가"], ys[lower_name]):
        if low is not None and lower is not None:
            assert lower >= low - 1, (low, lower)
            checked += 1
    assert checked > 0
