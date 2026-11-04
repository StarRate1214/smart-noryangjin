"""경락 시세 알림: 저장된 원본으로 알림 규칙(alerts.json)을 판정하고, 새 알림을 ntfy 로 보낸다.

실행:
    python alerts.py            # 최근 거래일 알림을 판정해 출력만 한다
    python alerts.py --send     # NTFY_TOPIC 으로 새 알림을 보내고 data/alerts_sent.json 에 기록한다

규칙 종류(alerts.json 의 rules):
    change  최근 값이 기준(직전 거래일 또는 직전 N거래일 평균)보다 %·원(경락량은 kg) 이상 오르거나 내렸을 때
    level   최근 값이 지정한 원(경락량은 kg) 이상이거나 이하일 때
자동 감시(alerts.json 의 auto)는 표본이 많은 어종·상태에서 평소 움직임보다 크게 벗어난 가격·경락량을 잡는다.

환경 변수:
    NTFY_TOPIC   알림을 보낼 ntfy 주제(저장소 Secrets). 없으면 보내지 않는다
    NTFY_SERVER  ntfy 서버 (기본: https://ntfy.sh)
    PAGE_URL     알림을 눌렀을 때 열 대시보드 주소 (기본: GITHUB_REPOSITORY 의 GitHub Pages 주소)
"""

import datetime
import json
import math
import os
import statistics
import sys
from pathlib import Path

import pandas as pd
import requests

import pipeline

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "alerts.json"
SENT_FILE = ROOT / "data" / "alerts_sent.json"

METRICS = {"avg": "평균가", "high": "고가", "low": "저가", "volume": "경락량"}
STATE_NAMES = {"활": "활", "선": "선", "": "전체"}
# 규칙 판정에 쓰는 지난 거래 기간. 직전 20거래일 평균까지 고를 수 있어 넉넉히 둔다.
LOOKBACK_DAYS = 45
SENT_KEEP_DAYS = 14
MAX_MESSAGES = 15  # 한 번에 보낼 최대 알림 수. 넘으면 나머지는 한 건으로 묶는다

# 자동 감시. 2026-09~11 원본 55거래일로 맞췄다: 주요 어종 기준 하루 1~1.5건(보통).
# 가격은 직전 10거래일 평균가 로그값의 중앙값·MAD 로 본 이탈 정도(z)와 변동률을 함께 본다.
AUTO_WINDOW = 10
AUTO_MIN_HISTORY = 7
AUTO_MAJOR_LOTS = 5  # 직전 10거래일 하루 거래 건수의 중앙값이 이 이상이면 '표본이 많은 어종'
AUTO_LEVELS = {
    "low": {"price_z": 5.0, "price_pct": 0.30, "volume_z": 4.0},
    "normal": {"price_z": 4.0, "price_pct": 0.20, "volume_z": 3.0},
    "high": {"price_z": 3.0, "price_pct": 0.15, "volume_z": 2.5},
}
AUTO_VOLUME_RATIO = 2.0
AUTO_VOLUME_MIN_KG = 500

DEFAULT_CONFIG = {"auto": {"enabled": True, "sensitivity": "normal"}, "rules": []}


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------


def load_config(path=None):
    path = path or CONFIG_FILE
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    config = json.loads(path.read_text(encoding="utf-8"))
    errors = validate(config)
    if errors:
        raise SystemExit("alerts.json 형식 오류: " + "; ".join(errors))
    return config


