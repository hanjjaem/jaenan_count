# -*- coding: utf-8 -*-
"""카드 결제일 기준 월 정산 청구서.

정산 단위 = 이번 카드결제일에 나간 카드값 + 직전 결제일 다음날~이번 결제일의 통장 직접지출.
카드 사용분은 카드사 명세기간이, 통장 직접분은 결제일컷이 기간을 정한다.

사용: python scripts/settle.py [9/21] [--kakao] [--demo]
새 달을 추가하려면 SETTLEMENTS 에 한 줄 넣고, 그 달 카드 명세서 JSON 을 만들면 된다.
"""
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "data" / "bank_txns.json"

# key: 결제회차(웹앱 bill 필드와 같은 값)
# card: 카드 명세서 JSON, prev: 직전 카드 결제일, pay: 이번 카드 결제일
# periods: 카드사 명세 이용기간 (표시용)
SETTLEMENTS = {
    "8/21분": {
        "card": ROOT / "clean_txns.json",
        "prev": "2026-07-21", "pay": "2026-08-21",
        "periods": {"국민카드": "07.13 ~ 08.06", "신한카드": "07.08 ~ 08.07"},
    },
    "9/21분": {
        "card": ROOT / "data" / "card_2609.json",
        "prev": "2026-08-21", "pay": "2026-09-21",
        "periods": {"국민카드": "08.07 ~ 09.07", "신한카드": "08.08 ~ 09.07"},
        # 같은 날 같은 적요가 여럿이라 분류 규칙으로 못 가르는 건 — (날짜, 적요, 금액)으로 지정
        # 9/12 충전분은 9/21 축의금(MANUAL 5만원)의 재원 — 축의금으로 한 번만 센다
        "reclass": [("2026-09-12", "카카오페이", 43115, "제외", "축의금 재원 충전")],
    },
}
LATEST = list(SETTLEMENTS)[-1]

# 통장·카드 어디에도 안 찍힌 지출 (카카오페이 머니 잔액 결제 등).
# 날짜로 회차가 정해진다 — 아직 없는 회차 몫은 그 회차를 SETTLEMENTS 에 추가하면 자동 포함.
MANUAL = [
    {"date": "2026-09-21", "desc": "축의금 (카카오페이 머니)", "amount": 50000,
     "category": "공금", "subcategory": "경조사비"},
    # ↓ 10/21 결제분(09.22~10.21) 몫
    {"date": "2026-09-26", "desc": "조카 용돈 (카카오페이 머니)", "amount": 50000,
     "category": "공금", "subcategory": "경조사비"},
]

BILLABLE = ("공금", "고정지출")  # 와이프 청구 대상
# 통장 행 중 웹앱·청구서에 올리는 분류. '확인필요'(개인 간 이체)는 제3자 실명이 있어 제외.
BANK_SHOWN = BILLABLE + ("내 용돈", "출장비")
CARD_OUT = "카드결제(명세서)"


def window(key):
    """통장 직접분 구간: 직전 결제일 다음날 ~ 이번 결제일 (경계가 겹치지 않게)."""
    s = SETTLEMENTS[key]
    lo = date.fromisoformat(s["prev"]) + timedelta(days=1)
    return lo.isoformat(), s["pay"]


def load_bank():
    return json.load(open(BANK, encoding="utf-8"))


def card_rows(key):
    """이번 결제일에 청구된 카드 명세서 건만 (clean_txns.json 의 직전 명세서 건은 bill 로 걸러짐)."""
    rows = json.load(open(SETTLEMENTS[key]["card"], encoding="utf-8"))
    return [x for x in rows if x.get("bill", key) == key]


