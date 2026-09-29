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
    live = [dict(zip(pipeline.ROW_FIELDS, r)) for r in rows]
    live = [r for r in live if r["state"] == "활"]
    days = sorted({r["date"] for r in live})[-5:]
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


def test_hover_lists_high_band_avg_low_in_order(page):
    page.click("#tab-price")
    page.locator("#ranges .chip", has_text="30일").click()
    drag = page.locator("#plot .nsewdrag").first
    drag.scroll_into_view_if_needed()  # 필터가 길면 차트가 화면 아래에 있다
    box = drag.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
    page.wait_for_selector("#plot .hoverlayer .legend text", state="attached")
    items = page.evaluate(
        "Array.from(document.querySelectorAll('#plot .hoverlayer .legend text')).map(t => t.textContent)"
    )
    labels = ["고가", "상한 80%", "하한 70%", "평균가", "저가"]
    order = [next(i for i, item in enumerate(items) if item.startswith(label)) for label in labels]
    assert order == sorted(order), f"호버 순서가 다릅니다: {items}"
