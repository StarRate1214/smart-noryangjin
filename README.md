# smart-noryangjin

노량진 수산시장 경매 시세를 매일 수집해 최근 30일 등락 추이와 **고가 대비 70~80% 가성비 매수 구간**을 Plotly 대화형 차트로 보여주는 개인 대시보드입니다. 차트에 마우스를 올리면 일자별 최고가(산지·규격 포함)와 80%선·70%선이 한 번에 표시됩니다.

페이지에서 바로 쓸 수 있는 옵션은 다음과 같습니다.

- **어종 선택:** 수집한 어종 중 하나를 고릅니다.
- **즐겨찾기(★):** 어종 이름 옆 ☆를 누르면 그 어종이 맨 앞에 오고, 다음 방문 때 먼저 선택됩니다. 이 설정은 보는 사람의 브라우저에만 저장됩니다.
- **상태:** 활어, 선어, 전체 중에서 고릅니다(기본 활어).
- **산지·규격:** 여러 개를 함께 고를 수 있고, 옆 숫자는 최근 30일 거래 건수입니다.

## 구성

| 파일 | 역할 |
|------|------|
| `noryangjin.py` | 공식 홈페이지 어종별 경락시세 요청·해석 |
| `pipeline.py` | 어종별 수집 → 원본 저장 → 최근 30일 원본을 `index.html`에 넣음 |
| `templates/dashboard.html` | 페이지 틀. 필터·즐겨찾기·최고가·70/80% 계산을 브라우저에서 처리 |
| `data/raw/<어종>/YYYY-MM-DD.csv` | 그날 받은 원본 경락 표 |
| `index.html` | GitHub Pages가 서빙하는 대시보드 |
| `.github/workflows/update.yml` | 매일 07:00 KST 자동 실행 후 결과 커밋 |

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

저장소 **Settings → Secrets and variables → Actions → Variables**에 `SPECIES`를 쉼표로 구분해 넣습니다. 기본값은 `방어,잿방어,넙치,참돔,우럭,농어,참숭어`이고, 적은 순서가 페이지의 어종 순서가 됩니다(즐겨찾기가 없을 때는 첫 어종이 기본 선택). 이름은 공식 홈페이지 표기를 따라야 합니다(예: 광어는 `넙치`). 어종을 추가한 뒤에는 **Run workflow**에서 `backfill_days`를 30으로 한 번 실행해 지난 기록을 채우세요.

어종 하나당 날짜마다 1초 간격으로 요청하므로, 매일 자동 실행은 어종 수 × 2회(오늘·어제), 30일 백필은 어종 수 × 30회 정도 요청합니다. 기간·비율은 `pipeline.py` 상단의 `WINDOW_DAYS`, `LOWER_RATIO`, `UPPER_RATIO`로 조정합니다.
