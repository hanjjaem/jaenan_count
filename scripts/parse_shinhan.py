# -*- coding: utf-8 -*-
"""신한카드 이용대금명세서(엑셀 저장 = HTML 표) → data/shinhan_YYMM.json + card_YYMM.json 합산.

사용:
    python scripts/parse_shinhan.py "../../downloads/신한카드_2026년10월_이용대금명세서.xls" \
                                    "../../downloads/신한카드_2026년11월_이용대금명세서.xls"

동작:
  1) 명세서 XML/HTML 표를 읽어 이용건마다 card_*.json 포맷(dict)으로 변환
     · amount = 이용금액, billed = 이번달 납부금액 원금 + 수수료(해외이용 수수료 포함)
     · 할인 적용 건은 note 에 "할인 -N" / "무이자할부" 를 남긴다
  2) data/shinhan_YYMM.json 저장
  3) data/card_YYMM.json 이 있으면 기존 카드사 행을 보존한 채 '신한카드' 행만 교체·합산
  4) 검증: 합계가 명세서 총액(입금할 금액)과 원 단위로 일치하는지 출력
"""
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 가맹점 → (대분류, 소분류, 사유). 왼쪽부터 부분일치로 찾는다(위에서부터 우선).
RULES = [
    ("페이타랩", "내 용돈", "카페/커피", "커피/음료 주문 (패스오더 등)"),
    ("컴포즈커피", "내 용돈", "카페/커피", "커피/음료 주문 (컴포즈커피)"),
    ("메가엠지씨커피", "내 용돈", "카페/커피", "커피/음료 주문 (메가MGC커피)"),
    ("씨유", "내 용돈", "편의점", "CU 편의점 소액 지출"),
    ("CU", "내 용돈", "편의점", "CU 편의점 소액 지출"),
    ("지에스", "내 용돈", "편의점", "GS25 편의점 소액 지출"),
    ("동구청", "공금", "공과금", "동구청 납부 (항목 미확인)"),
    ("지마켓", "공금", "마트장보기", "온라인 장보기 (지마켓)"),
    ("무신사", "공금", "의류/패션", "의류 구매 (공금)"),
    ("이랜드월드", "공금", "의류/패션", "가족 의류 구매 (이랜드월드)"),
    ("네이버페이", "공금", "마트장보기", "네이버페이 결제 (품목 미확인)"),
    ("롯데쇼핑", "공금", "마트장보기", "롯데쇼핑 장보기"),
    ("모두마트", "공금", "마트장보기", "동네마트 장보기"),
    ("KT통신요금", "고정지출", "통신비", "KT 알뜰폰 통신요금 월납"),
    ("OPENAI", "공금", "정기구독", "ChatGPT Plus 월구독 정기결제"),
    ("ANTHROPIC", "공금", "정기구독", "Claude AI 월구독 정기결제"),
    ("CLOUDFLARE", "공금", "정기구독", "Cloudflare 유료플랜 정기결제"),
    ("ALIEXPRESS", "공금", "생활용품", "알리익스프레스 구매"),
    ("삼성화재", "고정지출", "보험", "삼성화재 자동차보험 (5개월 무이자할부)"),
    ("예스이십사", "내 용돈", "도서/문화", "도서 구매 (예스24)"),
    ("에스케이일렉링크", "공금", "차량유지", "전기차 충전 (SK일렉링크)"),
    ("아이파킹", "공금", "차량유지", "주차요금 (아이파킹)"),
    ("11번가", "공금", "생활용품", "11번가 온라인 구매"),
    ("인터넷우체국", "공금", "생활용품", "우체국 온라인 결제"),
    ("토스페이먼츠", "공금", "생활용품", "토스페이먼츠 결제 (가맹점 미확인)"),
    ("민락회타운", "내 용돈", "식음료", "시장 소액 결제 (품목 미확인)"),
]
FALLBACK = ("내 용돈", "기타", "가맹점 성격 미확인 (분류 필요)")


def num(s):
    s = (s or "").replace(",", "").strip()
    if s in ("", "-"):
        return 0
    return int(float(s))


def rows_of(path):
    s = Path(path).read_text(encoding="utf-8", errors="replace")
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", s, re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
        vals = [" ".join(html.unescape(re.sub(r"<[^>]+>", "", c)).split()) for c in cells]
        if any(vals):
            out.append(vals)
    return out


