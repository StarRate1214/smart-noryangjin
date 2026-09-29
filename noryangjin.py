"""노량진수산물도매시장 공식 홈페이지(susansijang.co.kr) 어종별 경락시세 수집기.

'수산물가격정보 > 어종별경락시세'(miw3130) 화면의 엑셀 다운로드 요청을 그대로 재현한다.
    POST https://www.susansijang.co.kr/nsis/miw/ko/info/excel/miw3130
    form: kdfshNm, kdfshCode(어종명), searchStartDe, searchEndDe(YYYY.MM.DD), pageIndex/pageUnit/pageSize
응답 표에는 어종((활)방어 식), 산지, 규격(1미 등), 포장(kg, S/P 등), 수량, 중량, 낙찰고가, 낙찰저가, 평균가 열이 있다.

응답은 진짜 .xls/.xlsx 이거나 확장자만 xls 인 HTML 표일 수 있어 둘 다 처리한다.
"""

import io
import re
import time

import pandas as pd
import requests

BASE_URL = "https://www.susansijang.co.kr/nsis/miw/ko/info"
PAGE_URL = f"{BASE_URL}/miw3130"
EXCEL_URL = f"{BASE_URL}/excel/miw3130"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

HIGH_COLUMNS = ["낙찰고가", "최고가", "고가"]
NUMERIC_COLUMNS = ["수량", "중량", "낙찰고가", "낙찰저가", "평균가", "최고가", "최저가", "고가", "저가"]
NAME_COLUMNS = ["어종", "어종명", "품목", "품목명", "품명"]


class FetchError(RuntimeError):
    """사이트 응답을 받지 못했거나 해석하지 못했을 때."""

    def __init__(self, message, content=None):
        super().__init__(message)
        self.content = content


def new_session():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": PAGE_URL})
    # 화면을 먼저 열어 세션 쿠키(JSESSIONIDMIW 등)를 받는다.
    session.get(PAGE_URL, timeout=30).raise_for_status()
    return session


def download(session, species, date):
    day = date.strftime("%Y.%m.%d")
    form = {
        "pageIndex": 1,
        "pageUnit": 1000,
        "pageSize": 1000,
        "kdfshNm": species,
        "kdfshCode": species,
        "searchStartDe": day,
        "searchEndDe": day,
    }
    response = session.post(EXCEL_URL, data=form, timeout=60)
    response.raise_for_status()
    return response.content


def _find_header(df):
    """머리글이 첫 줄이 아닐 때(제목 행이 위에 붙는 경우) 가격 열이 있는 행을 머리글로 올린다."""
    if any(c in df.columns for c in HIGH_COLUMNS):
        return df
    for i in range(min(len(df), 15)):
        row = [str(v).strip() for v in df.iloc[i].tolist()]
        if any(c in row for c in HIGH_COLUMNS):
            body = df.iloc[i + 1 :].copy()
            body.columns = row
            return body.reset_index(drop=True)
    return df


def parse_table(content):
    """다운로드한 바이트를 DataFrame 으로 바꾼다. 행이 없으면 빈 DataFrame."""
    if not content or not content.strip():
        return pd.DataFrame()

    head = content[:8]
    try:
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            tables = [pd.read_excel(io.BytesIO(content), engine="xlrd", header=None)]
        elif head.startswith(b"PK"):
            tables = [pd.read_excel(io.BytesIO(content), engine="openpyxl", header=None)]
        else:
            text = _decode(content)
            if "<table" not in text.lower():
                raise FetchError("응답에 표가 없습니다(차단 페이지이거나 형식이 바뀌었을 수 있습니다).", content)
            tables = pd.read_html(io.StringIO(text), header=None)
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"응답을 표로 해석하지 못했습니다: {exc}", content) from exc

    for raw in tables:
        raw.columns = [str(c).strip() for c in raw.columns]
        df = _find_header(raw)
        if any(c in df.columns for c in HIGH_COLUMNS):
            return _clean(df)

    # 머리글만 있고 행이 없는 표, 또는 '데이터가 없습니다' 한 줄짜리 표
    if all(len(t) <= 1 for t in tables):
        return pd.DataFrame()
    raise FetchError("가격 열(낙찰고가)을 찾지 못했습니다.", content)


def _decode(content):
    for encoding in ("utf-8", "cp949"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _clean(df):
    df = df.copy()
    df.columns = [re.sub(r"\s+", "", str(c)) for c in df.columns]
    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(r"[^\d.]", "", regex=True), errors="coerce")
    high_col = next(c for c in HIGH_COLUMNS if c in df.columns)
    df = df[df[high_col].notna() & (df[high_col] > 0)]
    return df.reset_index(drop=True)


def split_name(value):
    """'(활)방어' -> ('활', '방어'). 상태 표기(활/선/냉 등)가 없으면 상태는 ''."""
    m = re.match(r"^\s*\(([^)]*)\)\s*(.*)$", str(value))
    return (m.group(1).strip(), m.group(2).strip()) if m else ("", str(value).strip())


def fetch_day(session, species, date, retries=3):
    last = None
    for attempt in range(retries):
        try:
            return parse_table(download(session, species, date))
        except requests.RequestException as exc:
            last = exc
            time.sleep(2 ** (attempt + 1))
    raise FetchError(f"{date} 요청 실패: {last}")
