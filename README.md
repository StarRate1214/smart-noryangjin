# smart-noryangjin

노량진 수산시장 경매 시세를 매일 수집해 최근 30일 등락 추이와 **고가 대비 70~80% 가성비 매수 구간**을 Plotly 대화형 차트로 보여주는 개인 대시보드입니다. 차트에 마우스를 올리면 일자별 최고가(산지·규격 포함)와 80%선·70%선이 한 번에 표시됩니다.

페이지는 두 개의 탭으로 되어 있습니다.

- **주간 경락량:** 활어 어종별로 최근 5영업일의 경락량(kg), 합계, 바로 앞 5영업일 대비 증감을 표로 보여 줍니다. 넙치·농어·참돔은 자연산·양식, 감성돔·돌돔은 국산·수입으로 나눕니다. ‹ › 버튼으로 이전 주를 볼 수 있고, 행을 누르면 그 어종의 시세 차트로 이동합니다.
- **어종별 시세:** 날짜별 경매 최고가(선)와 경락량(막대)을 함께 그리고, 최고가의 70~80% 가성비 구간을 음영으로 표시합니다. 어종 선택, 즐겨찾기(★, 보는 사람의 브라우저에만 저장), 상태(활어·선어·전체), 산지·규격 다중 선택, 기간(2주·30일)을 고를 수 있습니다.

색상은 #3368A0, #66A3BF, #C8DFDB, #F2EFE7을 씁니다.

## 구성

| 파일 | 역할 |
|------|------|
| `noryangjin.py` | 공식 홈페이지 어종별 경락시세 요청·해석 |
| `pipeline.py` | 어종별 수집 → 원본 저장 → 최근 30일 원본을 `index.html`에 넣음. 어종 목록, 공식 표기 별칭, 자연산/양식 규칙이 여기 있음 |
| `templates/dashboard.html` | 페이지 틀. 필터·즐겨찾기·최고가·70/80% 계산을 브라우저에서 처리 |
| `data/raw/<어종>/YYYY-MM-DD.csv` | 그날 받은 원본 경락 표 |
| `index.html` | GitHub Pages가 서빙하는 대시보드 |
| `.github/workflows/update.yml` | 매일 07:00 KST 자동 수집, 데이터 검사 후 커밋 |
| `.github/workflows/ci.yml` | PR·`main` 푸시마다 린트, 단위·데이터 테스트, 브라우저 스모크 테스트 |
| `tests/`, `docs/CI_PLAN.md` | 테스트와 CI 세부 계획 |

## 로컬 실행

```bash
pip install -r requirements.txt
python pipeline.py                     # 공식 홈페이지에서 어종별 오늘·어제 시세 수집
BACKFILL_DAYS=30 python pipeline.py    # 원본이 없는 지난 30일도 채움
DATA_SOURCE=sample python pipeline.py  # 사이트 접속 없이 샘플 데이터로 화면만 확인
```

## 배포 (GitHub Pages)

1. 저장소 **Settings → Pages → Build and deployment**에서 Source를 `Deploy from a branch`, Branch를 `main` / `(root)`로 저장합니다.
2. **Actions → Update market dashboard → Run workflow**를 한 번 수동 실행하면 이후 매일 07:00 KST에 갱신됩니다. 기록을 새로 채울 때는 `backfill_days`에 30을 넣습니다.

## 수집 방식 (노량진수산물도매시장 공식 홈페이지)

`noryangjin.py`는 공식 홈페이지 **수산물가격정보 → 어종별경락시세**(`/nsis/miw/ko/info/miw3130`) 화면의 엑셀 다운로드 요청을 그대로 재현합니다.

1. 화면(`miw3130`)을 GET 해서 세션 쿠키를 받습니다.
2. `excel/miw3130`에 어종명(`kdfshNm`)과 조회일(`searchStartDe`, `searchEndDe`, `YYYY.MM.DD`)을 POST 합니다. 날짜마다 1초 간격으로 요청합니다.
3. 응답 표(어종, 산지, 규격, 포장, 수량, 중량, 낙찰고가, 낙찰저가, 평균가)를 `data/raw/<어종>/YYYY-MM-DD.csv`로 저장합니다. 휴장일(일요일, 명절)은 행이 없어 건너뜁니다.
4. 최근 30일 원본 행을 페이지에 넣습니다. 어종 이름은 `(활)`·`(선)` 표기를 뗀 뒤 검색어와 정확히 같아야 하므로 `방어` 검색에 섞여 오는 `잿방어`는 빠집니다. 날짜별 최고가는 페이지에서 고른 조건(상태·산지·규격, kg 단위 거래만)으로 계산합니다.
5. 해석에 실패한 응답은 워크플로 아티팩트 `debug-response`로 올리고 작업을 실패 처리합니다.

오늘과 어제는 늦게 올라오는 경락분이 있을 수 있어 매번 다시 받습니다.

### 수집 어종 바꾸기

기본 수집 어종은 넙치, 농어, 참돔, 숭어, 부시리, 도다리, 능성어, 방어, 잿방어, 흑점줄전갱이, 민어, 벤자리, 감성돔, 돌돔, 황전어, 참숭어, 우럭, 킹크랩입니다. 바꾸려면 저장소 **Settings → Secrets and variables → Actions → Variables**에 `SPECIES`를 쉼표로 구분해 넣습니다. 적은 순서가 페이지의 어종 순서가 됩니다.

공식 홈페이지 표기가 다른 어종은 `pipeline.py`의 `ALIASES`로 연결합니다. 현재 숭어=감숭어, 돌돔=줄돔, 황전어=전어, 킹크랩=왕게입니다. 공식 표기는 어종명을 비워 조회하면 나오는 전체 목록(172종)에서 확인했습니다. 어종을 추가한 뒤에는 **Run workflow**에서 `backfill_days`를 30으로 한 번 실행해 지난 기록을 채우세요.

자연산·양식, 국산·수입은 원본에 구분이 없어 산지로 나눕니다(`KIND_RULES`). 공개 입하 통계와 산지별 경락 수량을 대조해 넙치 제주도, 참돔 일본·통영, 농어 중국을 양식으로 봅니다.

어종 하나당 날짜마다 1초 간격으로 요청하므로, 매일 자동 실행은 어종 수 × 2회(오늘·어제), 30일 백필은 어종 수 × 30회 정도 요청합니다. 기간·비율은 `pipeline.py` 상단의 `WINDOW_DAYS`, `LOWER_RATIO`, `UPPER_RATIO`로 조정합니다.

## CI

`docs/CI_PLAN.md`에 세부 계획이 있습니다. 외부 사이트 없이 돌도록 날짜를 2026-09-29로 고정하고, 단위 테스트는 네트워크 접속을 막고, 브라우저 테스트는 `cdn.plot.ly` 요청을 `plotly==5.24.1` 패키지에 든 plotly.js 2.35.2로 돌려줍니다.

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && pytest -m "not browser"
python -m playwright install chromium && pytest -m browser
```

매일 수집 봇의 커밋은 CI를 실행시키지 않으므로, `update.yml`이 커밋 직전에 `tests/test_data.py`로 원본과 페이지를 검사하고 실패하면 커밋하지 않습니다.
