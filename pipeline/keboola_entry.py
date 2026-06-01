"""keboola_entry.py — Sourcing Dashboard Phase 1 refresh.

Custom Python component entrypoint. Same pattern as the recruiting dashboard's
keboola_entry.py: read staged input CSV(s), build the dashboard data.json,
PUT it to the GitHub repo via the Contents API. A GitHub Actions deploy
workflow on the repo then deploys to Cloudflare Pages.

Inputs (Keboola input mapping):
  out.c-WBRMBR-weekly-aggregations.sourcing_dashboard_per_sourcer
    → snowflake_sourcing_dashboard.csv

Output (PUT to repo via GitHub Contents API):
  bark8922/tribe-sourcing : data.json
"""
import sys
print("=== sourcing keboola_entry.py loaded ===", flush=True)

import base64, csv, json, urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from keboola.component import CommonInterface

REPO = "bark8922/tribe-sourcing"
TARGET_FILE = "data.json"
INPUT_CSV_NAME = "snowflake_sourcing_dashboard.csv"
METHODOLOGY_VERSION = "1.0"


def iso_week_to_calendar_quarter(year, week):
    """Map ISO (year, week) to calendar (year, quarter)."""
    monday = date.fromisocalendar(year, week, 1)
    return monday.year, (monday.month - 1) // 3 + 1


def aggregate_quarterly(rows):
    bucket = defaultdict(lambda: {
        "contacted": 0, "pos_resp": 0, "rs": 0, "act_scr": 0,
        "ats": 0, "offered": 0, "hired": 0, "sourcers": set(),
    })
    for r in rows:
        y, w = int(r["ISO_YEAR"]), int(r["ISO_WEEK"])
        cy, cq = iso_week_to_calendar_quarter(y, w)
        key = (cy, cq)
        b = bucket[key]
        b["contacted"] += int(r["CONTACTED"])
        b["pos_resp"]  += int(r["POSITIVE_RESPONSE"])
        b["rs"]        += int(r["RECRUITER_SCREENS"])
        b["act_scr"]   += int(r["ACTUAL_SCREENS"])
        b["ats"]       += int(r["MOVED_TO_ATS"])
        b["offered"]   += int(r["OFFERED"])
        b["hired"]     += int(r["HIRED"])
        if int(r["CONTACTED"]) > 0:
            b["sourcers"].add(r["TS"])

    today = date.today()
    cur_y, cur_q = today.year, (today.month - 1) // 3 + 1
    out = []
    for (y, q), v in sorted(bucket.items(), reverse=True):
        if y < 2025:
            continue
        out.append({
            "q":          f"{y} Q{q}",
            "in_progress": (y == cur_y and q == cur_q),
            "contacted":  v["contacted"],
            "pos_resp":   v["pos_resp"] if v["pos_resp"] > 0 else None,
            "rs":         v["rs"],
            "act_scr":    v["act_scr"],
            "ats":        v["ats"],
            "offered":    v["offered"],
            "hired":      v["hired"],
            "team_size":  len(v["sourcers"]),
        })
    return out


def read_input_csv(ci):
    for tbl in ci.get_input_tables_definitions():
        if Path(tbl.full_path).name == INPUT_CSV_NAME:
            with open(tbl.full_path, newline="") as f:
                return list(csv.DictReader(f))
    raise RuntimeError(f"Input table {INPUT_CSV_NAME} not in input mapping")


def push_to_github(token, content):
    url = "https://api.github.com/repos/" + REPO + "/contents/" + TARGET_FILE
    headers = {
        "Authorization": "token " + token,
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "keboola-custom-python-tribe-sourcing",
    }
    req = urllib.request.Request(url + "?ref=main", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        current = json.loads(r.read())
    sha = current["sha"]
    print("[push_to_github] current sha: " + sha[:10], flush=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = json.dumps({
        "message": "refresh: Keboola-driven rebuild (" + now + ")",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "sha": sha,
        "branch": "main",
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="PUT",
        headers={**headers, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    commit = resp["commit"]
    print("[push_to_github] pushed " + commit["sha"][:10] + ": " + commit["html_url"], flush=True)
    return commit["sha"]


def main():
    print("=== main() called ===", flush=True)
    ci = CommonInterface()
    params = ci.configuration.parameters
    print("=== CommonInterface ready, params keys: " + str(list(params.keys())) + " ===", flush=True)

    github_token = params.get("#github_token") or params.get("user_properties", {}).get("#github_token")
    if not github_token:
        raise RuntimeError("Missing #github_token in configuration parameters.")
    print("=== github_token loaded (len=" + str(len(github_token)) + ") ===", flush=True)

    rows = read_input_csv(ci)
    print("[main] read " + str(len(rows)) + " weekly rows from input", flush=True)

    quarterly = aggregate_quarterly(rows)
    print("[main] aggregated to " + str(len(quarterly)) + " quarters", flush=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Keboola out.c-WBRMBR-weekly-aggregations.sourcing_dashboard_per_sourcer (Option C cross-client filter)",
        "methodology_version": METHODOLOGY_VERSION,
        "quarterly": quarterly,
    }
    content = json.dumps(payload, indent=2) + "\n"

    sha = push_to_github(github_token, content)
    print("=== done: commit=" + sha[:10] + " size=" + str(len(content) // 1024) + "KB ===", flush=True)
    return 0


print("=== about to call main() ===", flush=True)
_rc = main()
print("=== main() returned " + str(_rc) + " ===", flush=True)
sys.exit(_rc)