def validate(config):
    """설정의 형식 오류를 문장으로 돌려준다. 페이지의 편집기와 같은 규칙이다."""
    errors = []
    auto = config.get("auto", {})
    if auto.get("sensitivity", "normal") not in AUTO_LEVELS:
        errors.append(f"auto.sensitivity 는 {list(AUTO_LEVELS)} 중 하나여야 합니다")
    for i, rule in enumerate(config.get("rules", []), 1):
        where = f"규칙 {i}"
        if rule.get("type") not in ("change", "level"):
            errors.append(f"{where}: type 은 change 또는 level 입니다")
            continue
        if rule.get("metric") not in METRICS:
            errors.append(f"{where}: metric 은 {list(METRICS)} 중 하나입니다")
        if rule.get("state", "") not in STATE_NAMES:
            errors.append(f"{where}: state 는 활, 선, '' 중 하나입니다")
        if not rule.get("species"):
            errors.append(f"{where}: species 가 비었습니다('*' 는 표본이 많은 어종 전체)")
        value = rule.get("value")
        if not isinstance(value, (int, float)) or value < 0:
            errors.append(f"{where}: value 는 0 이상의 숫자입니다")
        if rule.get("type") == "change":
            if rule.get("unit") not in ("pct", "abs"):
                errors.append(f"{where}: unit 은 pct 또는 abs 입니다")
            if rule.get("direction") not in ("up", "down", "both"):
                errors.append(f"{where}: direction 은 up, down, both 중 하나입니다")
            if not isinstance(rule.get("base_days"), int) or not 1 <= rule["base_days"] <= 20:
                errors.append(f"{where}: base_days 는 1~20 의 정수입니다")
        elif rule.get("op") not in ("above", "below"):
            errors.append(f"{where}: op 는 above 또는 below 입니다")
    return errors


# ---------------------------------------------------------------------------
# 일별 값
# ---------------------------------------------------------------------------


def recent_rows(latest, days=LOOKBACK_DAYS):
    since = (latest - datetime.timedelta(days=days)).isoformat()
    rows = []
    for path in sorted(pipeline.DAILY_DIR.glob("*.csv")):
        if since <= path.stem <= latest.isoformat():
            rows += pipeline.table_rows(path.stem, pd.read_csv(path))
    return rows


def daily_values(rows):
    """{'방어|활': {날짜: {'avg', 'high', 'low', 'volume', 'lots'}}}. 상태 '' 는 모든 상태를 합친 값이다.
    가격은 페이지와 같이 kg 거래만, 평균가는 수량 가중이다."""
    combined = [r[:3] + [""] + r[4:] for r in rows]
    out = {}
    for source in (rows, combined):
        ref = pipeline.summarize(source)
        for key, entries in ref["series"].items():
            series = out.setdefault(key, {})
            for i, avg, high, low, _, vol in entries:
                series[ref["dates"][i]] = {"avg": avg, "high": high, "low": low, "volume": vol, "lots": 0}
    for date, species, _, state, *_ in rows:
        for key in (f"{species}|{state}", f"{species}|"):
            out[key][date]["lots"] += 1
    return out


def history(series, latest):
    """latest 이전 거래일들(오래된 순)과 latest 의 값."""
    past = [series[d] for d in sorted(series) if d < latest]
    return past, series.get(latest)


def is_major(past):
    lots = [d["lots"] for d in past[-AUTO_WINDOW:]]
    return len(lots) >= AUTO_MIN_HISTORY and statistics.median(lots) >= AUTO_MAJOR_LOTS


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------


def unit_of(metric):
    return "kg" if metric == "volume" else "원"


def describe_rule(rule):
    metric = METRICS[rule["metric"]]
    unit = unit_of(rule["metric"])
    if rule["type"] == "level":
        return f"{metric} {rule['value']:,.0f}{unit} {'이상' if rule['op'] == 'above' else '이하'}"
    base = "직전 거래일" if rule["base_days"] == 1 else f"직전 {rule['base_days']}거래일 평균"
    amount = f"{rule['value']:g}%" if rule["unit"] == "pct" else f"{rule['value']:,.0f}{unit}"
    way = {"up": "상승", "down": "하락", "both": "변동"}[rule["direction"]]
    return f"{metric} {base} 대비 {amount} 이상 {way}"


