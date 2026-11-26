import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from collector import edgar, form4, store

FEED = Path(__file__).resolve().parents[1]
DATA_PATH = FEED / "data" / "purchases.json"
STATE_PATH = FEED / "collector" / "state.json"
DELAY = 0.125          # 초당 8건 (SEC 한도 10건)
MAX_TRIES = 3
REWIND_DAYS = 10
KEEP_DAYS = 120
DONE_KEEP_DAYS = 15


class NotFound(Exception):
    pass


class HttpTransport:
    def __init__(self, ua):
        self.ua = ua

    def get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": self.ua})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from e
            raise


def _load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _day(state, filed):
    return state["stats"].setdefault(filed, {"filings": 0, "no_price": 0, "gave_up": 0})


def _fail(state, acc, entry):
    f = state["failed"].setdefault(acc, dict(entry, tries=0))
    f["tries"] += 1
    if f["tries"] >= MAX_TRIES:
        del state["failed"][acc]
        state["done"][acc] = entry["filed"]
        _day(state, entry["filed"])["gave_up"] += 1


class _Paced:
    """요청 시작 간격을 DELAY 이상으로 유지한다(응답 대기 시간도 간격에 포함)."""

    def __init__(self, transport, sleep, clock):
        self.transport, self.sleep, self.clock = transport, sleep, clock
        self.last = None

    def get(self, url):
        if self.last is not None:
            wait = DELAY - (self.clock() - self.last)
            if wait > 0:
                self.sleep(wait)
        self.last = self.clock()
        return self.transport.get(url)


def _process(transport, entry, state, new_rows):
    acc = edgar.accession_from_path(entry["path"])
    try:
        raw = transport.get(edgar.filing_url(entry["path"]))
        xml = edgar.extract_xml(raw)
        if xml is None:
            raise ValueError("no XML block")
        rows, no_price = form4.parse_form4(xml, acc, entry["filed"], edgar.index_url(entry["path"]))
    except Exception:
        _fail(state, acc, entry)
        return
    new_rows.extend(rows)
    day = _day(state, entry["filed"])
    day["filings"] += 1
    day["no_price"] += no_price
    state["done"][acc] = entry["filed"]
    state["failed"].pop(acc, None)


def _start(state, today, backfill_days):
    if backfill_days:
        return today - timedelta(days=backfill_days)
    if state.get("backfill"):  # 끊긴 백필은 10일 하한 없이 이어 받는다
        if state["last_date"]:
            return date.fromisoformat(state["last_date"]) + timedelta(days=1)
        return date.fromisoformat(state["backfill"])
    floor = today - timedelta(days=REWIND_DAYS)
    if state["last_date"]:
        return max(date.fromisoformat(state["last_date"]) + timedelta(days=1), floor)
    return floor


def run(transport, today, backfill_days=0, data_path=DATA_PATH, state_path=STATE_PATH, sleep=time.sleep,
        clock=time.monotonic):
    state = _load(state_path, {"last_date": None, "done": {}, "failed": {}, "stats": {}})
    rows = _load(data_path, {"rows": []})["rows"]
    if backfill_days:
        state["done"], state["stats"], state["last_date"] = {}, {}, None
        state["backfill"] = (today - timedelta(days=backfill_days)).isoformat()
    transport = _Paced(transport, sleep, clock)
    retry = list(state["failed"].items())
    new_rows = []
    try:
        for d in edgar.business_days(_start(state, today, backfill_days), today):
            try:
                text = transport.get(edgar.daily_index_url(d))
            except NotFound:
                continue
            for entry in edgar.parse_daily_index(text):
                acc = edgar.accession_from_path(entry["path"])
                if acc in state["done"] or acc in state["failed"]:
                    continue
                _process(transport, entry, state, new_rows)
            state["last_date"] = max(state["last_date"] or "", d.isoformat())
        state.pop("backfill", None)
        for acc, entry in retry:  # 인덱스가 정상일 때만 재시도해 장애 중 시도 횟수를 아낀다
            if acc in state["failed"]:
                _process(transport, {k: entry[k] for k in ("form", "filed", "path")}, state, new_rows)
    finally:
        rows = store.prune(store.merge(rows, new_rows), today, KEEP_DAYS)
        done_cut = (today - timedelta(days=DONE_KEEP_DAYS)).isoformat()
        stats_cut = (today - timedelta(days=KEEP_DAYS)).isoformat()
        state["done"] = {a: f for a, f in state["done"].items() if f >= done_cut}
        state["stats"] = {d: v for d, v in state["stats"].items() if d >= stats_cut}
        stats = state["stats"].values()
        _save(data_path, {
            "schema": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "data_through": state["last_date"],
            "filings_processed": sum(v["filings"] for v in stats),
            "excluded": {
                "no_price": sum(v["no_price"] for v in stats),
                "gave_up": sum(v["gave_up"] for v in stats),
            },
            "rows": sorted(rows, key=lambda r: (r["trade_date"], r["accession"]), reverse=True),
        })
        _save(state_path, state)


def main(argv=None):
    p = argparse.ArgumentParser(description="Collect Form 4 open-market purchases")
    p.add_argument("--backfill-days", type=int, default=0)
    a = p.parse_args(argv)
    ua = os.environ.get("SEC_USER_AGENT", "")
    if "@" not in ua:
        print("ERROR: SEC_USER_AGENT must include a contact email", file=sys.stderr)
        return 2
    run(HttpTransport(ua), date.today(), a.backfill_days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
