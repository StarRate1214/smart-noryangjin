# smart-noryangjin

노량진 수산시장 경매 시세를 매일 수집해 최근 30일 등락 추이와 **고가 대비 70~80% 가성비 매수 구간**을 Plotly 대화형 차트로 보여주는 개인 대시보드입니다. 차트에 마우스를 올리면 일자별 최고가·80%선·70%선이 한 번에 표시됩니다.

## 구성

| 파일 | 역할 |
|------|------|
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

## 실제 시세 연결

현재 `fetch_live()`는 비어 있고 기본 소스는 결정적 샘플 데이터입니다(페이지 상단에 샘플 안내가 표시됩니다). 실제 수집으로 바꾸려면 다음 순서를 따릅니다.

1. `pipeline.py`의 `fetch_live(date)`에서 노량진수산물도매시장 또는 인어교주해적단의 해당 일자 1kg당 경매 최고가를 정수로 반환하도록 구현합니다. 휴장일처럼 값이 없으면 `None`을 반환합니다.
2. **Settings → Secrets and variables → Actions → Variables**에 `DATA_SOURCE=live`를 추가합니다.

`live`로 전환하면 기존 샘플 기록은 자동으로 제외되고 실제 수집값만 누적됩니다. 품목명·기간·비율은 `pipeline.py` 상단의 `ITEM_NAME`, `WINDOW_DAYS`, `LOWER_RATIO`, `UPPER_RATIO`로 조정합니다.
