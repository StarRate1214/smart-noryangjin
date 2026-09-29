# CI 세부 계획

## 목적

PR마다 수집·해석 코드와 대시보드 페이지가 깨지지 않았는지 자동으로 확인하고, 매일 수집 봇이 커밋하기 직전에도 데이터와 페이지를 검사합니다. 외부 사이트(susansijang.co.kr, cdn.plot.ly)에 의존하면 결과가 흔들리므로 CI는 저장소 안의 고정 데이터와 설치된 패키지만 씁니다.

## 범위와 위험

| 대상 | 깨질 때의 증상 | 막을 검사 |
|------|---------------|-----------|
| `noryangjin.parse_table` | 응답 형식(xlsx, HTML 표, 제목 행)이 바뀌면 수집 전체가 실패 | 형식별 단위 테스트 |
| `split_name`, 어종 정확 일치 | '방어'에 '잿방어', '우럭'에 '우럭조개', '넙치'에 '찰넙치'가 섞임(실제로 발생) | 실제 원본으로 만든 단위 테스트 |
| 숫자 열 정리 | `중량`에 천 단위 쉼표가 오면 `float()`에서 실패 | 단위 테스트 + 코드 수정 |
| `kind_of` 자연산/양식·국산/수입 규칙 | 산지 표기가 바뀌면 구분 행이 조용히 0이 됨 | 단위 테스트 + 데이터 검사 |
| 경락량 환산(`수량 × 중량`) | 상자(S/P) 거래가 kg으로 잘못 합산 | 단위 테스트 |
| `collect_live`·`fetch_live` 수집 계획 | 백필이 이미 받은 날짜를 또 받거나 오늘·어제를 빠뜨림, 전체 어종 요청(빈 검색어)이 깨짐 | 모의 수집 테스트 |
| 전체 어종 응답 잘림 | 하루치가 요청 한도에 닿으면 일부 어종이 조용히 빠짐 | 수집 시 실패 처리 + 데이터 검사 |
| `render_page` | `</script>`가 든 값이 페이지를 깨뜨림 | 단위 테스트 |
| 페이지 스크립트 | JS 오류로 표·차트가 비어 보임(실제로 발생) | 브라우저 스모크 테스트 |
| 저장된 원본 `data/daily`, 생성된 `index.html` | 헤더가 바뀐 파일, 비어 있는 페이지가 배포됨 | 데이터 검사(PR과 매일 수집 모두) |
| 워크플로 YAML | 문법 오류로 매일 수집이 멈춤 | YAML 파싱 검사 |

실제 사이트 응답 원본(바이트)은 아직 저장해 둔 것이 없어 이번 범위에서 뺍니다. 해석 실패 시 올라오는 `debug-response` 아티팩트를 받으면 `tests/fixtures`에 넣어 회귀 테스트로 추가합니다.

## 결정성 확보

- **날짜 고정:** `conftest.py`의 fixture가 `pipeline.today_kst`를 2026-09-29로 바꿉니다. 30일 범위, 오늘·어제 재수집 계산이 실행 날짜와 무관해집니다.
- **모듈 상수:** `pipeline.DAILY_DIR`, `BACKFILL_DAYS`는 import 시점에 정해지므로 테스트에서 `monkeypatch`로 바꿉니다. 원본 디렉터리는 `tmp_path`를 씁니다.
- **네트워크 차단:** 단위 테스트에만 `socket.socket.connect`를 막는 autouse fixture를 둡니다(`browser` 표시가 붙은 테스트는 제외). Playwright는 로컬 드라이버·서버와 소켓으로 통신하기 때문입니다.
- **plotly.js 버전 고정:** 페이지는 `plotly-2.35.2.min.js`를 씁니다. 개발 의존성에 같은 plotly.js를 내장한 `plotly==5.24.1`을 고정하고, Playwright `route`로 `cdn.plot.ly` 요청을 그 파일로 돌려줍니다. 두 버전이 같은지 확인하는 테스트도 둡니다. 수집 코드는 plotly를 쓰지 않으므로 `requirements.txt`에서 뺍니다.
- **페이지 생성:** 브라우저 테스트는 `pipeline.main()`을 부르지 않습니다(네트워크 수집, 저장소 `index.html` 덮어쓰기). `sample_rows()`나 `live_rows()` 결과를 `render_page()`로 만들어 `tmp_path`에 쓰고, `http.server`로 띄워 엽니다. `file://`와 달리 localStorage와 새로고침이 실제처럼 동작합니다.
- **브라우저 환경:** `locale="ko-KR"`, `timezone_id="Asia/Seoul"`로 고정합니다.