def check_rule(rule, past, now):
    """규칙에 걸리면 알림 세부 내용(dict), 아니면 None."""
    metric = rule["metric"]
    cur = now.get(metric) if now else None
    if cur is None:
        return None
    if rule["type"] == "level":
        hit = cur >= rule["value"] if rule["op"] == "above" else cur <= rule["value"]
        return {"current": cur, "up": rule["op"] == "above"} if hit else None
    values = [d[metric] for d in past[-rule["base_days"]:] if d.get(metric) is not None]
    if len(values) < math.ceil(rule["base_days"] / 2):
        return None
    base = sum(values) / len(values)
    if base <= 0:
        return None
    diff = cur - base
    amount = diff / base * 100 if rule["unit"] == "pct" else diff
    threshold = rule["value"]
    hit = {"up": amount >= threshold, "down": amount <= -threshold, "both": abs(amount) >= threshold}[rule["direction"]]
    if not hit or diff == 0:
        return None
    return {"current": cur, "base": base, "pct": diff / base * 100, "up": diff > 0}


def robust_z(values, current, floor):
    logs = [math.log(v) for v in values]
    med = statistics.median(logs)
    mad = max(1.4826 * statistics.median(abs(x - med) for x in logs), floor)
    return (math.log(current) - med) / mad, math.exp(med)


def check_auto(past, now, sensitivity):
    """표본이 많은 어종·상태의 평균가·경락량이 평소 움직임에서 크게 벗어났는지 본다."""
    level = AUTO_LEVELS[sensitivity]
    window = past[-AUTO_WINDOW:]
    found = []
    prices = [d["avg"] for d in window if d.get("avg")]
    if now.get("avg") and len(prices) >= AUTO_MIN_HISTORY:
        z, typical = robust_z(prices, now["avg"], 0.05)
        pct = now["avg"] / typical - 1
        if abs(z) >= level["price_z"] and abs(pct) >= level["price_pct"]:
            found.append(("avg", now["avg"], typical, pct * 100))
    volumes = [d["volume"] for d in window if d.get("volume")]
    if now.get("volume") and len(volumes) >= AUTO_MIN_HISTORY:
        z, typical = robust_z(volumes, now["volume"], 0.1)
        if (z >= level["volume_z"] and now["volume"] >= typical * AUTO_VOLUME_RATIO
                and now["volume"] - typical >= AUTO_VOLUME_MIN_KG):
            found.append(("volume", now["volume"], typical, (now["volume"] / typical - 1) * 100))
    return found


def label_of(key):
    species, state = key.split("|")
    name = pipeline.LABELS.get(species)
    shown = f"{species}({name})" if name else species
    return f"{'(' + state + ')' if state else ''}{shown}"


def evaluate(config, values, latest):
    """latest(YYYY-MM-DD) 거래에 대한 알림 목록."""
    alerts = []
    auto = config.get("auto", {})
    majors = set()
    for key, series in values.items():
        past, now = history(series, latest)
        if key.endswith("|") or not now or not is_major(past):
            continue
        majors.add(key)
        if auto.get("enabled", True):
            for metric, cur, typical, pct in check_auto(past, now, auto.get("sensitivity", "normal")):
                alerts.append({
                    "id": f"auto:{metric}", "key": key, "metric": metric, "current": cur, "base": typical,
                    "pct": pct, "up": pct >= 0, "rule": f"자동 감시: 평소({AUTO_WINDOW}거래일)보다 크게 벗어남",
                })
    for i, rule in enumerate(config.get("rules", [])):
        if not rule.get("enabled", True):
            continue
        state = rule.get("state", "")
        if rule["species"] == "*":
            keys = sorted(k for k in majors if k.endswith(f"|{state}")) if state else sorted(majors)
        else:
            keys = [f"{rule['species']}|{state}"]
        for key in keys:
            if key not in values:
                continue
            past, now = history(values[key], latest)
            hit = check_rule(rule, past, now)
            if hit:
                alerts.append({"id": rule.get("id") or f"rule{i + 1}", "key": key, "metric": rule["metric"],
                               "rule": describe_rule(rule), **hit})
    for a in alerts:
        a["date"] = latest
        a["label"] = label_of(a["key"])
        a["text"] = message_text(a)
    return alerts


