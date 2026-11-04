import datetime
import json

import pytest

import alerts
import pipeline
from tests.conftest import FROZEN_TODAY


def day(n):
    return (FROZEN_TODAY - datetime.timedelta(days=n)).isoformat()


def series(values, metric="avg", lots=6):
    """오래된 날부터 values 를 그 metric 값으로 갖는 일별 값. 마지막 값이 최근 거래일(FROZEN_TODAY)이다."""
    n = len(values)
    out = {}
    for i, v in enumerate(values):
        d = {"avg": 10000, "high": 12000, "low": 8000, "volume": 100, "lots": lots}
        d[metric] = v
        out[day(n - 1 - i)] = d
    return out


def change_rule(**kw):
    rule = {"type": "change", "species": "방어", "state": "활", "metric": "avg", "base_days": 1,
            "direction": "both", "unit": "pct", "value": 20}
    rule.update(kw)
    return rule


@pytest.mark.parametrize("rule, values, fired", [
    (change_rule(direction="up"), [10000, 12500], True),
    (change_rule(direction="up"), [10000, 7500], False),
    (change_rule(direction="down"), [10000, 7500], True),
    (change_rule(direction="both", value=30), [10000, 12500], False),
    (change_rule(unit="abs", value=2000, direction="up"), [10000, 12100], True),
    (change_rule(base_days=5, direction="up"), [9000, 10000, 11000, 10000, 10000, 12100], True),  # 평균 10000
    (change_rule(metric="volume", unit="abs", value=500, direction="up"), [100, 700], True),
    ({"type": "level", "species": "방어", "state": "활", "metric": "high", "op": "above", "value": 30000},
     [12000, 31000], True),
    ({"type": "level", "species": "방어", "state": "활", "metric": "low", "op": "below", "value": 5000},
     [8000, 6000], False),
])
def test_check_rule(rule, values, fired):
    metric = rule["metric"]
    s = series(values, metric)
    past, now = alerts.history(s, FROZEN_TODAY.isoformat())
    assert (alerts.check_rule(rule, past, now) is not None) == fired


def test_change_rule_needs_half_of_the_base_days():
    s = series([10000, 13000])  # 직전 10거래일 평균을 고르면 지난 값이 1개뿐이다
    past, now = alerts.history(s, FROZEN_TODAY.isoformat())
    assert alerts.check_rule(change_rule(base_days=10), past, now) is None


def test_describe_rule_reads_like_a_sentence():
    rule = change_rule(base_days=5, direction="up")
    assert alerts.describe_rule(rule) == "평균가 직전 5거래일 평균 대비 20% 이상 상승"
    level = {"type": "level", "metric": "volume", "op": "below", "value": 1000}
    assert alerts.describe_rule(level) == "입하량 1,000kg 이하"


def test_validate_reports_each_problem():
    bad = {"auto": {"sensitivity": "max"}, "rules": [
        {"type": "change", "species": "", "state": "x", "metric": "price", "value": -1, "unit": "won",
         "direction": "sideways", "base_days": 30},
        {"type": "level", "species": "방어", "state": "활", "metric": "avg", "value": 1, "op": "near"},
    ]}
    errors = alerts.validate(bad)
    assert len(errors) == 9, errors
    assert alerts.validate(json.loads((pipeline.ROOT / "alerts.json").read_text(encoding="utf-8"))) == []


def test_evaluate_applies_star_rules_only_to_major_series():
    latest = FROZEN_TODAY.isoformat()
    values = {
        "방어|활": series([10000] * 9 + [13000]),
        "참돔|활": series([10000] * 9 + [13000], lots=1),  # 표본이 적다
        "방어|": series([10000] * 9 + [13000]),
    }
    config = {"auto": {"enabled": False}, "rules": [change_rule(species="*", state="활", direction="up")]}
    fired = alerts.evaluate(config, values, latest)
    assert [(a["key"], a["up"]) for a in fired] == [("방어|활", True)]
    assert fired[0]["text"].startswith("(활)방어 평균가 13,000원 (+30%, 기준 10,000원)")


