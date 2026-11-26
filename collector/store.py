from datetime import timedelta


def row_key(r):
    return (r["owner_cik"], r["issuer_cik"], r["trade_date"], r["shares"], r["direct"])


def merge(existing, new):
    """새 행을 합친다. 같은 공시·키·단가는 건너뛰고, 4/A는 같은 키의 기존 행을 대체한다."""
    rows = list(existing)
    by_key = {}
    for i, r in enumerate(rows):
        by_key.setdefault(row_key(r), []).append(i)
    for r in new:
        k = row_key(r)
        hits = by_key.get(k, [])
        if any(rows[i]["accession"] == r["accession"] and rows[i]["price"] == r["price"] for i in hits):
            continue
        if r["amended"]:
            old = [i for i in hits if rows[i]["accession"] != r["accession"]]
            if old:
                rows[old[0]] = r
                continue
        by_key.setdefault(k, []).append(len(rows))
        rows.append(r)
    return rows


def prune(rows, today, days=120):
    cutoff = (today - timedelta(days=days)).isoformat()
    return [r for r in rows if r["trade_date"] >= cutoff]