## 워크플로 설계

### `.github/workflows/ci.yml`

- **트리거:** `pull_request`, `main` 푸시, `workflow_dispatch`. 수집 봇이 `GITHUB_TOKEN`으로 올린 커밋은 다른 워크플로를 실행시키지 않으므로, 봇 커밋 검사는 `update.yml` 안에서 합니다.
- **동시 실행:** `group: ci-${{ github.ref }}`, `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`로 PR의 이전 실행만 취소합니다.
- **권한:** `contents: read`.
- **작업 두 개:**
  1. **checks:** 의존성 설치 후 `ruff check .`, `python -m compileall -q .`, 워크플로 YAML 파싱, `pytest -m "not browser"`를 차례로 돕니다. 설치를 한 번만 하도록 린트와 단위 테스트를 합쳤습니다.
  2. **browser:** `checks`가 통과한 뒤에만 돕니다. `~/.cache/ms-playwright`를 Playwright 버전으로 캐시하고, `playwright install --with-deps chromium` 후 `pytest -m browser`를 돌립니다. 실패하면 스크린샷을 아티팩트로 올립니다.
- **캐시:** `actions/setup-python`의 pip 캐시에 `cache-dependency-path: requirements*.txt`를 지정합니다.

### `update.yml`(매일 수집)

`python pipeline.py` 다음, 커밋 직전에 `pytest tests/test_data.py`를 돌립니다. 원본 헤더, 폴더와 어종 이름 일치, `index.html`의 데이터 JSON이 정상인지 확인하고, 실패하면 커밋하지 않습니다. 여기에는 `requirements-test.txt`(pytest만)만 설치해 Playwright는 받지 않습니다.

## 의존성과 설정 파일

- `requirements.txt`: 수집·생성 실행에 필요한 것(pandas, requests, lxml, xlrd, openpyxl).
- `requirements-test.txt`: `pytest` 고정 버전.
- `requirements-dev.txt`: `-r requirements-test.txt`, `ruff`, `playwright`, `pyyaml`, `plotly==5.24.1`, 모두 버전 고정.
- `pyproject.toml`: pytest(`testpaths = ["tests"]`, `--strict-markers`, `browser` 표시 등록)와 ruff(`select = ["E", "F", "W", "I"]`, `line-length = 120`) 설정.

## 테스트 목록

### 단위 테스트 (`tests/test_noryangjin.py`, `tests/test_pipeline.py`)

- `parse_table`: cp949 HTML 표, 제목 행이 위에 붙은 xlsx, 머리글만 있는 빈 표(→ 빈 DataFrame), 표가 없는 차단 페이지(→ `FetchError`), 천 단위 쉼표 제거(`중량` 포함).
- `split_name`: `(활)방어` → (`활`, `방어`), 상태 표기가 없는 이름.
- `table_rows`: 실제 원본에서 뽑은 방어·잿방어·우럭·우럭조개 행으로, 공식 표기로 정확히 나뉘는지(방어 최고가 33,000원, 잿방어 37,000원이 섞이지 않음)와 S/P 행의 `weight`가 4인지 확인합니다.
- `species_order`, `LABELS`, `TABLE_ROWS`: 표 어종이 앞에 오고 나머지는 경락량 순인지, 흔히 쓰는 이름이 공식 표기에 연결되는지 확인합니다.
- `kind_of`: 넙치 제주도 → 양식, 넙치 완도 → 자연산, 돌돔 일본 → 수입, 규칙 없는 어종 → 빈 문자열.
- `collect_live`: `BACKFILL_DAYS=5`, 원본 일부를 `tmp_path`에 만들어 두고 `fetch_live`를 가짜로 바꿔, 있는 과거 날짜는 건너뛰고 오늘·어제는 항상 요청하는지 확인합니다.
- `fetch_live`: 빈 검색어로 요청하고, 휴장일에는 파일을 만들지 않는지 확인합니다.
- `live_rows`: 고정 날짜 기준 30일보다 오래된 원본이 빠지는지 확인합니다.
- `render_page`: 자리표시자 치환, `</` 이스케이프, 필드 순서.

