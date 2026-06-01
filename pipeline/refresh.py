"""
refresh.py — Pull the latest sourcing dashboard data from Keboola Storage API.

Reads out.c-WBRMBR-weekly-aggregations.sourcing_dashboard_per_sourcer (Option C
SQL block, populated by the WBR/MBR transformation), aggregates to quarterly
granularity, and writes data.json. Committed to the repo by the GitHub
Actions workflow; Cloudflare Pages auto-deploys the change.

Requires KEBOOLA_STORAGE_API_TOKEN env var (a read-only Storage API token).
"""

from __future__ import annotations
import csv, io, json, os, sys
from datetime import date, datetime, timezone
from collections import defaultdict

KEBOOLA_BASE = "https://connection.eu-central-1.keboola.com/v2/storage"
TABLE_ID = "out.c-WBRMBR-weekly-aggregations.sourcing_dashboard_per_sourcer"
OUTPUT = "data.json"


def iso_week_to_quarter(year: int, week: int) -> tuple[int, int]:
    """Map an ISO (year, week) to a calendar (year, quarter)."""
    monday = date.fromisocalendar(year, week, 1)
    return monday.year, (monday.month - 1) // 3 + 1


def pull_table_csv(token: str) -> list[dict]:
    """Export the table via Storage API → CSV → list of dicts."""
    import requests
    url = f"{KEBOOLA_BASE}/tables/{TABLE_ID}/export-async"
    r = requests.post(url, headers={"X-StorageApi-Token": token}, json={"format": "rfc"})
    r.raise_for_status()
    job_id = r.json()["id"]
    # Poll the job
    job_url = f"{KEBOOLA_BASE}/jobs/{job_id}"
    while True:
        j = requests.get(job_url, headers={"X-StorageApi-Token": token}).json()
        if j.get("status") in ("success", "error"):
            break
    if j["status"] != "success":
        raise RuntimeError(f"Keboola export job failed: {j}")
    file_id = j["results"]["file"]["id"]
    f = requests.get(f"{KEBOOLA_BASE}/files/{file_id}?federationToken=1", headers={"X-StorageApi-Token": token}).json()
    csv_resp = requests.get(f["url"])
    csv_resp.raise_for_status()
    text = csv_resp.content.decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def aggregate_quarterly(rows: list[dict]) -> list[dict]:
    """Roll up weekly rows into quarterly aggregates."""
    bucket = defaultdict(lambda: {
        "contacted": 0, "pos_resp": 0, "rs": 0, "act_scr": 0,
        "ats": 0, "offered": 0, "hired": 0, "sourcers": set(),
    })
    for r in rows:
        y, w = int(r["ISO_YEAR"]), int(r["ISO_WEEK"])
        cy, cq = iso_week_to_quarter(y, w)
        key = (cy, cq)
        bucket[key]["contacted"] += int(r["CONTACTED"])
        bucket[key]["pos_resp"]  += int(r["POSITIVE_RESPONSE"])
        bucket[key]["rs"]        += int(r["RECRUITER_SCREENS"])
        bucket[key]["act_scr"]   += int(r["ACTUAL_SCREENS"])
        bucket[key]["ats"]       += int(r["MOVED_TO_ATS"])
        bucket[key]["offered"]   += int(r["OFFERED"])
        bucket[key]["hired"]     += int(r["HIRED"])
        if int(r["CONTACTED"]) > 0:
            bucket[key]["sourcers"].add(r["TS"])

    today = date.today()
    cur_y, cur_q = today.year, (today.month - 1) // 3 + 1
    out = []
    for (y, q), v in sorted(bucket.items(), reverse=True):
        out.append({
            "q": f"{y} Q{q}",
            "in_progress": (y == cur_y and q == cur_q),
            "contacted": v["contacted"],
            "pos_resp":  v["pos_resp"] if v["pos_resp"] > 0 else None,
            "rs":        v["rs"],
            "act_scr":   v["act_scr"],
            "ats":       v["ats"],
            "offered":   v["offered"],
            "hired":     v["hired"],
            "team_size": len(v["sourcers"]),
        })
    # Drop pre-2025 (Phase 1 scope is 2025-onwards)
    return [r for r in out if not r["q"].startswith("2024") and not r["q"].startswith("2023")]


def main() -> int:
    token = os.environ.get("KEBOOLA_STORAGE_API_TOKEN")
    if not token:
        print("ERROR: KEBOOLA_STORAGE_API_TOKEN not set", file=sys.stderr)
        return 1
    rows = pull_table_csv(token)
    quarterly = aggregate_quarterly(rows)
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"Keboola {TABLE_ID} (Option C cross-client filter)",
        "methodology_version": "1.0",
        "quarterly": quarterly,
    }
    os.makedirs(os.path.dirname(OUTPUT) or ".", exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    print(f"Wrote {OUTPUT} with {len(quarterly)} quarters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
