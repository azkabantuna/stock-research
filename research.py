"""
SEC EDGAR 공식 API로 종목 리포트를 만드는 프로그램.

사용법:
    python research.py NVDA

결과:
    reports/NVDA_2026-10-09.md 같은 한국어 요약 리포트가 만들어집니다.

파이썬 기본 기능만 사용하므로 따로 설치할 패키지가 없습니다.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

# SEC 규칙: 모든 요청의 User-Agent에 "이름(또는 앱 이름) + 연락 가능한 이메일"을 넣어야 합니다.
# https://www.sec.gov/os/accessing-edgar-data
# 다른 이메일을 쓰고 싶으면 SEC_EMAIL 환경변수로 바꿀 수 있습니다.
CONTACT_EMAIL = os.environ.get("SEC_EMAIL", "2808happy@naver.com")
USER_AGENT = f"stock-research {CONTACT_EMAIL}"

NUM_8K = 5            # 가져올 최근 8-K 개수
INSIDER_DAYS = 90     # 내부자 거래 조회 기간(일)
MAX_FORM4 = 100       # Form 4를 너무 많이 받지 않도록 상한
REQUEST_DELAY = 0.15  # SEC 제한(초당 10회) 이하로 유지하기 위한 대기 시간(초)

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# 8-K 항목 번호 → 한국어 설명
ITEM_8K_KO = {
    "1.01": "중요 계약 체결",
    "1.02": "중요 계약 해지",
    "1.03": "파산 또는 법정관리",
    "1.04": "광산 안전 관련",
    "1.05": "중대한 사이버보안 사고",
    "2.01": "자산 인수·처분 완료",
    "2.02": "실적 발표(영업·재무 결과)",
    "2.03": "직접 금융 채무 발생",
    "2.04": "채무 조기 상환 사유 발생",
    "2.05": "구조조정·사업 철수 비용",
    "2.06": "자산 손상(감액)",
    "3.01": "상장폐지 통지·상장 요건 미달",
    "3.02": "미등록 주식 매각",
    "3.03": "주주 권리 변경",
    "4.01": "회계감사인 변경",
    "4.02": "과거 재무제표 신뢰 불가(정정)",
    "5.01": "경영권(지배권) 변경",
    "5.02": "임원·이사 선임/퇴임, 보수 계약",
    "5.03": "정관·회계연도 변경",
    "5.04": "직원 연금플랜 거래 일시 중지",
    "5.05": "윤리강령 변경·면제",
    "5.06": "쉘 회사 지위 변경",
    "5.07": "주주총회 투표 결과",
    "5.08": "이사 후보 추천 관련",
    "6.01": "ABS 정보",
    "6.02": "ABS 서비서·수탁자 변경",
    "6.03": "ABS 신용보강 변경",
    "6.04": "ABS 배당 미지급",
    "6.05": "ABS 증권법 공시",
    "7.01": "공정공시(Reg FD) 자료",
    "8.01": "기타 중요 사건",
    "9.01": "재무제표 및 첨부 서류",
}

# Form 4 거래 코드 → 한국어 설명
TX_CODE_KO = {
    "P": "장내 매수",
    "S": "장내 매도",
    "A": "회사로부터 주식 부여(보상)",
    "D": "회사에 반환·처분",
    "F": "세금 납부용 주식 공제",
    "I": "재량적 거래",
    "M": "옵션·RSU 행사/전환",
    "C": "파생증권 전환",
    "E": "단기 파생증권 만기",
    "H": "장기 파생증권 만기·취소",
    "O": "외가격 파생증권 행사",
    "X": "내가격 파생증권 행사",
    "G": "증여",
    "L": "소액 취득",
    "W": "상속·유언에 의한 취득/처분",
    "Z": "의결권 신탁",
    "J": "기타",
    "K": "주식 스왑 등",
    "U": "공개매수에 따른 처분",
    "V": "자발적 조기 신고",
}


# ---------------------------------------------------------------------------
# SEC 요청
# ---------------------------------------------------------------------------

def fetch(url):
    """SEC 서버에서 데이터를 받아옵니다(User-Agent 포함, 속도 제한 준수)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "identity",
    })
    for attempt in range(3):
        time.sleep(REQUEST_DELAY)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))  # 잠깐 쉬고 다시 시도
                continue
            if e.code == 403:
                raise RuntimeError(
                    "SEC가 요청을 거부했습니다(403). User-Agent 이메일이 올바른지, "
                    "너무 자주 실행하지 않았는지 확인하고 잠시 후 다시 시도하세요."
                ) from e
            raise
    raise RuntimeError(f"요청 실패: {url}")


