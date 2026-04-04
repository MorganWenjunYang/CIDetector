#!/usr/bin/env python3
"""Search ClinicalTrials.gov via the public REST API v2.

Usage:
    python tools/search_clinical_trials.py --query "B7H4 ADC"
    python tools/search_clinical_trials.py --query "pembrolizumab" --phase "Phase 3" --status RECRUITING
    python tools/search_clinical_trials.py --query "B7H4" --sponsor "Daiichi"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.http_client import fetch_json
from utils.cache import cache_key, get as cache_get, put as cache_put

API_BASE = "https://clinicaltrials.gov/api/v2/studies"
SOURCE = "ClinicalTrials.gov"
RATE_KEY = "clinicaltrials"

FIELDS = [
    "NCTId",
    "BriefTitle",
    "OfficialTitle",
    "OverallStatus",
    "Phase",
    "StartDate",
    "PrimaryCompletionDate",
    "CompletionDate",
    "LeadSponsorName",
    "EnrollmentCount",
    "EnrollmentType",
    "BriefSummary",
    "Condition",
    "InterventionName",
    "InterventionType",
    "StudyType",
]


def _build_params(args: argparse.Namespace) -> dict:
    params: dict = {
        "format": "json",
        "pageSize": min(args.max_results, 100),
        "countTotal": "true",
    }
    if args.query:
        params["query.term"] = args.query
    if args.phase:
        params["filter.phase"] = args.phase
    if args.status:
        params["filter.overallStatus"] = args.status
    if args.sponsor:
        params["query.spons"] = args.sponsor
    return params


def _extract_field(proto: dict, *keys: str) -> str:
    """Walk nested dicts/lists to pull a value from protocolSection."""
    node = proto
    for k in keys:
        if isinstance(node, dict):
            node = node.get(k)
        elif isinstance(node, list):
            return ", ".join(str(i) for i in node)
        else:
            return ""
        if node is None:
            return ""
    if isinstance(node, list):
        return ", ".join(str(i) for i in node)
    return str(node)


def _parse_study(study: dict) -> dict:
    proto = study.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    status_mod = proto.get("statusModule", {})
    sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
    design = proto.get("designModule", {})
    desc = proto.get("descriptionModule", {})
    cond_mod = proto.get("conditionsModule", {})
    arms_mod = proto.get("armsInterventionsModule", {})

    nct_id = ident.get("nctId", "")
    interventions = arms_mod.get("interventions", [])
    intervention_names = [i.get("name", "") for i in interventions]

    return {
        "title": ident.get("briefTitle", ""),
        "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else "",
        "content": desc.get("briefSummary", ""),
        "published_at": (status_mod.get("startDateStruct") or {}).get("date", ""),
        "metadata": {
            "nct_id": nct_id,
            "official_title": ident.get("officialTitle", ""),
            "status": status_mod.get("overallStatus", ""),
            "phase": (design.get("phases") or [""])[0] if design.get("phases") else "",
            "sponsor": (sponsor_mod.get("leadSponsor") or {}).get("name", ""),
            "enrollment": design.get("enrollmentInfo", {}).get("count", ""),
            "conditions": cond_mod.get("conditions", []),
            "interventions": intervention_names,
            "completion_date": (status_mod.get("completionDateStruct") or {}).get("date", ""),
        },
    }


async def search(args: argparse.Namespace) -> dict:
    params = _build_params(args)

    ck = cache_key(SOURCE, params)
    cached = cache_get(ck)
    if cached is not None:
        return cached

    all_items: list[dict] = []
    total = None
    page_token = None

    while True:
        p = dict(params)
        if page_token:
            p["pageToken"] = page_token

        data = await fetch_json(API_BASE, params=p, rate_key=RATE_KEY, rate_limit=3.0)

        if total is None:
            total = data.get("totalCount", 0)

        for study in data.get("studies", []):
            all_items.append(_parse_study(study))

        if len(all_items) >= args.max_results:
            all_items = all_items[: args.max_results]
            break

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    result = {
        "source": SOURCE,
        "query": args.query or "",
        "total_results": total or len(all_items),
        "items": all_items,
    }
    cache_put(ck, result, ttl_seconds=3600)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Search ClinicalTrials.gov")
    parser.add_argument("--query", "-q", required=True, help="Search term")
    parser.add_argument("--phase", help='Phase filter, e.g. "Phase 1", "Phase 2|Phase 3"')
    parser.add_argument("--status", help="Status filter, e.g. RECRUITING, COMPLETED")
    parser.add_argument("--sponsor", help="Sponsor name filter")
    parser.add_argument("--max-results", type=int, default=20, help="Max results (default 20)")
    args = parser.parse_args()

    result = asyncio.run(search(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
