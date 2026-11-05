"""경락 시세 알림: 저장된 원본으로 알림 규칙(alerts.json)을 판정하고, 새 알림을 ntfy 로 보낸다.

실행:
    python alerts.py            # 최근 거래일 알림을 판정해 출력만 한다
    python alerts.py --send     # NTFY_TOPIC 으로 새 알림을 보내고 data/alerts_sent.json 에 기록한다

규칙(alerts.json 의 rules): 어종·상태와 조건 1~5개, 조건을 모두(match=all) 또는 하나라도(any) 만족하면 알린다.
조건 종류:
    change  최근 값이 기준(직전 거래일 또는 직전 N거래일 평균)보다 %·원(입하량은 kg)·배 이상 오르거나 내렸을 때
    level   최근 값이 지정한 원(입하량은 kg) 이상이거나 이하일 때
자동 감시(alerts.json 의 auto)는 '활어 입하량' 표의 품목(WATCH_LIST)에서 물량이 몰리며 값이 떨어지는 날을
잡는다(기본값).
품목별로 끄거나(off) 어종을 더할(extra) 수 있고, 감지 방식, 입하량 배수·증가 kg, 가격 방향·변동률,
평소 대비 강도, 기준 거래일을 모두 바꿀 수 있다.

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
import urllib.parse
from pathlib import Path

import pandas as pd
import requests

import pipeline

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "alerts.json"
SENT_FILE = ROOT / "data" / "alerts_sent.json"

# 입하량은 그날 낙찰된 수량 x 중량의 합(kg)이다. 시장이 발표하는 입하량과는 조금 다를 수 있다
METRICS = {"avg": "평균가", "high": "고가", "low": "저가", "volume": "입하량"}
STATE_NAMES = {"활": "활", "선": "선", "": "전체"}
# 규칙 판정에 쓰는 지난 거래 기간. 직전 20거래일 평균까지 고를 수 있어 넉넉히 둔다.
LOOKBACK_DAYS = 45
SENT_KEEP_DAYS = 14
MAX_MESSAGES = 15  # 한 번에 보낼 최대 알림 수. 넘으면 나머지는 한 건으로 묶는다

# 자동 감시. 목적은 '물량이 몰려 경매가가 떨어지는 날'(사기 좋은 날)을 알리는 것이다.
# 기본값은 2025-10~2026-11 원본 317거래일로 맞췄다: 입하량 1.5배·+300kg 그리고 평균가 -10%, 강도 1 이상이면
# 표본이 많은 어종 기준 하루 0.8건(일주일 4건 정도). 강도는 평소 출렁임(직전 거래일 값 로그의 MAD) 대비
# 벗어난 정도라, 늘 크게 출렁이는 어종이 매일 걸리지 않게 한다. 0 이면 보지 않는다.
AUTO_MIN_HISTORY = 7
AUTO_MODES = {
    "both": "입하량 급증 그리고 가격 변화",
    "any": "입하량 급증 또는 가격 변화",
    "volume": "입하량 급증",
    "price": "가격 변화",
}
# 자동 감시 대상: 시장이 매일 내는 '활어 입하량' 표의 품목(2026-11 표 기준). 값은 공식 표기와 구분(산지로 나눔)이다.
# 표의 줄전갱이는 공식 표기가 흑점줄전갱이이고, 양)우럭은 산지로 가를 수 없어 우럭 하나로 본다. '기타'는 뺀다.
WATCH_LIST = [
    ("넙치", "자연산"), ("넙치", "양식"), ("농어", "자연산"), ("농어", "양식"), ("참돔", "자연산"), ("참돔", "양식"),
    ("우럭", ""), ("감숭어", ""), ("참숭어", ""), ("점성어", ""), ("찰넙치", ""), ("줄돔", "국산"), ("줄돔", "수입"),
    ("감성돔", "국산"), ("감성돔", "수입"), ("벤자리", ""), ("민어", ""), ("도다리", ""), ("부시리", ""),
    ("자바리", ""), ("능성어", ""), ("붉바리", ""), ("흑점줄전갱이", ""), ("방어", "국산"), ("방어", "수입"),
    ("잿방어", ""), ("전갱이", ""), ("강도다리", ""), ("보리새우", ""),
]
WATCH_STATE = "활"  # 활어 입하량 기준


def watch_id(species, kind=""):
    """감시 대상 이름. 구분이 있으면 '넙치#양식'처럼 붙인다."""
    return f"{species}#{kind}" if kind else species


WATCH_IDS = [watch_id(s, k) for s, k in WATCH_LIST]

AUTO_DEFAULT = {
    "enabled": True,
    "mode": "both",
    "window": 10,      # 평소 기준: 직전 N거래일
    "off": [],         # 끈 기본 감시 어종(WATCH_IDS 중)
    "extra": [],       # 더 감시할 어종(공식 표기, 활어)
    "volume": {"ratio": 1.5, "min_kg": 300, "strength": 1},
    "price": {"direction": "down", "pct": 10, "strength": 1},
}
# 규칙의 '*'(표본이 많은 어종 전체): 직전 10거래일 하루 거래 건수의 중앙값이 이 이상인 어종·상태
MAJOR_WINDOW = 10
MAJOR_LOTS = 5

DEFAULT_CONFIG = {"auto": AUTO_DEFAULT, "rules": []}


def normalize_auto(auto):
    """빠진 값은 기본값으로 채운다. 예전 형식({"enabled", "sensitivity"})도 켜짐 여부만 살려 읽는다."""
    auto = auto or {}
    out = {**AUTO_DEFAULT, **{k: v for k, v in auto.items() if k in AUTO_DEFAULT}}
    out["volume"] = {**AUTO_DEFAULT["volume"], **(auto.get("volume") or {})}
    out["price"] = {**AUTO_DEFAULT["price"], **(auto.get("price") or {})}
    return out


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


CONDITION_KEYS = ("metric", "type", "base_days", "direction", "unit", "value", "op")
MAX_CONDITIONS = 5


def normalize_rule(rule):
    """규칙을 조건 목록 형식으로 맞춘다. 예전 형식(조건 필드가 규칙에 바로 있음)도 조건 하나짜리 규칙으로 읽는다."""
    if "conditions" in rule:
        return {**rule, "match": rule.get("match", "all")}
    rest = {k: v for k, v in rule.items() if k not in CONDITION_KEYS}
    return {**rest, "match": "all", "conditions": [{k: rule[k] for k in CONDITION_KEYS if k in rule}]}


def validate_condition(cond, where):
    errors = []
    if cond.get("type") not in ("change", "level"):
        return [f"{where}: type 은 change 또는 level 입니다"]
    if cond.get("metric") not in METRICS:
        errors.append(f"{where}: metric 은 {list(METRICS)} 중 하나입니다")
    value = cond.get("value")
    if not isinstance(value, (int, float)) or value < 0:
        errors.append(f"{where}: value 는 0 이상의 숫자입니다")
    if cond.get("type") == "change":
        if cond.get("unit") not in ("pct", "abs", "ratio"):
            errors.append(f"{where}: unit 은 pct, abs, ratio 중 하나입니다")
        elif cond["unit"] == "ratio" and isinstance(value, (int, float)) and value <= 1:
            errors.append(f"{where}: 배수(ratio)는 1보다 커야 합니다")
        if cond.get("direction") not in ("up", "down", "both"):
            errors.append(f"{where}: direction 은 up, down, both 중 하나입니다")
        if not isinstance(cond.get("base_days"), int) or not 1 <= cond["base_days"] <= 20:
            errors.append(f"{where}: base_days 는 1~20 의 정수입니다")
    elif cond.get("op") not in ("above", "below"):
        errors.append(f"{where}: op 는 above 또는 below 입니다")
    return errors


def validate_auto(auto):
    errors = []
    num = lambda v, lo, hi: isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi  # noqa: E731
    if auto["mode"] not in AUTO_MODES:
        errors.append(f"자동 감시: mode 는 {list(AUTO_MODES)} 중 하나입니다")
    if not (isinstance(auto["window"], int) and 5 <= auto["window"] <= 20):
        errors.append("자동 감시: window(평소 기준 거래일)는 5~20 의 정수입니다")
    for name in ("off", "extra"):
        if not (isinstance(auto[name], list) and all(isinstance(x, str) and x for x in auto[name])):
            errors.append(f"자동 감시: {name} 는 어종 이름 목록입니다")
    vol, price = auto["volume"], auto["price"]
    if not num(vol["ratio"], 1, 20):
        errors.append("자동 감시: 입하량 배수는 1~20 입니다")
    if not num(vol["min_kg"], 0, 100000):
        errors.append("자동 감시: 입하량 증가 kg 는 0 이상입니다")
    if price["direction"] not in ("up", "down", "both"):
        errors.append("자동 감시: 가격 방향은 up, down, both 중 하나입니다")
    if not num(price["pct"], 0, 100):
        errors.append("자동 감시: 가격 변동률은 0~100% 입니다")
    for name, part in (("입하량", vol), ("가격", price)):
        if not num(part["strength"], 0, 10):
            errors.append(f"자동 감시: {name} 강도는 0~10 입니다")
    return errors


def validate(config):
    """설정의 형식 오류를 문장으로 돌려준다. 페이지의 편집기와 같은 규칙이다."""
    errors = []
    errors += validate_auto(normalize_auto(config.get("auto")))
    for i, raw in enumerate(config.get("rules", []), 1):
        rule = normalize_rule(raw)
        where = f"규칙 {i}"
        if rule.get("state", "") not in STATE_NAMES:
            errors.append(f"{where}: state 는 활, 선, '' 중 하나입니다")
        if not rule.get("species"):
            errors.append(f"{where}: species 가 비었습니다('*' 는 표본이 많은 어종 전체)")
        if rule["match"] not in ("all", "any"):
            errors.append(f"{where}: match 는 all(그리고) 또는 any(또는)입니다")
        conditions = rule.get("conditions")
        if not isinstance(conditions, list) or not 1 <= len(conditions) <= MAX_CONDITIONS:
            errors.append(f"{where}: 조건은 1~{MAX_CONDITIONS}개입니다")
            continue
        for j, cond in enumerate(conditions, 1):
            errors += validate_condition(cond, f"{where} 조건 {j}")
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
    # 구분이 있는 행은 '넙치#양식'처럼 구분별로도 모은다(자동 감시의 기본 목록이 구분별이다)
    by_kind = [[r[0], watch_id(r[1], r[2])] + r[2:] for r in rows if r[2]]
    out = {}
    for source in (rows, combined, by_kind):
        ref = pipeline.summarize(source)
        for key, entries in ref["series"].items():
            series = out.setdefault(key, {})
            for i, avg, high, low, _, vol in entries:
                series[ref["dates"][i]] = {"avg": avg, "high": high, "low": low, "volume": vol, "lots": 0}
    for date, species, kind, state, *_ in rows:
        keys = [f"{species}|{state}", f"{species}|"] + ([f"{watch_id(species, kind)}|{state}"] if kind else [])
        for key in keys:
            out[key][date]["lots"] += 1
    return out


def history(series, latest):
    """latest 이전 거래일들(오래된 순)과 latest 의 값."""
    past = [series[d] for d in sorted(series) if d < latest]
    return past, series.get(latest)


def is_major(past):
    lots = [d["lots"] for d in past[-MAJOR_WINDOW:]]
    return len(lots) >= AUTO_MIN_HISTORY and statistics.median(lots) >= MAJOR_LOTS


def watch_targets(auto):
    """자동 감시할 대상 이름들: 기본 목록에서 끈 것을 빼고, 더한 어종을 붙인다."""
    off = set(auto["off"])
    return [w for w in WATCH_IDS if w not in off] + [w for w in auto["extra"] if w not in WATCH_IDS]


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------


def unit_of(metric):
    return "kg" if metric == "volume" else "원"


def describe_condition(cond):
    metric = METRICS[cond["metric"]]
    unit = unit_of(cond["metric"])
    if cond["type"] == "level":
        return f"{metric} {cond['value']:,.0f}{unit} {'이상' if cond['op'] == 'above' else '이하'}"
    base = "직전 거래일" if cond["base_days"] == 1 else f"직전 {cond['base_days']}거래일 평균"
    if cond["unit"] == "ratio":
        way = {"up": "이상", "down": "분의 1 이하", "both": "이상 또는 그 분의 1 이하"}[cond["direction"]]
        return f"{metric} {base}의 {cond['value']:g}배 {way}"
    amount = f"{cond['value']:g}%" if cond["unit"] == "pct" else f"{cond['value']:,.0f}{unit}"
    way = {"up": "상승", "down": "하락", "both": "변동"}[cond["direction"]]
    return f"{metric} {base} 대비 {amount} 이상 {way}"


def describe_rule(rule):
    rule = normalize_rule(rule)
    joiner = " 그리고 " if rule["match"] == "all" else " 또는 "
    return joiner.join(describe_condition(c) for c in rule["conditions"])


def check_condition(cond, past, now):
    """조건에 걸리면 세부 내용(dict), 아니면 None."""
    metric = cond["metric"]
    cur = now.get(metric) if now else None
    if cur is None:
        return None
    if cond["type"] == "level":
        hit = cur >= cond["value"] if cond["op"] == "above" else cur <= cond["value"]
        return {"metric": metric, "current": cur, "up": cond["op"] == "above"} if hit else None
    values = [d[metric] for d in past[-cond["base_days"]:] if d.get(metric) is not None]
    if len(values) < math.ceil(cond["base_days"] / 2):
        return None
    base = sum(values) / len(values)
    if base <= 0:
        return None
    diff = cur - base
    threshold = cond["value"]
    if cond["unit"] == "ratio":
        # 2배 상승은 기준의 2배 이상, 2배 하락은 기준의 절반 이하
        ratio = cur / base
        up, down = ratio >= threshold, ratio <= 1 / threshold
    else:
        amount = diff / base * 100 if cond["unit"] == "pct" else diff
        up, down = amount >= threshold, amount <= -threshold
    hit = {"up": up, "down": down, "both": up or down}[cond["direction"]]
    if not hit or diff == 0:
        return None
    return {"metric": metric, "current": cur, "base": base, "pct": diff / base * 100, "up": diff > 0}


def check_rule(rule, past, now):
    """규칙에 걸리면 {'hits': 걸린 조건들, 'up': 첫 조건의 방향}, 아니면 None.
    match 가 all 이면 모든 조건이, any 면 하나 이상이 걸려야 한다."""
    rule = normalize_rule(rule)
    results = [check_condition(c, past, now) for c in rule["conditions"]]
    hits = [r for r in results if r]
    if not hits or (rule["match"] == "all" and len(hits) < len(results)):
        return None
    return {"hits": hits, "up": hits[0]["up"]}


def robust_z(values, current, floor):
    logs = [math.log(v) for v in values]
    med = statistics.median(logs)
    mad = max(1.4826 * statistics.median(abs(x - med) for x in logs), floor)
    return (math.log(current) - med) / mad, math.exp(med)


def check_auto(past, now, auto):
    """자동 감시 설정으로 걸린 항목들(입하량, 평균가). 감지 방식(mode)에 맞지 않으면 빈 목록."""
    window = past[-auto["window"]:]
    need = min(AUTO_MIN_HISTORY, auto["window"])
    volume = price = None
    vols = [d["volume"] for d in window if d.get("volume")]
    if now.get("volume") and len(vols) >= need:
        z, typical = robust_z(vols, now["volume"], 0.1)
        v = auto["volume"]
        if (now["volume"] >= typical * v["ratio"] and now["volume"] - typical >= v["min_kg"]
                and z >= v["strength"] and now["volume"] > typical):
            volume = {"metric": "volume", "current": now["volume"], "base": typical,
                      "pct": (now["volume"] / typical - 1) * 100, "up": True}
    prices = [d["avg"] for d in window if d.get("avg")]
    if now.get("avg") and len(prices) >= need:
        z, typical = robust_z(prices, now["avg"], 0.05)
        p = auto["price"]
        pct = (now["avg"] / typical - 1) * 100
        up = pct >= p["pct"] and z >= p["strength"] and pct > 0
        down = pct <= -p["pct"] and z <= -p["strength"] and pct < 0
        if {"up": up, "down": down, "both": up or down}[p["direction"]]:
            price = {"metric": "avg", "current": now["avg"], "base": typical, "pct": pct, "up": pct > 0}
    mode = auto["mode"]
    if mode == "both":
        return [volume, price] if volume and price else []
    if mode == "volume":
        return [volume] if volume else []
    if mode == "price":
        return [price] if price else []
    return [h for h in (volume, price) if h]


def describe_auto(auto):
    way = {"down": "하락", "up": "상승", "both": "변동"}[auto["price"]["direction"]]
    vol = f"입하량 평소의 {auto['volume']['ratio']:g}배·{auto['volume']['min_kg']:,.0f}kg 이상 증가"
    price = f"평균가 {auto['price']['pct']:g}% 이상 {way}"
    text = {"both": f"{vol} 그리고 {price}", "any": f"{vol} 또는 {price}", "volume": vol, "price": price}[auto["mode"]]
    return f"자동 감시: 활어 {text} (직전 {auto['window']}거래일 대비)"


def label_of(key):
    target, state = key.split("|")
    species, _, kind = target.partition("#")
    name = pipeline.LABELS.get(species)
    shown = f"{species}({name})" if name else species
    if kind:
        shown += f"({kind})"
    return f"{'(' + state + ')' if state else ''}{shown}"


def evaluate(config, values, latest):
    """latest(YYYY-MM-DD) 거래에 대한 알림 목록."""
    alerts = []
    auto = normalize_auto(config.get("auto"))
    majors = set()
    for key, series in values.items():
        past, now = history(series, latest)
        if not key.endswith("|") and "#" not in key and now and is_major(past):
            majors.add(key)
    if auto["enabled"]:
        for target in watch_targets(auto):
            key = f"{target}|{WATCH_STATE}"
            if key not in values:
                continue
            past, now = history(values[key], latest)
            hits = check_auto(past, now, auto) if now else []
            if hits:
                # 가격이 걸렸으면 그 방향을, 입하량만 걸렸으면 하락 쪽(살 기회) 표시를 쓴다
                price = next((h for h in hits if h["metric"] == "avg"), None)
                alerts.append({"id": f"auto:{auto['mode']}", "key": key, "metric": hits[0]["metric"], "hits": hits,
                               "up": price["up"] if price else False, "rule": describe_auto(auto)})
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
                alerts.append({"id": rule.get("id") or f"rule{i + 1}", "key": key, "metric": hit["hits"][0]["metric"],
                               "rule": describe_rule(rule), **hit})
    for a in alerts:
        a["date"] = latest
        a["label"] = label_of(a["key"])
        a["text"] = message_text(a)
    return alerts


def message_text(alert):
    parts = []
    for h in alert["hits"]:
        unit = unit_of(h["metric"])
        part = f"{METRICS[h['metric']]} {h['current']:,.0f}{unit}"
        if "base" in h:
            part += f" ({'+' if h['pct'] >= 0 else '−'}{abs(h['pct']):.0f}%, 기준 {h['base']:,.0f}{unit})"
        parts.append(part)
    return f"{alert['label']} {', '.join(parts)} · {alert['rule']}"


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


def species_link(page, key):
    """알림을 눌렀을 때 그 어종의 시세 차트를 여는 주소. 예: ...#price?species=농어&state=활&kind=자연산"""
    if not page:
        return None
    target, state = key.split("|")
    species, _, kind = target.partition("#")
    query = urllib.parse.urlencode({"species": species, "state": state, "kind": kind}, quote_via=urllib.parse.quote)
    return f"{page.split('#')[0]}#price?{query}"


def send(alerts, topic, click=None):
    """보낸 알림 수. 같은 날 같은 알림은 다시 보내지 않는다."""
    sent = load_sent()
    fresh = [a for a in alerts if alert_key(a) not in sent.get(a["date"], [])]
    for a in fresh[:MAX_MESSAGES]:
        tags = ["chart_with_upwards_trend" if a["up"] else "chart_with_downwards_trend", "fish"]
        what = METRICS[a["metric"]] if len(a["hits"]) == 1 else "알림"
        link = species_link(click, a["key"])
        publish(topic, f"노량진 {a['label']} {what}", a["text"] + f" ({a['date']})", tags, link)
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
    # 경매 집계 전에 받은 반쪽 원본으로는 입하량이 적게 잡혀 엉뚱한 판정이 나오므로 보내지 않는다
    if pipeline.is_partial(datetime.date.fromisoformat(latest)):
        print(f"{latest} 원본이 경매 집계 전({pipeline.COMPLETE_HOUR}시 전)에 받은 것이라 보내지 않습니다")
        return alerts
    print(f"새 알림 {send(alerts, topic, page_url())}건을 보냈습니다")
    return alerts


if __name__ == "__main__":
    run(send_now="--send" in sys.argv)