### 데이터 검사 (`tests/test_data.py`, PR과 매일 수집 모두)

- `data/daily/*.csv` 파일 이름이 날짜 형식이고 모든 파일에 `어종`, `산지`, `낙찰고가` 열이 있으며, 요청 한도보다 행이 적고 어종이 두 종류 이상이어야 합니다.
- 주간 표의 공식 표기(감숭어, 줄돔, 전어 등)가 원본에 한 번 이상 나와야 합니다.
- `KIND_RULES`의 국내 산지(제주도, 통영 등)가 원본에 한 번 이상 나와야 합니다.
- `index.html`에 자리표시자가 남아 있지 않고, 데이터 JSON을 읽을 수 있으며, 행이 1개 이상이어야 합니다.

### 브라우저 스모크 테스트 (`tests/test_page.py`, `@pytest.mark.browser`)

- 콘솔 오류와 페이지 오류가 없어야 합니다.
- 주간 표에 `TABLE_ROWS` 수만큼 행이 있고, 0이 아닌 칸이 하나 이상 있어야 합니다(빈 표 버그 방지).
- 합계 행의 각 값이 파이썬으로 계산한 합계와 ±1 안에서 같아야 합니다(반올림 차이 허용).
- 표의 행을 누르면 '어종별 시세' 탭으로 바뀌고 그 어종이 선택돼야 합니다.
- ★를 누른 뒤 새로고침해도 그 어종이 맨 앞에 있어야 합니다.
- 산지 칩을 누르면 제목에 산지가 들어가야 합니다.
- 시세 차트에 고가·평균가·저가·가성비 구간·경락량이 모두 있고, 날짜마다 저가 ≤ 평균가 ≤ 고가여야 합니다.
- '표시' 선택박스를 끄면 해당 항목이 숨겨지고, 새로고침해도 선택이 유지돼야 합니다.
- 390px 폭에서 가로 스크롤이 없어야 합니다.
- 산지 칩이 포함 → 제외 → 해제 순서로 바뀌고, 제외한 산지가 제목과 차트에서 빠져야 합니다.
- 어종 검색이 칩을 거르고(흔히 쓰는 이름 '킹크랩'으로 왕게 검색), Enter로 첫 결과를 고르며, 결과가 없으면 안내가 나와야 합니다.
- '더 보기'로 전체 어종이 펼쳐져야 합니다.
- 고가·저가·평균가 선이 주식 차트 관례 색(빨강·파랑·주황)이어야 합니다.
- 차트에 마우스를 올렸을 때 정보가 고가, 상한 80%, 하한 70%, 평균가, 저가 순서로 나와야 합니다.
- 샘플 데이터 페이지와 실제 원본 페이지 두 가지로 모두 돌립니다.

## 머지 기준

- `ci.yml`의 `checks`와 `browser`가 PR의 최신 커밋에서 모두 통과해야 합니다.
- 저장소 설정의 브랜치 보호 규칙에서 두 작업을 필수 검사로 지정하는 것을 권장합니다.

## 로컬 실행

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && pytest -m "not browser"
python -m playwright install chromium && pytest -m browser
```

## 평가 기록

- 1차 계획은 독립 서브에이전트 평가에서 69/100을 받았습니다. 지적된 차단 항목(plotly.js 버전 불일치, 날짜 미고정, 봇 커밋이 CI를 실행하지 않는 점, 저장소 `index.html` 덮어쓰기, pytest 설정 누락)과 제안 항목(합계 반올림 허용, 우럭조개·찰넙치·`중량` 테스트, 산지 규칙 데이터 검사, 동시 실행·캐시 설정, ruff 규칙 명시, 브라우저 로캘)을 위 내용에 반영했습니다.
