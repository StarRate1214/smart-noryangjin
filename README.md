# smart-noryangjin

노량진 수산시장 경매 시세를 매일 수집해 최근 30일 등락 추이와 **고가 대비 70~80% 가성비 매수 구간**을 Plotly 대화형 차트로 보여주는 개인 대시보드입니다. 차트에 마우스를 올리면 일자별 최고가·80%선·70%선이 한 번에 표시됩니다.

## 구성

| 파일 | 역할 |
|------|------|
| `noryangjin.py` | 공식 홈페이지 어종별 경락시세 요청·해석 |
| `pipeline.py` | 수집 → 원본 저장 → 날짜별 최고가 계산 → 70/80% 목표가 → `index.html` 생성 |
| `data/raw/YYYY-MM-DD.csv` | 그날 받은 원본 경락 표(최고가 계산의 기준) |
| `data/prices.csv` | 날짜별 경매 최고가 |
| `index.html` | GitHub Pages가 서빙하는 대시보드 |
| `.github/workflows/update.yml` | 매일 07:00 KST 자동 실행 후 결과 커밋 |

## 로컬 실행

```bash
pip install -r requirements.txt
python pipeline.py                     # 공식 홈페이지에서 오늘·어제 시세 수집
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
3. 응답 표(어종, 산지, 규격, 포장, 수량, 중량, 낙찰고가, 낙찰저가, 평균가)를 `data/raw/YYYY-MM-DD.csv`로 저장합니다. 휴장일(일요일, 명절)은 행이 없어 건너뜁니다.
4. 저장된 원본 전체에 아래 필터를 적용해 날짜별 `낙찰고가` 최댓값을 다시 계산합니다. 그래서 필터를 바꾸면 과거 기록도 같은 기준으로 바뀝니다.
5. 해석에 실패한 응답은 워크플로 아티팩트 `debug-response`로 올리고 작업을 실패 처리합니다.

오늘과 어제는 늦게 올라오는 경락분이 있을 수 있어 매번 다시 받습니다.

### 필터 설정

저장소 **Settings → Secrets and variables → Actions → Variables**에 넣습니다. 모두 선택 사항입니다.

| 변수 | 기본값 | 의미 |
|------|------|------|
| `ITEM_NAME` | `방어` | 화면에 표시할 이름 |
| `SEARCH_NAME` | `방어` | 홈페이지 어종 검색어. `(활)`·`(선)` 표기를 뗀 이름이 정확히 같아야 사용하므로 `잿방어`는 섞이지 않습니다 |
| `NAME_CONTAINS` | `(활)` | 어종명에 이 글자가 들어간 행만 사용. 선어까지 포함하려면 `방어` |
| `ITEM_SIZES` | (전체) | 쉼표로 구분한 규격 목록, 예: `1미` |
| `PACK_UNIT` | `kg` | 이 포장 단위 행만 사용(`S/P` 상자 단위 제외) |
| `DATA_SOURCE` | `live` | `sample`이면 샘플 데이터 |

방어의 규격은 `1미`, `2미`, `3미`, `3/4미`, `5/10미`처럼 표기됩니다. `data/raw`의 원본을 보고 원하는 크기만 `ITEM_SIZES`에 넣으면 됩니다. 기간·비율은 `pipeline.py` 상단의 `WINDOW_DAYS`, `LOWER_RATIO`, `UPPER_RATIO`로 조정합니다.
