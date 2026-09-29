# CI 세부 계획

## 목적

PR과 `main` 푸시마다 수집·해석 코드와 대시보드 페이지가 깨지지 않았는지 자동으로 확인합니다. 외부 사이트(susansijang.co.kr, cdn.plot.ly)에 의존하지 않아야 CI 결과가 흔들리지 않으므로, 모든 검사는 저장소 안의 고정 데이터와 설치된 패키지만으로 돌아가게 만듭니다.

## 범위와 위험

| 대상 | 깨질 때의 증상 | 막을 검사 |
|------|---------------|-----------|
| `noryangjin.parse_table` | 응답 형식(xlsx, HTML 표, 제목 행)이 바뀌면 수집 전체가 실패 | 형식별 단위 테스트 |
| `split_name`, 어종 정확 일치 | '방어'에 '잿방어'가 섞여 가격이 틀어짐(실제로 한 번 발생) | 단위 테스트 |
| `kind_of` 자연산/양식·국산/수입 규칙 | 주간 표의 구분 행 값이 틀어짐 | 단위 테스트 |
| 경락량 환산(`수량 × 중량`) | 상자(S/P) 거래가 kg으로 잘못 합산 | 단위 테스트 |
| `collect_live` 수집 계획 | 백필이 이미 받은 날짜를 또 받거나 오늘·어제를 빠뜨림 | 모의 수집 테스트 |
| `render_page` | `</script>`가 든 값이 페이지를 깨뜨림, JSON 누락 | 단위 테스트 |
| 페이지 스크립트 | JS 오류로 표·차트가 비어 보임(실제로 한 번 발생) | 브라우저 스모크 테스트 |
| 저장된 원본 `data/raw` | 헤더가 바뀐 파일이 섞이면 페이지가 조용히 틀어짐 | 데이터 무결성 검사 |
| 워크플로 YAML | 문법 오류로 매일 수집이 멈춤 | YAML 파싱 검사 |

## 워크플로 설계 (`.github/workflows/ci.yml`)

- **트리거:** `pull_request`(모든 브랜치), `main` 푸시, `workflow_dispatch`. 매일 수집 봇의 데이터 커밋도 `main` 푸시라 검사가 돌지만, 몇십 초면 끝나 비용은 작습니다.
- **동시 실행:** 같은 ref의 이전 실행은 취소합니다(`concurrency`, `cancel-in-progress: true`).
- **권한:** `contents: read`만 둡니다.
- **파이썬:** 3.12, `pip` 캐시를 씁니다. 의존성은 `requirements.txt`와 `requirements-dev.txt`(pytest, ruff, playwright, pyyaml)로 나눕니다.

작업(job)은 세 개로 나눠 실패 위치를 바로 알 수 있게 합니다.

1. **lint:** `ruff check .`(pyflakes·pycodestyle 오류 규칙), `python -m compileall -q .`, 모든 워크플로 YAML을 `yaml.safe_load`로 파싱합니다.
2. **unit:** `pytest -m "not browser"`로 아래 단위 테스트를 돌립니다. 네트워크 호출은 `socket` 차단 fixture로 막아, 실수로 외부에 요청하면 테스트가 실패하게 합니다.
3. **browser:** `lint`와 `unit`이 통과한 뒤에만 돕니다(`needs`). Playwright 크로미엄을 설치하고, `DATA_SOURCE=sample`로 만든 페이지와 저장소의 실제 `data/raw`로 만든 페이지를 각각 엽니다. `cdn.plot.ly` 요청은 Playwright `route`로 가로채 파이썬 `plotly` 패키지에 든 plotly.js를 돌려주므로 외부 네트워크가 필요 없습니다. 실패하면 스크린샷을 아티팩트로 올립니다.

## 테스트 목록

### 단위 테스트 (`tests/test_noryangjin.py`, `tests/test_pipeline.py`)

- `parse_table`: cp949 HTML 표, 제목 행이 위에 붙은 xlsx, 머리글만 있는 빈 표(→ 빈 DataFrame), 표가 없는 차단 페이지(→ `FetchError`), 천 단위 쉼표 제거.
- `split_name`: `(활)방어` → (`활`, `방어`), 상태 표기가 없는 이름.
- `table_rows`: 실제 원본 `data/raw/방어/2026-09-29.csv`를 고정 데이터로 써서 잿방어가 빠지고 행 수·최고가가 기대값과 같은지 확인합니다.
- `kind_of`: 넙치 제주도 → 양식, 넙치 완도 → 자연산, 돌돔 일본 → 수입, 규칙 없는 어종 → 빈 문자열.
- 경락량: `중량`이 4인 S/P 행이 `weight=4`로 들어가는지 확인합니다.
- `collect_live`: `fetch_live`를 가짜로 바꿔, 원본이 있는 과거 날짜는 건너뛰고 오늘·어제는 항상 요청하는지 확인합니다.
- `live_rows`: 30일보다 오래된 원본이 빠지는지 확인합니다.
- `render_page`: 자리표시자가 치환되고, `</script>`가 든 값이 `<\/`로 바뀌며, JSON을 다시 읽으면 필드 순서가 `ROW_FIELDS`와 같은지 확인합니다.

### 브라우저 스모크 테스트 (`tests/test_page.py`, `@pytest.mark.browser`)

- 콘솔 오류와 페이지 오류가 없어야 합니다.
- 주간 표의 행 수가 `TABLE_ROWS` 수와 같고, 합계 행의 값이 각 열 합과 같아야 합니다.
- 표의 행을 누르면 '어종별 시세' 탭으로 바뀌고 그 어종이 선택돼야 합니다.
- ★를 누른 뒤 새로고침해도 그 어종이 맨 앞에 있어야 합니다.
- 산지 칩을 누르면 제목에 산지가 들어가야 합니다.
- 390px 폭에서 `document.documentElement.scrollWidth`가 390 이하여야 합니다(가로 스크롤 없음).

### 데이터 무결성 (`tests/test_data.py`)

- `data/raw/*/*.csv` 파일 이름이 날짜 형식이고, 모든 파일에 `어종`, `낙찰고가` 열이 있어야 합니다.
- 각 파일의 어종 이름이 폴더 이름을 포함해야 합니다(다른 어종 파일이 잘못 들어가는 것을 막음).

## 매일 수집 워크플로와의 연결

`update.yml`의 수집 단계 앞에 `pytest -m "not browser"`를 넣어, 코드가 깨진 상태로는 새 페이지를 커밋하지 않게 합니다.

## 머지 기준

- `ci.yml`의 세 작업이 PR의 최신 커밋에서 모두 통과해야 합니다.
- 저장소 설정의 브랜치 보호 규칙에서 세 작업을 필수 검사로 지정하는 것을 권장합니다(저장소 관리자가 설정).

## 로컬 실행

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && pytest -m "not browser"
python -m playwright install chromium && pytest -m browser
```