def bank_rows(key, bank):
    """정산구간의 통장 직접지출. KB·신한은 명세서로 세므로 제외. KTX 환급은 음수로 상계."""
    s = SETTLEMENTS[key]
    reclass = {(d, desc, amt): (cat, sub) for d, desc, amt, cat, sub in s.get("reclass", [])}
    lo, hi = window(key)
    out = []
    for x in bank:
        if not lo <= x["date"] <= hi or x["subcategory"] == CARD_OUT:
            continue
        cat, sub = reclass.pop((x["date"], x["desc"], x["out"]), (x["category"], x["subcategory"]))
        x = {**x, "category": cat, "subcategory": sub}
        if x["out"] > 0 and x["category"] in BANK_SHOWN:
            out.append({**x, "amount": x["out"]})
        elif x["in"] > 0 and x["subcategory"] == "KTX 환급":
            out.append({**x, "amount": -x["in"]})
    assert not reclass, f"재분류 대상이 통장에 없음: {list(reclass)}"
    for m in MANUAL:
        if lo <= m["date"] <= hi:
            out.append({"out": m["amount"], "in": 0, "manual": True, **m})
    return sorted(out, key=lambda x: x["date"])


def received(key, bank):
    """정산구간에 이미 받은 고정지출비."""
    lo, hi = window(key)
    return sum(x["in"] for x in bank
               if lo <= x["date"] <= hi and x["subcategory"] == "고정지출비 입금")


def card_paid(key, bank):
    """결제일에 통장에서 실제로 빠진 카드값 {카드사: 금액}."""
    pay = SETTLEMENTS[key]["pay"]
    paid = {}
    for x in bank:
        if x["date"] == pay and x["subcategory"] == CARD_OUT:
            paid["신한카드" if "신한" in x["desc"] else "국민카드"] = x["out"]
    return paid


def settle(key=LATEST, bank=None):
    bank = bank if bank is not None else load_bank()
    card, direct = card_rows(key), bank_rows(key, bank)

    # 명세서 합계가 통장에서 실제로 빠진 카드값과 맞는지 — 틀리면 명세서가 어긋난 것
    gap = sum(x["billed"] for x in card) - sum(card_paid(key, bank).values())

    lines = Counter()
    for cat, sub, amt in [(x["category"], x["subcategory"], x["billed"]) for x in card] + \
                         [(x["category"], x["subcategory"], x["amount"]) for x in direct]:
        if cat in BILLABLE:
            lines[(cat, sub)] += amt

    def total(cat):
        return (sum(x["billed"] for x in card if x["category"] == cat) +
                sum(x["amount"] for x in direct if x["category"] == cat))

    총액 = sum(lines.values())
    받음 = received(key, bank)
    return lines, 총액, 받음, 총액 - 받음, total("내 용돈"), total("출장비"), gap


def render(key=LATEST, kakao=False):
    lines, 총액, 받음, 청구, 본인, 출장, gap = settle(key)
    lo, hi = window(key)
    out = [f"[{key[:-1]} 카드결제 정산] 카드 명세서 + 통장 {lo[5:].replace('-', '/')}~{hi[5:].replace('-', '/')} 출금", ""]
    for cat in BILLABLE:
        items = sorted(((s, v) for (c, s), v in lines.items() if c == cat), key=lambda i: -i[1])
        out.append(f"■ {cat} {sum(v for _, v in items):,}원")
        out += [f"  {s:14s} {v:>9,}" for s, v in items]
        out.append("")
    out += [
        f"합계 {총액:,}원",
        f"기수령 고정지출비 -{받음:,}원",
        "━" * 20,
        f"청구액 {청구:,}원",
        "",
        f"※ 내 용돈 {본인:,}원, 출장비 {출장:,}원 제외",
    ]
    text = "\n".join(out)
    if not kakao and gap:
        text += f"\n\n[검증] 명세서 합계가 통장 카드출금과 {gap:+,}원 차이"
    return text


def demo():
    bank = load_bank()
    expect = {"8/21분": (1840097, 1440097), "9/21분": (1822527, 1422527)}
    for key, (총, 청) in expect.items():
        _, 총액, 받음, 청구, _, _, gap = settle(key, bank)
        assert (총액, 받음, 청구) == (총, 400000, 청), (key, 총액, 받음, 청구)
        assert abs(gap) < 200, (key, gap)  # 신한 원단위 반올림 오차만 허용
        print(f"demo ok {key} (청구액 {청구:,}원, 명세서-통장 오차 {gap:+,}원)")
    assert window("9/21분") == ("2026-08-22", "2026-09-21")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    key = args[0] if args else LATEST
    key = key if key.endswith("분") else key + "분"
    if "--demo" in sys.argv:
        demo()
    else:
        print(render(key, kakao="--kakao" in sys.argv))