def message_text(alert):
    unit = unit_of(alert["metric"])
    metric = METRICS[alert["metric"]]
    head = f"{alert['label']} {metric} {alert['current']:,.0f}{unit}"
    if "base" in alert:
        pct = alert["pct"]
        head += f" ({'+' if pct >= 0 else '−'}{abs(pct):.0f}%, 기준 {alert['base']:,.0f}{unit})"
    return f"{head} · {alert['rule']}"


# ---------------------------------------------------------------------------
# 보내기
# ---------------------------------------------------------------------------


def load_sent():
    if not SENT_FILE.exists():
        return {}
    return json.loads(SENT_FILE.read_text(encoding="utf-8"))


def save_sent(sent, today):
    keep = (today - datetime.timedelta(days=SENT_KEEP_DAYS)).isoformat()
    sent = {d: sorted(set(keys)) for d, keys in sent.items() if d >= keep}
    SENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SENT_FILE.write_text(json.dumps(sent, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def alert_key(a):
    return f"{a['id']}|{a['key']}|{a['metric']}"


def publish(topic, title, message, tags, click):
    server = os.environ.get("NTFY_SERVER") or "https://ntfy.sh"
    body = {"topic": topic, "title": title, "message": message, "tags": tags, "priority": 4}
    if click:
        body["click"] = click
    # 본문을 JSON 으로 보내면 한글 제목도 그대로 간다(머리글로 보내면 latin-1 만 된다)
    requests.post(server.rstrip("/") + "/", json=body, timeout=20).raise_for_status()


def send(alerts, topic, click=None):
    """보낸 알림 수. 같은 날 같은 알림은 다시 보내지 않는다."""
    sent = load_sent()
    fresh = [a for a in alerts if alert_key(a) not in sent.get(a["date"], [])]
    for a in fresh[:MAX_MESSAGES]:
        tags = ["chart_with_upwards_trend" if a["up"] else "chart_with_downwards_trend", "fish"]
        publish(topic, f"노량진 {a['label']} {METRICS[a['metric']]}", a["text"] + f" ({a['date']})", tags, click)
    if len(fresh) > MAX_MESSAGES:
        rest = fresh[MAX_MESSAGES:]
        publish(topic, f"노량진 알림 {len(rest)}건 더", "\n".join(a["text"] for a in rest), ["fish"], click)
    for a in fresh:
        sent.setdefault(a["date"], []).append(alert_key(a))
    save_sent(sent, pipeline.today_kst())
    return len(fresh)


def page_url():
    """알림을 눌렀을 때 열 대시보드. PAGE_URL 이 없으면 저장소 이름으로 GitHub Pages 주소를 만든다."""
    if os.environ.get("PAGE_URL"):
        return os.environ["PAGE_URL"]
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    owner, name = repo.split("/")
    return f"https://{owner.lower()}.github.io/{name}/"


def latest_date():
    dates = sorted(p.stem for p in pipeline.DAILY_DIR.glob("*.csv"))
    return dates[-1] if dates else None


def run(send_now=False):
    config = load_config()
    latest = latest_date()
    if not latest:
        print("원본이 없어 알림을 판정하지 않습니다")
        return []
    alerts = evaluate(config, daily_values(recent_rows(datetime.date.fromisoformat(latest))), latest)
    for a in alerts:
        print(a["text"])
    print(f"{latest}: 알림 {len(alerts)}건")
    if not send_now:
        return alerts
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC 이 없어 보내지 않습니다")
        return alerts
    # 지난 날짜를 채우는 백필이나 오래된 원본으로는 보내지 않는다
    if latest < (pipeline.today_kst() - datetime.timedelta(days=1)).isoformat():
        print(f"최근 거래일({latest})이 오늘·어제가 아니라 보내지 않습니다")
        return alerts
    print(f"새 알림 {send(alerts, topic, page_url())}건을 보냈습니다")
    return alerts


if __name__ == "__main__":
    run(send_now="--send" in sys.argv)
