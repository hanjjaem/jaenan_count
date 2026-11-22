# -*- coding: utf-8 -*-
"""KB국민카드 전자문서 XLSX(이용대금명세서) -> data/card_YYMM.json

KB 전자문서에서 내려받은 .xlsx 는 openpyxl 이 스타일시트 때문에 열지 못한다.
그래서 zip 안의 xl/worksheets/sheet1.xml 을 직접 읽는다(비밀번호 없음).

사용:
  python scripts/parse_kb_card.py download.xlsx 10/21
  python scripts/parse_kb_card.py download.xlsx 11/21
출력: data/card_2610.json, data/card_2611.json (clean_txns.json 과 같은 포맷)
"""
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# (정규식, category, subcategory, reason) — 위에서부터 먼저 맞는 규칙 적용
RULES = [
    (r"KT통신요금", "고정지출", "통신비", "알뜰폰 통신요금 자동납부 (제휴할인 반영)"),
    (r"KB아파트관리비", "고정지출", "관리비", "KB아파트 관리비 정기납부"),
    (r"부산도시가스", "고정지출", "공과금", "부산도시가스 요금 자동납부"),
    (r"e 하이패스|연회비", "고정지출", "연회비", "KB e하이패스(기아) 카드 연회비"),
    (r"쿠팡이츠", "공금", "배달식비", "쿠팡이츠 가족 공동 배달 식사"),
    (r"쿠팡\(쿠페이\)", "공금", "쿠팡장보기", "쿠팡 생필품/식재료 온라인 장보기"),
    (r"쿠팡\(와우멤버십\)", "공금", "정기구독", "쿠팡 와우멤버십 월정액 구독"),
    (r"배민클럽", "공금", "정기구독", "배민클럽 배달 멤버십 월정액 구독"),
    (r"우아한형제들", "공금", "배달식비", "배달의민족 가족 공동 식사 배달"),
    (r"씨유|이마트24", "내 용돈", "편의점", "CU/편의점 소액 지출"),
    (r"주차장|부산항시설관리", "공금", "차량유지", "주차요금"),
    (r"플러그링크|전기차", "공금", "차량유지", "전기차 충전 (플러그링크)"),
    (r"현대서비스", "공금", "차량유지", "차량 정비 및 점검"),
    (r"이마트|메가마트|정육|롯데쇼핑", "공금", "마트장보기", "대형마트/온라인 식료품 장보기"),
    (r"신세계백화점", "공금", "식음/쇼핑", "백화점 공동 소비"),
    (r"무신사|29CM", "공금", "의류/패션", "의류 구매 (공금)"),
    (r"오늘의집", "공금", "생활용품", "생활용품 구매 (오늘의집)"),
    (r"아성다이소", "공금", "생활용품", "아성다이소 생활잡화 구매"),
    (r"남구도서관", "내 용돈", "도서/문화", "남구도서관 소액 이용료"),
    (r"약국", "내 용돈", "의료/약국", "약국 의약품 구매"),
    (r"부산아동병원", "공금", "의료/병원", "아이 병원 진료비"),
    (r"동구청", "공금", "공과금", "동구청 납부 (항목 미확인)"),
    (r"영도구청", "공금", "공과금", "영도구청 납부 (항목 미확인)"),
    (r"한국우편사업진흥원", "공금", "생활용품", "우체국 결제 (품목 미확인)"),
    (r"네이버페이", "공금", "마트장보기", "네이버페이 결제 (품목 미확인)"),
    (r"토스페이_일반", "공금", "온라인쇼핑", "토스페이 결제 (품목 미확인)"),
]
CONFIRM = {"동구청", "영도구청", "한국우편사업진흥원", "부산아동병원"}


def period_label(d):
    """가계부 관리기간(24일컷) 라벨."""
    m, y = d.month, d.year
    if d.day >= 24:
        start = date(y, m, 24)
        nm = date(y + (m == 12), (m % 12) + 1, 23)
        label_month = m % 12 + 1
    else:
        py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
        start, nm = date(py, pm, 24), date(y, m, 23)
        label_month = m
    return f"{label_month}월 ({start:%m.%d}~{nm:%m.%d})"


def read_rows(xlsx):
    with zipfile.ZipFile(xlsx) as z:
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    out = []
    for row in root.iter(NS + "row"):
        cells = {}
        for c in row.iter(NS + "c"):
            ref = c.get("r")
            v, inline = c.find(NS + "v"), c.find(NS + "is")
            if c.get("t") == "inlineStr" and inline is not None:
                cells[ref] = "".join(t.text or "" for t in inline.iter(NS + "t"))
            elif v is not None:
                cells[ref] = v.text
        out.append((row.get("r"), cells))
    return out


def cell(cells, col, r):
    return (cells.get(f"{col}{r}") or "").strip()


def parse(xlsx, bill):
    rows = read_rows(xlsx)
    txns, last = [], None
    for r, cells in rows:
        use = cell(cells, "B", r)
        merchant = cell(cells, "E", r)
        amount = cell(cells, "G", r)
        billed = cell(cells, "I", r)
        if not use and amount.startswith("-"):  # 할인 줄 — 직전 건에 합산
            if last is not None:
                last["discount"] += -int(amount)
            continue
        if not re.match(r"^\d{2}\.\d{2}\.\d{2}$", use):
            continue
        d = date(2000 + int(use[:2]), int(use[3:5]), int(use[6:8]))
        cat = sub = reason = None
        for pat, c_, s_, why in RULES:
            if re.search(pat, merchant):
                cat, sub, reason = c_, s_, why
                break
        if cat is None:
            cat, sub, reason = "확인필요", "미분류", "분류 규칙에 없는 가맹점"
        last = {
            "card": "국민카드",
            "date": d.isoformat().replace("-", "."),
            "merchant": merchant,
            "amount": int(amount),
            "billed": int(billed or amount),
            "period": period_label(d),
            "category": cat,
            "subcategory": sub,
            "reason": reason,
            "note": "",
            "bill": bill,
            "_confirm": any(k in merchant for k in CONFIRM),
            "discount": 0,
        }
        txns.append(last)
    return txns


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    xlsx, bill = Path(sys.argv[1]), sys.argv[2]
    bill = bill if bill.endswith("분") else bill + "분"
    txns = parse(xlsx, bill)
    for t in txns:
        if t["discount"]:
            t["note"] = f"할인 -{t['discount']:,}"
        t.pop("discount", None)
        t.pop("_confirm", None)
    month = int(bill.split("/")[0])
    out = ROOT / "data" / f"card_26{month:02d}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(txns, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(txns)}건, 합계 {sum(t['billed'] for t in txns):,}원 -> {out}")


if __name__ == "__main__":
    main()