def fetch_json(url):
    return json.loads(fetch(url).decode("utf-8"))


# ---------------------------------------------------------------------------
# 데이터 가져오기
# ---------------------------------------------------------------------------

def find_company(ticker):
    """티커(NVDA) → (CIK 번호, 회사 이름)."""
    data = fetch_json("https://www.sec.gov/files/company_tickers.json")
    for row in data.values():
        if row["ticker"].upper() == ticker.upper():
            return int(row["cik_str"]), row["title"]
    raise SystemExit(f"티커 '{ticker}'를 SEC 목록에서 찾지 못했습니다. 철자를 확인해 주세요.")


def get_recent_filings(cik):
    """회사의 최근 공시 목록을 [{form, date, accession, doc, items}, ...] 형태로 반환."""
    data = fetch_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    recent = data["filings"]["recent"]
    filings = []
    for i in range(len(recent["form"])):
        filings.append({
            "form": recent["form"][i],
            "date": recent["filingDate"][i],
            "accession": recent["accessionNumber"][i],
            "doc": recent["primaryDocument"][i],
            "items": recent.get("items", [""] * len(recent["form"]))[i] or "",
        })
    return filings  # SEC가 최신순으로 정렬해서 줍니다


def filing_url(cik, accession, doc):
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"


def get_8ks(cik, filings):
    result = []
    for f in filings:
        if f["form"] == "8-K":
            f = dict(f)
            f["url"] = filing_url(cik, f["accession"], f["doc"])
            result.append(f)
        if len(result) >= NUM_8K:
            break
    return result


def _text(node, path):
    """XML에서 값을 꺼냅니다. Form 4는 값이 <value> 태그 안에 있는 경우가 많습니다."""
    el = node.find(path)
    if el is None:
        return ""
    v = el.find("value")
    if v is not None:
        el = v
    return (el.text or "").strip()


