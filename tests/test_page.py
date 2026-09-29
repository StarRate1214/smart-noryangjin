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
    wanted = {(s, k) for s, k in pipeline.TABLE_ROWS}
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
    assert rows.count() == len([r for r in pipeline.TABLE_ROWS if r[0] in pipeline.SPECIES])
    cells = page.locator("#weekTable tbody td:not(:first-child)").all_inner_texts()
    assert any(number(c) > 0 for c in cells if re.fullmatch(r"[\d,]+", c)), "주간 표가 모두 0 입니다"
    foot = page.locator("#weekTable tfoot td").all_inner_texts()
    expected = expected_week_totals(site["rows"])
    shown = [number(t) for t in foot[1 : 1 + len(expected)]]
    assert shown == pytest.approx(expected, abs=1.0)


def test_row_click_opens_species_chart(page):
    first = page.locator("#weekTable tbody tr").first
    species = first.get_attribute("title").replace(" 시세 보기", "")
    first.click()
    assert page.locator("#tab-price").get_attribute("aria-selected") == "true"
    assert species in page.locator('#species .chip[aria-pressed="true"]').inner_text()
    assert species in page.inner_text("#chartTitle")


def test_favorite_moves_species_first_after_reload(page):
    page.click("#tab-price")
    last = page.locator("#species .chip").last
    name = last.inner_text().replace("☆", "").strip()
    last.locator(".star").click()
    page.reload()
    page.wait_for_selector("#weekTable tbody tr", state="attached")
    page.click("#tab-price")
    first = page.locator("#species .chip").first
    assert "★" in first.inner_text() and name in first.inner_text()


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
