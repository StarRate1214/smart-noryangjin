# smart-noryangjin

노량진 수산시장 경매 시세를 매일 수집해 최근 30일 등락 추이와 **고가 대비 70~80% 가성비 매수 구간**을 Plotly 대화형 차트로 보여주는 개인 대시보드입니다. 차트에 마우스를 올리면 일자별 최고가·80%선·70%선이 한 번에 표시됩니다.

## 구성

| 파일 | 역할 |
|------|------|
| `noryangjin.py` | 공식 홈페이지 어종별 경락시세 수집·해석 |
| `pipeline.py` | 수집 → `data/prices.csv` 누적 → 70/80% 목표가 계산 → `index.html` 생성 |
| `data/prices.csv` | 일별 경매 최고가 기록(같은 날짜는 덮어씀) |
| `index.html` | GitHub Pages가 서빙하는 대시보드 |
| `.github/workflows/update.yml` | 매일 07:00 KST 자동 실행 후 결과 커밋 |

## 로컬 실행

```bash
pip install -r requirements.txt
python pipeline.py          # 샘플 데이터로 index.html 생성
```

## 배포 (GitHub Pages)

1. 저장소 **Settings → Pages → Build and deployment**에서 Source를 `Deploy from a branch`, Branch를 `main` / `(root)`로 저장합니다.
2. **Actions** 탭에서 `Update market dashboard` 워크플로를 한 번 수동 실행(`Run workflow`)하면 이후 매일 07:00 KST에 갱신됩니다.

## 실제 시세 연결 (노량진수산물도매시장 공식 홈페이지)

`noryangjin.py`는 공식 홈페이지 **수산물가격정보 → 어종별경락시세**(`/nsis/miw/ko/info/miw3130`) 화면의 엑셀 다운로드 요청을 그대로 재현합니다.

1. 화면(`miw3130`)을 GET 해서 세션 쿠키를 받습니다.
2. `excel/miw3130`에 어종명(`kdfshNm`)과 조회일(`searchStartDe`, `searchEndDe`, `YYYY.MM.DD`)을 POST 합니다.
3. 받은 파일(.xls/.xlsx 또는 HTML 표)에서 `낙찰고가` 열을 찾아 숫자로 바꾸고, 어종명·규격 조건으로 거른 뒤 최고값을 그날의 최고가로 씁니다.
4. 원본 표는 `data/raw/YYYY-MM-DD.csv`에 남기고, 해석에 실패한 응답은 워크플로 아티팩트 `debug-response`로 올립니다.

### 켜는 방법

저장소 **Settings → Secrets and variables → Actions → Variables**에 아래 값을 넣습니다. `DATA_SOURCE`만 필수입니다.

| 변수 | 예시 | 의미 |
|------|------|------|
| `DATA_SOURCE` | `live` | 공식 홈페이지에서 수집 |
| `ITEM_NAME` | `방어` | 화면에 표시할 이름 |
| `SEARCH_NAME` | `방어` | 홈페이지 어종 검색어 |
| `NAME_CONTAINS` | `(활)` | 어종명에 이 글자가 들어간 행만 사용 |
| `ITEM_SIZES` | `소,중` | 이 규격만 사용(비우면 전체 규격 중 최고가) |

처음 한 번은 **Actions → Update market dashboard → Run workflow**에서 `source=live`, `backfill_days=30`으로 실행해 지난 30일을 채웁니다. 실행 로그에 날짜별 규격 목록과 단위가 찍히므로, 그 값을 보고 `ITEM_SIZES`를 정하면 됩니다.

`live`로 전환하면 기존 샘플 기록은 자동으로 제외됩니다. 기간·비율은 `pipeline.py` 상단의 `WINDOW_DAYS`, `LOWER_RATIO`, `UPPER_RATIO`로 조정합니다.
