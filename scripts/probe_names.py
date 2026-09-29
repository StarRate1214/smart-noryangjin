"""임시 조사: 검색어별로 공식 홈페이지가 돌려주는 어종 이름을 출력한다."""
import datetime
import time

import noryangjin

session = noryangjin.new_session()
dates = [datetime.date(2026, 9, d) for d in (18, 19, 21, 22)]
for term in ["", "돌돔", "전어", "황전어", "킹크랩", "크랩", "대게", "게"]:
    names = {}
    for date in dates:
        try:
            table = noryangjin.fetch_day(session, term, date)
        except noryangjin.FetchError as exc:
            print(f"[{term}] {date} FetchError {exc}")
            continue
        if not table.empty and "어종" in table.columns:
            for name, qty in table.groupby("어종")["수량"].sum().items():
                names[name] = names.get(name, 0) + qty
        time.sleep(1)
    print(f"[{term or '(빈 검색어)'}] {len(names)}종:", ", ".join(f"{k}({v:g})" for k, v in sorted(names.items())))
