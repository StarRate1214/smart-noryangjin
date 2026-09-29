import datetime
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pipeline  # noqa: E402

FROZEN_TODAY = datetime.date(2026, 9, 29)


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """30일 범위와 오늘·어제 재수집 계산이 실행 날짜에 따라 바뀌지 않게 날짜를 고정한다."""
    monkeypatch.setattr(pipeline, "today_kst", lambda: FROZEN_TODAY)
    return FROZEN_TODAY


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """단위 테스트가 실수로 외부에 요청하면 실패하게 한다. 브라우저 테스트는 로컬 소켓이 필요해 제외한다."""
    if request.node.get_closest_marker("browser"):
        return

    def guard(self, address, *args, **kwargs):
        raise RuntimeError(f"테스트 중 네트워크 접속 시도: {address}")

    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    """pipeline.RAW_DIR 를 임시 디렉터리로 바꾼다."""
    path = tmp_path / "raw"
    path.mkdir()
    monkeypatch.setattr(pipeline, "RAW_DIR", path)
    return path


REAL_RAW = ROOT / "data" / "raw"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """브라우저 테스트가 실패하면 fixture 에서 스크린샷을 남길 수 있도록 결과를 노드에 붙인다."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)