def test_auto_watch_catches_a_jump_but_not_usual_noise():
    latest = FROZEN_TODAY.isoformat()
    calm = [10000, 10300, 9800, 10100, 9900, 10200, 10000, 9700, 10100, 10000]
    values = {"방어|활": series(calm + [14000]), "참돔|활": series(calm + [10400])}
    fired = alerts.evaluate({"auto": {"enabled": True, "sensitivity": "normal"}, "rules": []}, values, latest)
    assert [(a["key"], a["metric"]) for a in fired] == [("방어|활", "avg")]


def test_daily_values_combine_states_and_count_lots():
    rows = [
        [day(0), "방어", "", "활", "포항", "1미", "kg", 3.0, 1.0, 30000.0, 20000.0, 25000.0],
        [day(0), "방어", "", "선", "포항", "1미", "kg", 1.0, 1.0, 9000.0, 8000.0, 9000.0],
    ]
    values = alerts.daily_values(rows)
    assert values["방어|활"][day(0)]["lots"] == 1
    combined = values["방어|"][day(0)]
    assert combined["lots"] == 2 and combined["avg"] == 21000 and combined["high"] == 30000


def fired_alert(**kw):
    a = {"id": "r1", "key": "방어|활", "metric": "avg", "date": day(0), "up": True, "label": "(활)방어",
         "text": "(활)방어 평균가 13,000원", "hits": [{"metric": "avg"}]}
    a.update(kw)
    return a


def test_send_skips_alerts_already_sent_today(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "SENT_FILE", tmp_path / "sent.json")
    posted = []
    monkeypatch.setattr(alerts, "publish", lambda topic, title, message, tags, click: posted.append((title, tags)))
    assert alerts.send([fired_alert()], "topic") == 1
    volume = fired_alert(metric="volume", up=False, hits=[{"metric": "volume"}])
    assert alerts.send([fired_alert(), volume], "topic") == 1
    assert posted == [("노량진 (활)방어 평균가", ["chart_with_upwards_trend", "fish"]),
                      ("노량진 (활)방어 입하량", ["chart_with_downwards_trend", "fish"])]


def test_send_bundles_alerts_over_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "SENT_FILE", tmp_path / "sent.json")
    posted = []
    monkeypatch.setattr(alerts, "publish", lambda topic, title, message, tags, click: posted.append(title))
    many = [fired_alert(key=f"어종{i}|활") for i in range(alerts.MAX_MESSAGES + 3)]
    assert alerts.send(many, "topic") == alerts.MAX_MESSAGES + 3
    assert len(posted) == alerts.MAX_MESSAGES + 1 and posted[-1] == "노량진 알림 3건 더"


def test_publish_sends_utf8_json(monkeypatch):
    sent = {}

    class Resp:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(alerts.requests, "post", lambda url, json, timeout: sent.update(url=url, body=json) or Resp())
    alerts.publish("t", "노량진 방어", "본문", ["fish"], "https://example.test/")
    assert sent["url"] == "https://ntfy.sh/"
    assert sent["body"] == {"topic": "t", "title": "노량진 방어", "message": "본문", "tags": ["fish"], "priority": 4,
                            "click": "https://example.test/"}


