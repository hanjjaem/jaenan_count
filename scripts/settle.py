# -*- coding: utf-8 -*-
"""카드 결제일 기준 월 정산 청구서.

정산 단위 = 이번 카드결제일에 나간 카드값 + 직전 결제일 다음날~이번 결제일의 통장 직접지출.
카드 사용분은 카드사 명세기간이, 통장 직접분은 결제일컷이 기간을 정한다.

사용: python scripts/settle.py [--kakao]
매달 SETTLE / PREV_SETTLE / NOT_THIS_STATEMENT 만 갱신하면 된다.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARD = ROOT / "clean_txns.json"
BANK = ROOT / "data" / "bank_txns.json"

SETTLE = "2026-08-21"  # 이번 카드 결제일 (KB·신한 공통, 매월 21일)
PREV_SETTLE = "2026-07-21"  # 직전 카드 결제일

# clean_txns.json 에 섞여 있는 '직전 명세서' 건 — (사용일, 청구액)으로 지정.
# 명세서를 이번 달치만 받아 쓰면 비워도 된다.
NOT_THIS_STATEMENT = [
    ("2026.07.09", 28370), ("2026.07.10", 22900),
    ("2026.07.12", 24000), ("2026.07.12", 16810),
    ("2026.07.30", 750),
]

BILLABLE = ("공금", "고정지출")  # 와이프 청구 대상


def load():
    card = json.load(open(CARD, encoding="utf-8"))
    bank = json.load(open(BANK, encoding="utf-8"))
    return card, bank


def statement(card):
    """이번 결제일에 청구된 카드 명세서 건만."""
    skip = set(NOT_THIS_STATEMENT)
    return [x for x in card if (x["date"], x["billed"]) not in skip]


def direct(bank):
    """정산구간의 통장 직접지출. KB·신한은 명세서로 세므로 제외."""
    lo = PREV_SETTLE[:8] + f"{int(PREV_SETTLE[8:]) + 1:02d}"
    return [
        x for x in bank
        if lo <= x["date"] <= SETTLE
        and x["out"] > 0
        and x["subcategory"] != "카드결제(명세서)"
    ]


def received(bank):
    """정산구간에 이미 받은 고정지출비."""
    lo = PREV_SETTLE[:8] + f"{int(PREV_SETTLE[8:]) + 1:02d}"
    return sum(
        x["in"] for x in bank
        if lo <= x["date"] <= SETTLE and x["subcategory"] == "고정지출비 입금"
    )


def settle():
    card, bank = load()
    stmt, dir_ = statement(card), direct(bank)

    # 명세서 합계가 통장에서 실제로 빠진 카드값과 맞는지 — 틀리면 명세서가 어긋난 것
    paid = sum(x["out"] for x in bank
               if x["date"] == SETTLE and x["subcategory"] == "카드결제(명세서)")
    gap = sum(x["billed"] for x in stmt) - paid

    lines = Counter()
    for x in stmt:
        if x["category"] in BILLABLE:
            lines[(x["category"], x["subcategory"])] += x["billed"]
    for x in dir_:
        if x["category"] in BILLABLE:
            lines[(x["category"], x["subcategory"])] += x["out"]

    총액 = sum(lines.values())
    받음 = received(bank)
    본인 = sum(x["billed"] for x in stmt if x["category"] == "내 용돈")
    출장 = sum(x["billed"] for x in stmt if x["category"] == "출장비")
    return lines, 총액, 받음, 총액 - 받음, 본인, 출장, gap


def render(kakao=False):
    lines, 총액, 받음, 청구, 본인, 출장, gap = settle()
    out = [f"[{SETTLE[5:].replace('-', '/')} 정산] 카드결제분 + 통장 직접지출", ""]
    for cat in BILLABLE:
        items = sorted(((s, v) for (c, s), v in lines.items() if c == cat),
                       key=lambda i: -i[1])
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
    lines, 총액, 받음, 청구, 본인, 출장, gap = settle()
    assert 총액 == 1840097, 총액
    assert 받음 == 400000, 받음
    assert 청구 == 1440097, 청구
    assert 본인 == 215590 and 출장 == 59400, (본인, 출장)
    assert abs(gap) < 200, gap  # 신한 원단위 반올림 오차만 허용
    print(f"demo ok (청구액 {청구:,}원, 명세서-통장 오차 {gap:+,}원)")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        print(render(kakao="--kakao" in sys.argv))