def _num(s):
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def parse_form4(xml_bytes):
    """Form 4 XML을 읽어 내부자 정보와 거래 목록을 반환."""
    root = ET.fromstring(xml_bytes)

    owners = []
    for ro in root.findall("reportingOwner"):
        name = _text(ro, "reportingOwnerId/rptOwnerName")
        rel = ro.find("reportingOwnerRelationship")
        roles = []
        if rel is not None:
            if _text(rel, "isDirector") in ("1", "true"):
                roles.append("이사")
            if _text(rel, "isOfficer") in ("1", "true"):
                title = _text(rel, "officerTitle")
                roles.append(f"임원({title})" if title else "임원")
            if _text(rel, "isTenPercentOwner") in ("1", "true"):
                roles.append("10% 이상 주주")
            if _text(rel, "isOther") in ("1", "true"):
                other = _text(rel, "otherText")
                roles.append(other or "기타")
        owners.append({"name": name, "role": ", ".join(roles) or "-"})

    plan_10b5_1 = _text(root, "aff10b5One") in ("1", "true")

    transactions = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        shares = _num(_text(tx, "transactionAmounts/transactionShares"))
        price = _num(_text(tx, "transactionAmounts/transactionPricePerShare"))
        transactions.append({
            "security": _text(tx, "securityTitle"),
            "date": _text(tx, "transactionDate"),
            "code": _text(tx, "transactionCoding/transactionCode"),
            "acq_disp": _text(tx, "transactionAmounts/transactionAcquiredDisposedCode"),
            "shares": shares,
            "price": price,
            "value": shares * price if shares is not None and price else None,
            "after": _num(_text(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
        })
    return owners, transactions, plan_10b5_1


def get_insider_trades(cik, filings, today):
    since = (today - timedelta(days=INSIDER_DAYS)).isoformat()
    form4s = [f for f in filings if f["form"] in ("4", "4/A") and f["date"] >= since]
    truncated = len(form4s) > MAX_FORM4
    form4s = form4s[:MAX_FORM4]

    trades = []
    for n, f in enumerate(form4s, 1):
        print(f"  Form 4 읽는 중... ({n}/{len(form4s)})", end="\r", flush=True)
        # primaryDocument는 보통 "xslF345X05/파일.xml"(웹 표시용) 형태 → 원본 XML은 폴더를 뺀 경로
        xml_name = f["doc"].split("/")[-1]
        url = filing_url(cik, f["accession"], xml_name)
        try:
            owners, txs, plan = parse_form4(fetch(url))
        except (ET.ParseError, urllib.error.HTTPError) as e:
            print(f"\n  경고: {f['accession']} 읽기 실패 ({e}) - 건너뜁니다.")
            continue
        owner_names = " / ".join(o["name"] for o in owners) or "-"
        owner_roles = " / ".join(o["role"] for o in owners) or "-"
        for t in txs:
            t.update({
                "owner": owner_names,
                "role": owner_roles,
                "filed": f["date"],
                "plan": plan,
                "url": filing_url(cik, f["accession"], f["doc"]),
            })
            trades.append(t)
    if form4s:
        print()
    trades.sort(key=lambda t: t["date"], reverse=True)
    return trades, len(form4s), truncated


# ---------------------------------------------------------------------------
# 리포트 작성
# ---------------------------------------------------------------------------

def fmt_num(x, digits=0):
    if x is None:
        return "-"
    return f"{x:,.{digits}f}"


def fmt_money(x):
    if x is None:
        return "-"
    if abs(x) >= 1e6:
        return f"${x / 1e6:,.1f}M"
    return f"${x:,.0f}"


def describe_items(items):
    if not items:
        return "-"
    parts = []
    for code in items.split(","):
        code = code.strip()
        parts.append(f"{code} {ITEM_8K_KO.get(code, '')}".strip())
    return "<br>".join(parts)


def build_report(ticker, company, cik, today, eightks, trades, n_form4, truncated):
    L = []
    L.append(f"# {ticker} ({company}) SEC 공시 요약 리포트")
    L.append("")
    L.append(f"- 작성일: {today.isoformat()}")
    L.append(f"- CIK: {cik}")
    L.append(f"- 데이터 출처: SEC EDGAR 공식 API (data.sec.gov)")
    L.append("")

    # ---- 한눈에 보기 ----
    buys = [t for t in trades if t["code"] == "P"]
    sells = [t for t in trades if t["code"] == "S"]
    buy_value = sum(t["value"] or 0 for t in buys)
    sell_value = sum(t["value"] or 0 for t in sells)
    buy_shares = sum(t["shares"] or 0 for t in buys)
    sell_shares = sum(t["shares"] or 0 for t in sells)
    plan_sells = [t for t in sells if t["plan"]]

    L.append("## 한눈에 보기")
    L.append("")
    if eightks:
        latest = eightks[0]
        L.append(f"- 가장 최근 8-K: **{latest['date']}** — {describe_items(latest['items']).replace('<br>', ', ')}")
    else:
        L.append("- 최근 8-K 공시가 없습니다.")
    L.append(f"- 최근 {INSIDER_DAYS}일 Form 4 공시: **{n_form4}건**, 주식 거래 내역 **{len(trades)}건**")
    L.append(f"- 장내 매수(P): **{len(buys)}건**, {fmt_num(buy_shares)}주, 약 {fmt_money(buy_value)}")
    L.append(f"- 장내 매도(S): **{len(sells)}건**, {fmt_num(sell_shares)}주, 약 {fmt_money(sell_value)}")
    if sells:
        L.append(f"  - 이 중 10b5-1(사전 계획 매매)로 표시된 매도: {len(plan_sells)}건")
    if buys and not sells:
        L.append("- 해석 참고: 최근 내부자 매도 없이 매수만 있습니다.")
    elif sells and not buys:
        L.append("- 해석 참고: 내부자 장내 매수는 없고 매도만 있습니다. "
                 "임원 매도는 보상 주식 현금화·세금·사전 계획(10b5-1)인 경우가 많아 "
                 "그 자체로 부정적 신호라고 단정할 수는 없습니다.")
    if truncated:
        L.append(f"- 참고: Form 4가 너무 많아 최근 {MAX_FORM4}건만 분석했습니다.")
    L.append("")

    # ---- 8-K ----
    L.append(f"## 최근 8-K 공시 {len(eightks)}건")
    L.append("")
    L.append("8-K는 회사에 중요한 일이 생겼을 때 수시로 내는 공시입니다.")
    L.append("")
    if eightks:
        L.append("| 제출일 | 항목(Item) | 원문 |")
        L.append("|---|---|---|")
        for f in eightks:
            L.append(f"| {f['date']} | {describe_items(f['items'])} | [보기]({f['url']}) |")
    else:
        L.append("최근 8-K 공시가 없습니다.")
    L.append("")

    # ---- Form 4 ----
    L.append(f"## 최근 {INSIDER_DAYS}일 내부자 거래 (Form 4)")
    L.append("")
    L.append("Form 4는 임원·이사·대주주가 자사 주식을 사고팔았을 때 2영업일 안에 내는 신고서입니다.")
    L.append("")

    # 사람별 요약
    by_person = {}
    for t in trades:
        p = by_person.setdefault(t["owner"], {"role": t["role"], "buy": 0.0, "sell": 0.0, "n": 0})
        p["n"] += 1
        if t["code"] == "P":
            p["buy"] += t["value"] or 0
        elif t["code"] == "S":
            p["sell"] += t["value"] or 0
    if by_person:
        L.append("### 사람별 요약")
        L.append("")
        L.append("| 이름 | 직책 | 거래 건수 | 장내 매수 금액 | 장내 매도 금액 |")
        L.append("|---|---|---|---|---|")
        for name, p in sorted(by_person.items(), key=lambda kv: -(kv[1]["buy"] + kv[1]["sell"])):
            L.append(f"| {name} | {p['role']} | {p['n']} | {fmt_money(p['buy']) if p['buy'] else '-'} "
                     f"| {fmt_money(p['sell']) if p['sell'] else '-'} |")
        L.append("")

        L.append("### 거래 상세")
        L.append("")
        L.append("| 거래일 | 이름 | 거래 종류 | 주식 수 | 단가 | 금액 | 거래 후 보유 | 10b5-1 | 원문 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for t in trades:
            kind = TX_CODE_KO.get(t["code"], t["code"] or "-")
            sign = "+" if t["acq_disp"] == "A" else "-" if t["acq_disp"] == "D" else ""
            L.append(
                f"| {t['date']} | {t['owner']} | {kind} ({t['code']}) | {sign}{fmt_num(t['shares'])} "
                f"| {'$' + fmt_num(t['price'], 2) if t['price'] else '-'} | {fmt_money(t['value'])} "
                f"| {fmt_num(t['after'])} | {'예' if t['plan'] else ''} | [보기]({t['url']}) |"
            )
    else:
        L.append(f"최근 {INSIDER_DAYS}일 동안 보통주 거래가 담긴 Form 4가 없습니다.")
    L.append("")

    L.append("---")
    L.append("")
    L.append("※ 이 리포트는 SEC 공시 데이터를 자동으로 정리한 것이며 투자 권유가 아닙니다. "
             "금액은 신고된 주식 수 × 단가로 계산한 추정치이고, 파생상품(옵션 등) 거래는 제외했습니다.")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) >= 2:
        ticker = sys.argv[1]
    else:
        ticker = input("티커를 입력하세요 (예: NVDA): ")
    ticker = ticker.strip().upper()
    if not ticker:
        raise SystemExit("티커가 비어 있습니다.")

    today = date.today()
    print(f"[1/4] {ticker} 회사 정보를 찾는 중...")
    cik, company = find_company(ticker)
    print(f"      → {company} (CIK {cik})")

    print("[2/4] 최근 공시 목록을 가져오는 중...")
    filings = get_recent_filings(cik)
    eightks = get_8ks(cik, filings)

    print(f"[3/4] 최근 {INSIDER_DAYS}일 내부자 거래(Form 4)를 읽는 중...")
    trades, n_form4, truncated = get_insider_trades(cik, filings, today)

    print("[4/4] 리포트를 작성하는 중...")
    report = build_report(ticker, company, cik, today, eightks, trades, n_form4, truncated)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"{ticker}_{today.isoformat()}.md")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(report)
    print(f"\n완료! 리포트 파일: {path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n중단했습니다.")
    except RuntimeError as e:
        raise SystemExit(f"오류: {e}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"SEC 서버 오류({e.code}): {e.url}")
    except urllib.error.URLError as e:
        raise SystemExit(f"인터넷 연결에 문제가 있습니다: {e.reason}")
