# -*- coding: utf-8 -*-
"""신한은행 거래내역조회 .xls -> data/bank_txns.json (가계부와 같은 관리기간/분류 축).

사용: python scripts/parse_bank.py [엑셀경로]
분류 규칙을 바꾸려면 CATEGORY_RULES 만 고치면 된다.
"""
import json
import re
import sys
from pathlib import Path

import xlrd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_XLS = ROOT / "신한은행_거래내역조회_20260904072434.xls"
OUT = ROOT / "data" / "bank_txns.json"

# 가계부 관리기간: 매월 24일 ~ 다음달 23일 (index.html 기준)
PERIODS = [
    ("2026-06-24", "2026-07-23", "7월 (06.24~07.23)"),
    ("2026-07-24", "2026-08-23", "8월 (07.24~08.23)"),
    ("2026-08-24", "2026-09-23", "9월 (08.24~09.23)"),
]

# (정규식, 대상 flow, category, subcategory) — 위에서부터 먼저 맞는 규칙 적용.
# 매칭 대상 문자열은 "날짜 적요 내용" 이라, 특정일 건만 잡는 규칙도 쓸 수 있다.
# flow: 'out' 출금 / 'in' 입금 / None 둘 다
CATEGORY_RULES = [
    # --- 출금 ---
    # 07-24 토스페이 3건은 KTX 예매(2건은 즉시 취소·환불). 같은 날 한국철도공사 환급과 짝.
    (r"2026-07-24.*토스페이", "out", "출장비", "KTX 예매"),
    (r"동백전충전|자동충전", "out", "공금", "지역화폐충전"),
    (r"KIA 한재림", "out", "공금", "계약금"),
    (r"카카오페이", "out", "고정지출", "카카오페이"),
    # KB·신한은 명세서(clean_txns.json)로 건별 분류되므로 정산 시 중복 제외 대상.
    (r"신한카드|ＫＢ카드출금|KB카드출금", "out", "고정지출", "카드결제(명세서)"),
    # 나머지 3장은 명세서가 없어 용도로 직접 분류한다.
    (r"롯데카드", "out", "공금", "노트북 할부"),
    (r"하나카드", "out", "공금", "마켓컬리"),
    (r"현대카드", "out", "공금", "하이패스"),
    (r"삼성화|삼성생명|한화손", "out", "고정지출", "보험료"),
    (r"감만종합사회복", "out", "공금", "장난감 대여료"),
    # --- 입금 ---
    (r"급여|월초수당|초과근무수당|초과수당", "in", "공금", "급여/수당"),
    (r"한국철도공사", "in", "출장비", "KTX 환급"),
    (r"기획감사실", "in", "출장비", "출장비 수령"),
    (r"삼성생보험금", "in", "고정지출", "실비보험금 수령"),
    (r"AI러닝크루", "in", "공금", "포상금"),
    (r"고정지출비", "in", "고정지출", "고정지출비 입금"),
]

# 적요가 '이자'인 건은 내용이 기간문자열이라 별도 처리
INTEREST_TYPE = "이자"


def load_rows(xls_path):
    """엑셀에서 헤더(거래일자) 행을 찾아 그 아래 데이터만 돌려준다."""
    sheet = xlrd.open_workbook(str(xls_path)).sheet_by_index(0)
    header = next(
        r for r in range(sheet.nrows) if str(sheet.cell_value(r, 0)).strip() == "거래일자"
    )
    return [
        [str(c.value).strip() for c in sheet.row(r)] for r in range(header + 1, sheet.nrows)
    ]


def to_period(date):
    for start, end, label in PERIODS:
        if start <= date <= end:
            return label
    return "기간외"


def classify(date, txn_type, desc, flow):
    if txn_type == INTEREST_TYPE:
        return "공금", "이자"
    text = f"{date} {txn_type} {desc}"
    for pattern, want_flow, category, subcategory in CATEGORY_RULES:
        if want_flow in (None, flow) and re.search(pattern, text):
            return category, subcategory
    return "확인필요", "개인이체" if flow == "out" else "기타입금"


def parse(xls_path):
    txns = []
    for row in load_rows(xls_path):
        date, time, txn_type, out, inn, desc, balance = row[:7]
        out, inn = int(float(out or 0)), int(float(inn or 0))
        flow = "in" if inn > 0 else "out"
        category, subcategory = classify(date, txn_type, desc, flow)
        txns.append(
            {
                "date": date,
                "time": time,
                "type": txn_type,
                "desc": desc,
                "out": out,
                "in": inn,
                "balance": int(float(balance or 0)),
                "flow": "입금" if flow == "in" else "출금",
                "period": to_period(date),
                "category": category,
                "subcategory": subcategory,
            }
        )
    return txns


def demo():
    """경계·분류 규칙이 깨지면 실패하는 최소 검증."""
    assert to_period("2026-06-23") == "기간외"
    assert to_period("2026-06-24") == "7월 (06.24~07.23)"
    assert to_period("2026-07-23") == "7월 (06.24~07.23)"
    assert to_period("2026-07-24") == "8월 (07.24~08.23)"
    assert to_period("2026-08-23") == "8월 (07.24~08.23)"
    assert to_period("2026-08-24") == "9월 (08.24~09.23)"
    d = "2026-08-01"
    assert classify(d, "FB카드", "ＫＢ카드출금", "out") == ("고정지출", "카드결제(명세서)")
    assert classify(d, "FB자동", "롯데카드(주)", "out") == ("공금", "노트북 할부")
    assert classify(d, "FB카드", "하나카드", "out") == ("공금", "마켓컬리")
    assert classify(d, "펌뱅킹 이체", "한재림현대카드", "out") == ("공금", "하이패스")
    assert classify(d, "오픈뱅킹 이체", "동백전충전", "out") == ("공금", "지역화폐충전")
    assert classify(d, "펌뱅킹 이체", "카카오페이", "out") == ("고정지출", "카카오페이")
    assert classify(d, "인터넷뱅킹", "8월급여", "in") == ("공금", "급여/수당")
    assert classify(d, "펌뱅킹 이체", "한국철도공사_", "in") == ("출장비", "KTX 환급")
    assert classify(d, "인터넷뱅킹", "기획감사실", "in") == ("출장비", "출장비 수령")
    # 규칙에 안 걸리는 인명 이체는 확인필요로 떨어져야 한다 (이름은 임의값)
    assert classify(d, "펌뱅킹 이체", "홍길동", "out") == ("확인필요", "개인이체")
    # 토스페이는 날짜로 갈린다: 07-24만 KTX 예매, 07-06은 별개 건
    assert classify("2026-07-24", "펌뱅킹 이체", "토스페이", "out") == ("출장비", "KTX 예매")
    assert classify("2026-07-06", "펌뱅킹 이체", "토스페이", "out") == ("확인필요", "개인이체")
    # 잔액 연속성: 출금/입금이 잔액 변화와 맞아야 원본을 옳게 읽은 것
    txns = parse(DEFAULT_XLS)
    for newer, older in zip(txns, txns[1:]):
        assert older["balance"] - newer["out"] + newer["in"] == newer["balance"], newer
    print(f"demo ok ({len(txns)}건, 잔액 연속성 확인)")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        xls = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLS
        txns = parse(xls)
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(json.dumps(txns, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(txns)}건 -> {OUT}")
