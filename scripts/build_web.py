# -*- coding: utf-8 -*-
"""settle.py 의 정산회차 데이터를 index.html 에 임베드한다.

사용: python scripts/build_web.py
index.html 의 `/*DATA:START*/ ... /*DATA:END*/` 사이만 교체한다.
"""
import hashlib
import json
import re

import settle

HTML = settle.ROOT / "index.html"
MARK = re.compile(r"/\*DATA:START\*/.*?/\*DATA:END\*/", re.S)


def dot(d):
    return d.replace("-", ".")


def build(key, bank):
    s = settle.SETTLEMENTS[key]
    tag = key[:-1].replace("/", "").zfill(4)  # '9/21분' -> '0921'
    rows = []
    for i, x in enumerate(settle.card_rows(key), 1):
        rows.append({
            "id": f"{tag}-c{i:03d}", "src": x["card"], "date": x["date"],
            "merchant": x["merchant"], "billed": x["billed"],
            "category": x["category"], "subcategory": x["subcategory"],
            "reason": x.get("reason", ""),
        })
    for i, x in enumerate(settle.bank_rows(key, bank), 1):
        rows.append({
            "id": f"{tag}-b{i:03d}", "src": "통장", "date": dot(x["date"]),
            "merchant": x["desc"], "billed": x["amount"],
            "category": x["category"], "subcategory": x["subcategory"],
            "reason": ("수기 입력 — 통장 기록 없음" if x.get("manual") else
                       "통장 직접 출금" if x["amount"] > 0 else "KTX 취소 환급 (상계)"),
        })
    paid = settle.card_paid(key, bank)
    cards = {}
    for name in ("국민카드", "신한카드"):
        mine = [r for r in rows if r["src"] == name]
        cards[name] = {"period": s["periods"][name], "paid": paid.get(name, 0),
                       "count": len(mine), "total": sum(r["billed"] for r in mine)}
    lo, hi = settle.window(key)
    recv_dates = [x["date"] for x in bank
                  if lo <= x["date"] <= hi and x["subcategory"] == "고정지출비 입금"]
    return {
        "key": key, "pay": dot(s["pay"]), "window": [dot(lo), dot(hi)],
        "received": settle.received(key, bank),
        "receivedDate": dot(recv_dates[0]) if recv_dates else "",
        "cards": cards, "rows": rows,
    }


def main():
    bank = settle.load_bank()
    data = [build(k, bank) for k in settle.SETTLEMENTS]
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    version = hashlib.sha1(blob.encode()).hexdigest()[:8]
    js = f"/*DATA:START*/\n    const SETTLEMENTS = {blob};\n    const DATA_VERSION = '{version}';\n    /*DATA:END*/"
    html = HTML.read_text(encoding="utf-8")
    assert MARK.search(html), "index.html 에 DATA 마커가 없음"
    HTML.write_text(MARK.sub(lambda _: js, html), encoding="utf-8")
    for d in data:
        print(f"{d['key']}: {len(d['rows'])}건 임베드")
    print(f"DATA_VERSION {version}")


if __name__ == "__main__":
    main()
