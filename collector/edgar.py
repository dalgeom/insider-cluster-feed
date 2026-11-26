import json
import re
from datetime import timedelta

ARCHIVES = "https://www.sec.gov/Archives/"
LINE = re.compile(r"^(4|4/A)\s+.*?\s(\d{8})\s+(edgar/data/\S+\.txt)\s*$")
XML_BLOCK = re.compile(r"<XML>(.*?)</XML>", re.S)


def parse_daily_index(text):
    """form.idx에서 Form 4, 4/A 행만 accession 기준 중복 없이 돌려준다."""
    seen, out = set(), []
    for line in text.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        form, d, path = m.groups()
        acc = accession_from_path(path)
        if acc in seen:
            continue
        seen.add(acc)
        out.append({"form": form, "filed": f"{d[:4]}-{d[4:6]}-{d[6:]}", "path": path})
    return out


def accession_from_path(path):
    return path.rsplit("/", 1)[1][:-4]


def filing_url(path):
    return ARCHIVES + path


def index_url(path):
    cik = path.split("/")[2]
    acc = accession_from_path(path)
    return f"{ARCHIVES}edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm"


def daily_index_url(d):
    q = (d.month - 1) // 3 + 1
    return f"{ARCHIVES}edgar/daily-index/{d.year}/QTR{q}/form.{d:%Y%m%d}.idx"


def quarter_listing_url(d):
    return daily_index_url(d).rsplit("/", 1)[0] + "/index.json"


def parse_listing(text):
    return {item["name"] for item in json.loads(text)["directory"]["item"]}


def extract_xml(raw):
    m = XML_BLOCK.search(raw)
    return m.group(1).strip() if m else None


def business_days(start, end):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out