def parse(path):
    rows = rows_of(path)
    title = rows[0][0]
    m = re.search(r"(\d{4})년(\d{1,2})월", title)
    y, mo = int(m.group(1)), int(m.group(2))

    pay_date = next(v[1] for v in rows if len(v) > 2 and v[0] == "신용카드" and re.match(r"\d{4}\.\d{2}\.\d{2}", v[1]))
    total = num(next(v[1] for v in rows if len(v) > 1 and v[0] == "입금할 금액"))
    period_line = [v for v in rows if v and v[0].startswith("이용일자별")][0][0]
    dates = re.findall(r"\d{4}\.\d{2}\.\d{2}", period_line)
    if len(dates) >= 2:
        use_from, use_to = dates[-2], dates[-1]
    else:
        tx_dates = sorted(v[0] for v in rows if re.match(r"^\d{4}\.\d{2}\.\d{2}$", v[0]))
        use_from, use_to = tx_dates[0], tx_dates[-1]

    bill = f"{int(pay_date[5:7])}/21분"
    period = f"{mo}월 ({use_from[5:]}~{use_to[5:]})"

    txns, skipped, section = [], [], "domestic"
    for v in rows:
        if len(v) > 1 and v[0].startswith("일시불 합계"):
            section = "overseas"
        if len(v) > 3 and re.match(r"^\d{4}\.\d{2}\.\d{2}$", v[0]) and len(v) >= 9:
            date, merchant = v[0], v[2]
            amount, inst_n, inst_seq = num(v[3]), v[4], v[5]
            won, fee = num(v[6]), num(v[7])
            billed = won + fee
            is_overseas = section == "overseas"
            if billed == 0:
                skipped.append(f"{date} {merchant} 이용금액 {amount:,}원(부분취소/승인취소)")
                continue
            cat, sub, reason = FALLBACK
            for key, c, s_, r in RULES:
                if key in merchant:
                    cat, sub, reason = c, s_, r
                    break
            note = []
            if inst_n:
                note.append(f"{inst_n}개월 {'무이자' if v[8] == '무이자' else ''}할부 {inst_seq}회차"
                            f" (원거래 {amount:,}, 남은잔액 {num(v[9]):,})")
                amount_field = amount
            else:
                amount_field = amount if not is_overseas or amount > 1000 else billed
                if amount - billed > 0:
                    note.append(f"할인 -{amount - billed:,}")
            if is_overseas:
                if amount > 1000:
                    note.append(f"해외이용 수수료 {fee}")
                else:
                    note.append(f"해외이용 USD {amount} · 수수료 {fee}")
            txns.append({
                "card": "신한카드",
                "date": date,
                "merchant": merchant,
                "amount": amount_field,
                "billed": billed,
                "period": period,
                "category": cat,
                "subcategory": sub,
                "reason": reason,
                "note": ", ".join(note),
                "bill": bill,
            })
    return {
        "month": f"{str(y)[2:]}{mo:02d}",
        "title": title,
        "pay_date": pay_date,
        "use_period": (use_from, use_to),
        "total": total,
        "skipped": skipped,
        "txns": txns,
    }


def main(paths):
    for p in paths:
        st = parse(p)
        key = st["month"]
        shin = ROOT / "data" / f"shinhan_{key}.json"
        shin.parent.mkdir(exist_ok=True)
        json.dump(st["txns"], open(shin, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

        s = sum(t["billed"] for t in st["txns"])
        print(f"\n[{st['title']}] 결제일 {st['pay_date']} · 이용기간 {st['use_period'][0]}~{st['use_period'][1]}")
        print(f"  건수 {len(st['txns'])} · 명세서 총액 {st['total']:,}원 · 파싱 합계 {s:,}원 → {'일치' if s == st['total'] else '불일치!'}")
        for sk in st["skipped"]:
            print(f"  · 0원 승인취소 제외: {sk}")

        card = ROOT / "data" / f"card_{key}.json"
        old = json.load(open(card, encoding="utf-8")) if card.exists() else []
        kept = [x for x in old if x["card"] != "신한카드"]
        merged = kept + st["txns"]
        json.dump(merged, open(card, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        by = {}
        for x in merged:
            by.setdefault(x["card"], [0, 0])
            by[x["card"]][0] += 1
            by[x["card"]][1] += x["billed"]
        print(f"  → {card.relative_to(ROOT)} : " + " / ".join(f"{k} {v[0]}건 {v[1]:,}원" for k, v in by.items())
              + f" · 총 {len(merged)}건 {sum(v[1] for v in by.values()):,}원")
        assert s == st["total"], "신한 합계 불일치"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