def test_run_does_not_send_for_old_data(daily_dir, monkeypatch, tmp_path):
    daily_dir.mkdir()
    header = "어종,산지,규격,포장,수량,중량,낙찰고가,낙찰저가,평균가\n"
    (daily_dir / f"{day(5)}.csv").write_text(header, encoding="utf-8")
    monkeypatch.setenv("NTFY_TOPIC", "topic")
    monkeypatch.setattr(alerts, "CONFIG_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(alerts, "send", lambda *a: pytest.fail("지난 원본으로 알림을 보냈습니다"))
    assert alerts.run(send_now=True) == []


def test_page_url_defaults_to_github_pages(monkeypatch):
    monkeypatch.delenv("PAGE_URL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "StarRate1214/smart-noryangjin")
    assert alerts.page_url() == "https://starrate1214.github.io/smart-noryangjin/"


def test_broken_alert_config_does_not_stop_the_page(monkeypatch, tmp_path):
    broken = tmp_path / "alerts.json"
    broken.write_text('{"rules": [{"type": "nope"}]}', encoding="utf-8")
    monkeypatch.setattr(alerts, "CONFIG_FILE", broken)
    info = pipeline.alert_info("live")
    assert "형식 오류" in info["error"] and info["config"] == alerts.DEFAULT_CONFIG and info["fired"] == []


@pytest.mark.parametrize("direction, values, fired", [
    ("up", [500, 500, 500, 500, 500, 1000], True),    # 평소 500kg -> 오늘 1000kg 은 2배
    ("up", [500, 500, 500, 500, 500, 900], False),
    ("down", [500, 500, 500, 500, 500, 250], True),   # 2배 하락 = 절반 이하
    ("down", [500, 500, 500, 500, 500, 300], False),
    ("both", [500, 500, 500, 500, 500, 1000], True),
])
def test_ratio_condition(direction, values, fired):
    cond = {"type": "change", "metric": "volume", "base_days": 5, "unit": "ratio", "direction": direction, "value": 2}
    past, now = alerts.history(series(values, "volume"), FROZEN_TODAY.isoformat())
    assert (alerts.check_condition(cond, past, now) is not None) == fired


def combined_rule(match):
    return {"species": "방어", "state": "활", "match": match, "conditions": [
        {"type": "change", "metric": "volume", "base_days": 5, "unit": "ratio", "direction": "up", "value": 2},
        {"type": "change", "metric": "avg", "base_days": 1, "unit": "pct", "direction": "down", "value": 10},
    ]}


@pytest.mark.parametrize("match, volume, price, hits", [
    ("all", 1000, 8500, 2),
    ("all", 1000, 10000, None),   # 입하량만 늘고 가격은 그대로
    ("any", 1000, 10000, 1),
    ("any", 500, 8500, 1),
    ("any", 500, 10000, None),
])
def test_and_or_rules(match, volume, price, hits):
    s = series([10000] * 5 + [price])
    for i, d in enumerate(sorted(s)):
        s[d]["volume"] = 500 if i < 5 else volume
    past, now = alerts.history(s, FROZEN_TODAY.isoformat())
    result = alerts.check_rule(combined_rule(match), past, now)
    assert (result and len(result["hits"])) == hits


def test_combined_rule_message_lists_every_hit():
    s = series([10000] * 5 + [8500])
    for i, d in enumerate(sorted(s)):
        s[d]["volume"] = 500 if i < 5 else 1000
    config = {"auto": {"enabled": False}, "rules": [combined_rule("all")]}
    (a,) = alerts.evaluate(config, {"방어|활": s}, FROZEN_TODAY.isoformat())
    assert a["text"] == ("(활)방어 입하량 1,000kg (+100%, 기준 500kg), 평균가 8,500원 (−15%, 기준 10,000원) · "
                         "입하량 직전 5거래일 평균의 2배 이상 그리고 평균가 직전 거래일 대비 10% 이상 하락")


def test_old_flat_rules_still_work():
    flat = change_rule(direction="up")
    assert alerts.normalize_rule(flat)["conditions"] == [
        {"metric": "avg", "type": "change", "base_days": 1, "direction": "up", "unit": "pct", "value": 20}]
    assert alerts.validate({"rules": [flat]}) == []
    past, now = alerts.history(series([10000, 12500]), FROZEN_TODAY.isoformat())
    assert alerts.check_rule(flat, past, now)["hits"][0]["pct"] == 25


def test_validate_checks_match_and_conditions():
    rule = combined_rule("maybe")
    rule["conditions"][0]["value"] = 1  # 배수는 1보다 커야 한다
    errors = alerts.validate({"rules": [rule, {**combined_rule("all"), "conditions": []}]})
    assert errors == [
        "규칙 1: match 는 all(그리고) 또는 any(또는)입니다",
        "규칙 1 조건 1: 배수(ratio)는 1보다 커야 합니다",
        "규칙 2: 조건은 1~5개입니다",
    ]
