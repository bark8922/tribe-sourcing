"""keboola_entry.py - Sourcing Dashboard Phase 1 refresh.

Custom Python component entrypoint. Same pattern as the recruiting dashboard's
keboola_entry.py: read staged input CSV(s), build the dashboard data.json,
PUT it to the GitHub repo via the Contents API.
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
METHODOLOGY_VERSION = "1.5"
QUARTERLY_MIN_CONTACTED = 5
EXCLUDED_SOURCERS = {"Sanja Pavlovikj"}


def iso_week_to_calendar_quarter(year, week):
    monday = date.fromisocalendar(year, week, 1)
    return monday.year, (monday.month - 1) // 3 + 1


def aggregate_quarterly(rows):
    per_sq = defaultdict(lambda: {
        "contacted": 0, "pos_resp": 0, "rs": 0, "act_scr": 0,
        "ats": 0, "offered": 0, "hired": 0,
    })
    for r in rows:
        if r["TS"] in EXCLUDED_SOURCERS:
            continue
        y, w = int(r["ISO_YEAR"]), int(r["ISO_WEEK"])
        cy, cq = iso_week_to_calendar_quarter(y, w)
        key = (cy, cq, r["TS"])
        v = per_sq[key]
        v["contacted"] += int(r["CONTACTED"])
        v["pos_resp"]  += int(r["POSITIVE_RESPONSE"])
        v["rs"]        += int(r["RECRUITER_SCREENS"])
        v["act_scr"]   += int(r["ACTUAL_SCREENS"])
        v["ats"]       += int(r["MOVED_TO_ATS"])
        v["offered"]   += int(r["OFFERED"])
        v["hired"]     += int(r["HIRED"])

    bucket = defaultdict(lambda: {
        "contacted": 0, "pos_resp": 0, "rs": 0, "act_scr": 0,
        "ats": 0, "offered": 0, "hired": 0, "sourcer_contacts": {},
    })
    for (cy, cq, ts), v in per_sq.items():
        if v["contacted"] < QUARTERLY_MIN_CONTACTED:
            continue
        b = bucket[(cy, cq)]
        for k in ("contacted", "pos_resp", "rs", "act_scr", "ats", "offered", "hired"):
            b[k] += v[k]
        b["sourcer_contacts"][ts] = v["contacted"]

    today = date.today()
    cur_y, cur_q = today.year, (today.month - 1) // 3 + 1
    out = []
    for (y, q), v in sorted(bucket.items(), reverse=True):
        if y < 2025:
            continue
        sourcers_sorted = sorted(v["sourcer_contacts"].items(), key=lambda kv: -kv[1])
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
            "team_size":  len(sourcers_sorted),
            "sourcers":   [name for name, _ in sourcers_sorted],
        })
    return out


def read_input_csv(ci):
    for tbl in ci.get_input_tables_definitions():
        if Path(tbl.full_path).name == INPUT_CSV_NAME:
            with open(tbl.full_path, newline="") as f:
                return list(csv.DictReader(f))
    raise RuntimeError("Input table " + INPUT_CSV_NAME + " not in input mapping")


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
    # Merge: keep cost array if it exists in current data.json (Phase 2 mock baked manually)
    try:
        cur_decoded = base64.b64decode(current["content"]).decode("utf-8")
        cur_obj = json.loads(cur_decoded)
        new_obj = json.loads(content)
        if "cost" in cur_obj and "cost" not in new_obj:
            new_obj["cost"] = cur_obj["cost"]
        content = json.dumps(new_obj, indent=2, ensure_ascii=False) + "\n"
    except Exception as e:
        print("[push_to_github] cost-merge skipped: " + str(e), flush=True)

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

    github_token = params.get("#github_token")
    if not github_token:
        github_token = params.get("user_properties", {}).get("#github_token")
    if not github_token:
        raise RuntimeError("Missing #github_token in configuration parameters.")
    print("=== github_token loaded (len=" + str(len(github_token)) + ") ===", flush=True)

    rows = read_input_csv(ci)
    print("[main] read " + str(len(rows)) + " weekly rows from input", flush=True)

    quarterly = aggregate_quarterly(rows)
    print("[main] aggregated to " + str(len(quarterly)) + " quarters", flush=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Keboola out.c-WBRMBR-weekly-aggregations.sourcing_dashboard_per_sourcer (v1.5: Bench/Internal only + onboarding drop + Sanja excluded + <5 noise filter)",
        "methodology_version": METHODOLOGY_VERSION,
        "quarterly": quarterly,
    }
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    sha = push_to_github(github_token, content)
    print("=== done: commit=" + sha[:10] + " size=" + str(len(content) // 1024) + "KB ===", flush=True)
    return 0


print("=== about to call main() ===", flush=True)
_rc = main()
print("=== main() returned " + str(_rc) + " ===", flush=True)
sys.exit(_rc)
