"""keboola_entry.py - Sourcing Dashboard refresh.

Phase 1 (TS-Summary): reads sourcing_dashboard_per_sourcer CSV, aggregates
weekly -> quarterly, applies v1.5 methodology (Bench/Internal only, onboarding
drop, Sanja excluded, <5 noise filter), produces quarterly array.

Phase 2 (Cost): fetches bark8922/tribe-dashboard data-next/data.json (the
Finance dashboard's per-Tribster per-month allocation records), filters to
our 19-sourcer roster, applies ct-based cost classification, produces cost
array using Gustavo's Sustainability Score framework.

PUTs the combined data.json to bark8922/tribe-sourcing main via GitHub
Contents API. GitHub Actions then deploys to Cloudflare Pages.
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
WBR_INPUT_CSV_NAME = "snowflake_wbr_comments.csv"
METHODOLOGY_VERSION = "1.5"
QUARTERLY_MIN_CONTACTED = 5
EXCLUDED_SOURCERS = {"Sanja Pavlovikj"}

# Phase 2: Finance dashboard data source
FINANCE_REPO = "bark8922/tribe-dashboard"
FINANCE_DATA_PATH = "data-next/data.json"

# Phase 2 roster — Finance dashboard uses "Last, First" name format.
# Sanja Pavlovikj intentionally excluded (Phase 1 + Phase 2 symmetry).
COST_ROSTER = {
    'Akovic, Andrea','Bozkurt, Chantal','Palotas, Dolores','Suljčić, Ejla',
    'Petrovska, Elena','Loureiro Castro, Gustavo','Lazarević, Marina',
    'Ngwenya, Naledi','Avetisyan, Nare','Gomes, Rodrigo',
    'Yurikova, Valeriia','Stajčić, Želimir','Karatanovska, Aleksandra',
    'Markovic, Aleksandra','Barroca, Alberto','Rodriguez Lage, Elena',
    'Bravo Querales, Luis','Gjorgievska, Mia','Veselinovic, Milica',
}


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


def fetch_finance_data(github_token):
    """Fetch tribe-dashboard data-next/data.json via raw URL (handles >1MB files)."""
    url = "https://raw.githubusercontent.com/" + FINANCE_REPO + "/main/" + FINANCE_DATA_PATH
    req = urllib.request.Request(url, headers={
        "Authorization": "token " + github_token,
        "User-Agent": "keboola-custom-python-tribe-sourcing",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def compute_cost(finance_data, quarterly_array):
    """Compute Phase 2 cost rows from Finance dashboard `ea` records.

    Methodology:
      ct='Bench'  -> full pr counts as internal cost
      ct='Client' -> pr * (bd/wd) is internal (bench fraction), rest is external
      Other cost types (Internal / Core Tribe / On Leave) skipped.
    """
    today = date.today()
    cur_y, cur_q = today.year, (today.month - 1) // 3 + 1

    # Pull hires per quarter from Phase 1 output for cost_per_hire calc
    hires_lookup = {}
    for q in quarterly_array:
        # q["q"] format = "YYYY QN"
        parts = q["q"].split(" ")
        hires_lookup[(parts[0], parts[1])] = q.get("hired", 0)

    per_period = defaultdict(lambda: {"int_cost": 0.0, "ext_cost": 0.0, "ext_rev": 0.0})
    for r in finance_data.get("ea", []):
        if r.get("n") not in COST_ROSTER:
            continue
        pr  = r.get("pr", 0) or 0
        bd  = r.get("bd", 0) or 0
        wd  = r.get("wd", 1) or 1
        rev = r.get("rev", 0) or 0
        ct  = r.get("ct", "")
        if wd <= 0:
            wd = 1
        if ct == "Bench":
            per_period[r["p"]]["int_cost"] += pr
        elif ct == "Client":
            bf = bd / wd
            per_period[r["p"]]["int_cost"] += pr * bf
            per_period[r["p"]]["ext_cost"] += pr * (1 - bf)
            per_period[r["p"]]["ext_rev"]  += rev
        # ct in {Internal, Core Tribe, On Leave} -> skip

    def qkey(p):
        y, m = p.split("-")
        return (y, (int(m) - 1) // 3 + 1)

    quarterly = defaultdict(lambda: {"int_cost": 0, "ext_cost": 0, "ext_rev": 0})
    for p, v in per_period.items():
        if not (p.startswith("2025") or p.startswith("2026-0")):
            continue  # Phase 2 scope: 2025 onwards
        # Don't include quarters past the current one — Finance dashboard
        # forecasts cost into the future but revenue hasn't been invoiced yet,
        # which would distort the Sustainability Score.
        y, m = p.split("-")
        if int(y) > cur_y or (int(y) == cur_y and (int(m) - 1) // 3 + 1 > cur_q):
            continue
        k = qkey(p)
        for kk in v:
            quarterly[k][kk] += v[kk]

    out = []
    for k in sorted(quarterly.keys(), reverse=True):
        v = quarterly[k]
        margin = v["ext_rev"] - v["ext_cost"]
        score = (margin / v["int_cost"]) if v["int_cost"] > 0 else None
        hires = hires_lookup.get((k[0], f"Q{k[1]}"), 0)
        cph = (v["int_cost"] / hires) if hires > 0 else None
        out.append({
            "q":            f"{k[0]} Q{k[1]}",
            "int_cost":     round(v["int_cost"]),
            "ext_rev":      round(v["ext_rev"]),
            "ext_cost":     round(v["ext_cost"]),
            "ext_margin":   round(margin),
            "score":        round(score, 2) if score is not None else None,
            "hires":        hires,
            "cost_per_hire": round(cph) if cph else None,
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



def read_wbr_comments(ci):
    """Read sourcing_wbr_comments CSV. Returns [] if the input mapping
    isn't wired yet (graceful — lets Phase 1/2 ship without Phase 3)."""
    for tbl in ci.get_input_tables_definitions():
        if Path(tbl.full_path).name == WBR_INPUT_CSV_NAME:
            with open(tbl.full_path, newline="") as f:
                return list(csv.DictReader(f))
    return []


