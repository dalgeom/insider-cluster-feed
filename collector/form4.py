import re
import xml.etree.ElementTree as ET

EXEC_PATTERNS = [
    ("ceo", re.compile(r"\bceo\b|chief executive officer")),
    ("cfo", re.compile(r"\bcfo\b|chief financial officer")),
    ("president", re.compile(r"(?<!vice[ -])\bpresident\b")),
    ("coo", re.compile(r"\bcoo\b|chief operating officer")),
    ("chair", re.compile(r"(?<!vice[ -])\bchair")),
]


def _text(el, path):
    if el is None:
        return ""
    node = el.find(path)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _flag(el, path):
    return _text(el, path).lower() in ("1", "true")


def _num(el, path):
    try:
        return float(_text(el, path))
    except ValueError:
        return None


def _clean(n):
    return int(n) if n is not None and float(n).is_integer() else n


def _cik(s):
    return str(int(s)) if s.isdigit() else s


def roles_for(rel):
    title = _text(rel, "officerTitle")
    low = title.lower()
    roles = [name for name, pat in EXEC_PATTERNS if pat.search(low)]
    if _flag(rel, "isDirector"):
        roles.append("director")
    if _flag(rel, "isOfficer"):
        roles.append("officer")
    if _flag(rel, "isTenPercentOwner"):
        roles.append("ten_pct")
    return roles, title


def _ticker(raw):
    t = raw.strip().upper()
    return "" if t in ("NONE", "N/A", "NA", "-") else t


def parse_form4(xml_text, accession, filed, url):
    """Form 4 XML에서 장내 매수(P) 행 목록과 단가 누락 제외 건수를 돌려준다."""
    root = ET.fromstring(xml_text.strip())
    owners = root.findall("reportingOwner")
    if not owners:
        return [], 0
    issuer = root.find("issuer")
    first = owners[0]
    roles, title = roles_for(first.find("reportingOwnerRelationship"))
    base = {
        "accession": accession,
        "filed": filed,
        "ticker": _ticker(_text(issuer, "issuerTradingSymbol")),
        "issuer": _text(issuer, "issuerName"),
        "issuer_cik": _cik(_text(issuer, "issuerCik")),
        "owner": _text(first, "reportingOwnerId/rptOwnerName"),
        "owner_cik": _cik(_text(first, "reportingOwnerId/rptOwnerCik")),
        "co_owners": [_text(o, "reportingOwnerId/rptOwnerName") for o in owners[1:]],
        "roles": roles,
        "title": title,
    }
    amended = _text(root, "documentType").endswith("/A")
    rows, no_price = [], 0
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if _text(t, "transactionCoding/transactionCode") != "P":
            continue
        shares = _num(t, "transactionAmounts/transactionShares/value")
        if not shares or shares <= 0:
            continue
        price = _num(t, "transactionAmounts/transactionPricePerShare/value")
        if price is None or price <= 0:
            no_price += 1
            continue
        row = dict(base)
        row.update({
            "trade_date": _text(t, "transactionDate/value")[:10],
            "shares": _clean(shares),
            "price": price,
            "value": round(shares * price),
            "owned_after": _clean(_num(t, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")),
            "direct": _text(t, "ownershipNature/directOrIndirectOwnership/value"),
            "amended": amended,
            "url": url,
        })
        rows.append(row)
    return rows, no_price
