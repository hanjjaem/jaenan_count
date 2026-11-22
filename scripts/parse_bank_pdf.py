# -*- coding: utf-8 -*-
"""신한은행 거래내역조회 PDF -> data/bank_txns.json (parse_bank.py 와 같은 분류 축).

PDF 는 표를 텍스트로 그리므로 좌표(x)로 열을 가른다.
    거래일자 | 거래시간 | 적요 | 출금 | 입금 | 내용 | 잔액 | 거래점
출금/입금 숫자는 각 열에 오른쪽 정렬되어 있어 x1(오른쪽 끝)로 구분한다.

사용: python scripts/parse_bank_pdf.py <pdf경로> --password <생년월일6자리>
비밀번호는 인자/환경변수(BANK_PDF_PW)로만 받고 파일에 남기지 않는다.
"""
import json
import os
import re
import sys
from pathlib import Path

import pymupdf

from parse_bank import to_period, classify

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "bank_txns.json"

# 열 경계 (PDF 좌표, 단위 pt). 숫자는 열별 오른쪽 정렬 위치로 가른다.
X_DATE, X_TIME, X_TYPE, X_CONTENT, X_BAL, X_BRANCH = 95, 160, 225, 356, 450, 516
OUT_RIGHT, IN_RIGHT = 292, 358   # 출금 x1 <= 292 < 입금 x1 <= 358
FOOTER_Y = 770                   # 이 아래는 저작권/안내문
HEADER = {"거래일자", "거래시간", "적요", "출금(원)", "입금(원)", "내용", "잔액(원)", "거래점"}


def pages_words(doc):
    """페이지별 (표 헤더 y, 단어목록) — 표 아래 본문만 돌려준다."""
    for page in doc:
        words = page.get_text("words")
        head_y = None
        for w in words:
            if w[4] == "거래일자":
                head_y = w[3]
                break
        if head_y is None:
            continue
        body = [
            w for w in words
            if w[1] > head_y + 4 and w[1] < FOOTER_Y and w[4] not in HEADER
        ]
        yield body


def col_of(w):
    x0, x1 = w[0], w[2]
    if x0 < X_DATE:
        return "date"
    if x0 < X_TIME:
        return "time"
    if x0 < X_TYPE:
        return "type"
    if x0 < X_CONTENT:
        return "out" if x1 <= OUT_RIGHT else "in"
    if x0 < X_BAL:
        return "content"
    if x0 < X_BRANCH:
        return "balance"
    return "branch"


def num(s):
    s = s.replace(",", "").strip()
    return int(s) if s.isdigit() else 0


def records(doc):
    """거래일자 행을 기준선으로 삼아, 각 단어를 가장 가까운 행에 붙인다.

    적요·내용이 두 줄로 접히면 첫 줄이 날짜줄보다 살짝 위(y-5pt)에 찍혀서
    y 순서로만 자르면 앞 거래에 붙는다. 그래서 날짜 셀의 세로 중심과의 거리로 붙인다.
    """
    out = []
    for words in pages_words(doc):
        dates = [w for w in words
                 if col_of(w) == "date" and re.fullmatch(r"20\d{6}", w[4])]
        if not dates:
            continue
        centers = [(w[1] + w[3]) / 2 for w in dates]
        recs = [{"date": w[4], "time": "", "type": "", "out": "", "in": "",
                 "content": "", "balance": "", "branch": ""} for w in dates]
        for w in sorted(words, key=lambda w: (w[1], w[0])):
            if col_of(w) == "date" and re.fullmatch(r"20\d{6}", w[4]):
                continue
            y = (w[1] + w[3]) / 2
            i = min(range(len(centers)), key=lambda k: abs(centers[k] - y))
            if abs(centers[i] - y) > 12:      # 행 사이에 떨어진 조각은 버린다
                continue
            key = col_of(w)
            recs[i][key] = (recs[i][key] + w[4]) if key in ("type", "content") else w[4]
        out += recs
    return out


def parse(pdf_path, password=None):
    doc = pymupdf.open(str(pdf_path))
    if doc.needs_pass:
        assert password, "PDF 에 암호가 걸려 있습니다 (--password)"
        assert doc.authenticate(password), "PDF 암호가 맞지 않습니다"
    txns = []
    for r in records(doc):
        out_, in_ = num(r["out"]), num(r["in"])
        date = f"{r['date'][:4]}-{r['date'][4:6]}-{r['date'][6:]}"
        flow = "in" if in_ > 0 else "out"
        category, subcategory = classify(date, r["type"], r["content"], flow)
        txns.append({
            "date": date,
            "time": r["time"],
            "type": r["type"],
            "desc": r["content"],
            "out": out_,
            "in": in_,
            "balance": num(r["balance"]),
            "flow": "입금" if flow == "in" else "출금",
            "period": to_period(date),
            "category": category,
            "subcategory": subcategory,
            "branch": r["branch"],
        })
    return txns


def check_balance(txns):
    """인접 거래의 잔액이 출금/입금과 맞아야 PDF 를 옳게 읽은 것."""
    bad = []
    for newer, older in zip(txns, txns[1:]):
        if older["balance"] - newer["out"] + newer["in"] != newer["balance"]:
            bad.append((newer, older))
    return bad


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pw = None
    if "--password" in sys.argv:
        pw = sys.argv[sys.argv.index("--password") + 1]
    pw = pw or os.environ.get("BANK_PDF_PW")
    pdf = Path(args[0]) if args else None
    assert pdf, "사용: python scripts/parse_bank_pdf.py <pdf> --password <6자리>"
    txns = parse(pdf, pw)
    bad = check_balance(txns)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(txns, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(txns)}건 -> {OUT}")
    print(f"기간 {txns[-1]['date']} ~ {txns[0]['date']}")
    if bad:
        print(f"!! 잔액 불일치 {len(bad)}건")
        for n, o in bad[:5]:
            print("  ", n["date"], n["type"], n["desc"], n["out"], n["in"], n["balance"], "| 이전", o["balance"])
    else:
        print("잔액 연속성 OK")