def build_wbr_comments_array(rows):
    """Transform CSV rows into the wbr_comments JSON array. One entry per
    sourcer-week. Front-end filters client-side by sourcer + year + period."""
    out = []
    for r in rows:
        try:
            year = int(r["ISO_YEAR"])
            week = int(r["ISO_WEEK"])
        except (KeyError, ValueError, TypeError):
            continue
        target_raw = r.get("TARGET")
        try:
            target = int(target_raw) if target_raw not in (None, "", "NULL") else None
        except (ValueError, TypeError):
            target = None
        out.append({
            "ts":         r.get("TS", ""),
            "year":       year,
            "week":       week,
            "week_label": r.get("WEEK_LABEL", ""),
            "target":     target,
            "reasoning":  (r.get("REASONING") or "").strip(),
            "comment":    (r.get("COMMENT") or "").strip(),
            "contacted":  int(r.get("CONTACTED") or 0),
            "rs":         int(r.get("RECRUITER_SCREENS") or 0),
            "act_scr":    int(r.get("ACTUAL_SCREENS") or 0),
            "ats":        int(r.get("ATS") or 0),
            "offered":    int(r.get("OFFERS") or 0),
            "hired":      int(r.get("HIRES") or 0),
        })
    # Sort by year DESC, week DESC, sourcer ASC for stable output
    out.sort(key=lambda e: (-e["year"], -e["week"], e["ts"]))
    return out


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

    # Phase 1: quarterly funnel
    rows = read_input_csv(ci)
    print("[phase1] read " + str(len(rows)) + " weekly rows", flush=True)
    quarterly = aggregate_quarterly(rows)
    print("[phase1] aggregated to " + str(len(quarterly)) + " quarters", flush=True)

    # Phase 2: cost
    cost = []
    try:
        finance = fetch_finance_data(github_token)
        print("[phase2] fetched finance data: " + str(len(finance.get('ea', []))) + " ea records", flush=True)
        cost = compute_cost(finance, quarterly)
        print("[phase2] computed " + str(len(cost)) + " quarterly cost rows", flush=True)
    except Exception as e:
        # If finance pull fails, ship phase 1 alone — better than failing the whole refresh.
        print("[phase2] WARNING: cost computation failed: " + str(e), flush=True)

    # Phase 3: WBR comments (graceful — skip if not wired)
    wbr_comments = []
    try:
        wbr_rows = read_wbr_comments(ci)
        if wbr_rows:
            wbr_comments = build_wbr_comments_array(wbr_rows)
            print("[phase3] built " + str(len(wbr_comments)) + " wbr_comments entries", flush=True)
        else:
            print("[phase3] no WBR input mapping yet — skipping", flush=True)
    except Exception as e:
        print("[phase3] WARNING: wbr_comments build failed: " + str(e), flush=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Keboola sourcing_dashboard_per_sourcer (v1.5) + Finance dashboard ea (ct-based cost classification)",
        "methodology_version": METHODOLOGY_VERSION,
        "quarterly": quarterly,
        "cost": cost,
        "wbr_comments": wbr_comments,
    }
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    sha = push_to_github(github_token, content)
    print("=== done: commit=" + sha[:10] + " size=" + str(len(content) // 1024) + "KB ===", flush=True)
    return 0


print("=== about to call main() ===", flush=True)
_rc = main()
print("=== main() returned " + str(_rc) + " ===", flush=True)
sys.exit(_rc)
