"""
Performance simulation + storage + reporting.

In production you'd call Brevo's GET /v3/emailCampaigns/{id} endpoint
to pull real open/click stats.  Here we simulate realistic per-persona
numbers using the benchmarks defined in mock_data.py.
"""

import random
import requests
from src.mock_data import PERFORMANCE_BENCHMARKS, MOCK_CONTACTS
from src import database as db
from src.personas import canonical_persona_slug, persona_label

BREVO_BASE = "https://api.brevo.com/v3"


def _contacts_per_persona() -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in MOCK_CONTACTS:
        counts[c["persona"]] = counts.get(c["persona"], 0) + 1
    return counts


def _fetch_brevo_stats(brevo_campaign_id: int, api_key: str) -> dict | None:
    """
    Fetch real engagement stats from Brevo for one campaign.
    Returns {opens, clicks, unsubscribes} or None on failure.

    Brevo endpoint: GET /v3/emailCampaigns/{campaignId}
    Relevant fields: statistics.globalStats.uniqueViews (opens),
                     statistics.globalStats.clickers (clicks),
                     statistics.globalStats.unsubscriptions
    """
    try:
        r = requests.get(
            f"{BREVO_BASE}/emailCampaigns/{brevo_campaign_id}",
            headers={"api-key": api_key, "accept": "application/json"},
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        # globalStats is unpopulated on free/small campaigns — aggregate campaignStats instead
        campaign_stats = data.get("statistics", {}).get("campaignStats", [])
        opens        = sum(s.get("uniqueViews", 0)      for s in campaign_stats)
        clicks       = sum(s.get("clickers", 0)         for s in campaign_stats)
        unsubscribes = sum(s.get("unsubscriptions", 0)  for s in campaign_stats)
        return {"opens": opens, "clicks": clicks, "unsubscribes": unsubscribes}
    except Exception:
        return None


def simulate_metrics(campaign_id: int, seed: int | None = None,
                     brevo_api_key: str | None = None) -> list[dict]:
    """
    Generate engagement metrics for each persona, persist them, and return the list.

    Pass seed=campaign_id for deterministic results per campaign.
    Pass brevo_api_key to attempt real Brevo polling once _fetch_brevo_stats is implemented;
    falls back to simulation if the fetch returns None.
    """
    if seed is not None:
        random.seed(seed)

    brevo_ids = db.get_brevo_ids_for_campaign(campaign_id) if brevo_api_key else {}
    contact_counts = _contacts_per_persona()
    results = []

    for persona, benchmarks in PERFORMANCE_BENCHMARKS.items():
        total = contact_counts.get(persona, 5)

        brevo_id = brevo_ids.get(persona)
        real = _fetch_brevo_stats(brevo_id, brevo_api_key) if brevo_id and brevo_api_key else None

        if real:
            opens        = real["opens"]
            clicks       = real["clicks"]
            unsubscribes = real["unsubscribes"]
        else:
            open_rate  = random.uniform(*benchmarks["open_rate"])
            click_rate = random.uniform(*benchmarks["click_rate"])
            unsub_rate = random.uniform(*benchmarks["unsubscribe_rate"])
            opens        = round(total * open_rate)
            clicks       = round(total * click_rate)
            unsubscribes = max(0, round(total * unsub_rate))

        db.save_metrics(
            campaign_id=campaign_id,
            persona=persona,
            total_sent=total,
            opens=opens,
            clicks=clicks,
            unsubscribes=unsubscribes,
        )

        results.append(
            {
                "persona": persona,
                "total_sent": total,
                "opens": opens,
                "clicks": clicks,
                "unsubscribes": unsubscribes,
                "open_rate": opens / total,
                "click_rate": clicks / total,
                "unsubscribe_rate": unsubscribes / total,
            }
        )

    return results


def refresh_metrics(campaign_id: int, brevo_api_key: str) -> list[dict]:
    """
    Fetch live Brevo stats for every persona in a campaign, update the DB,
    and return the refreshed metric dicts.

    Raises ValueError if no Brevo campaign IDs are found for the given campaign.
    """
    brevo_ids = db.get_brevo_ids_for_campaign(campaign_id)
    if not brevo_ids:
        raise ValueError(
            f"No Brevo campaign IDs found for campaign {campaign_id}. "
            "Was the campaign created with a real Brevo key?"
        )

    existing = {r["persona"]: r for r in db.get_metrics_for_campaign(campaign_id)}
    results = []

    for persona, brevo_id in brevo_ids.items():
        stats = _fetch_brevo_stats(brevo_id, brevo_api_key)
        if stats is None:
            # Brevo fetch failed — keep the stored values unchanged
            if persona in existing:
                results.append(dict(existing[persona]))
            continue

        total = existing[persona]["total_sent"] if persona in existing else 1
        opens, clicks, unsubscribes = stats["opens"], stats["clicks"], stats["unsubscribes"]

        db.update_metrics(campaign_id, persona, total, opens, clicks, unsubscribes)
        results.append({
            "persona":          persona,
            "total_sent":       total,
            "opens":            opens,
            "clicks":           clicks,
            "unsubscribes":     unsubscribes,
            "open_rate":        opens        / total if total else 0,
            "click_rate":       clicks       / total if total else 0,
            "unsubscribe_rate": unsubscribes / total if total else 0,
        })

    return results


def _canonical(slug: str) -> str:
    return canonical_persona_slug(slug)


def build_dashboard_data(rows: list[dict]) -> dict:
    """
    Aggregate all campaign metric rows (from db.get_all_campaign_metrics()) into
    per-persona averages/trends and per-campaign averages ranked by engagement.

    Returns:
        {
            "per_persona": {slug: {"count", "avg_open", "avg_click", "avg_unsub", "trend"}},
            "per_campaign": [{"campaign_id", "blog_title", "topic", "sent_at",
                              "avg_open", "avg_click"}, ...] ranked best → worst
        }
    """
    if not rows:
        return {"per_persona": {}, "per_campaign": []}

    # --- per_persona aggregation ---
    # rows arrive ORDER BY recorded_at DESC from get_all_campaign_metrics;
    # group and sort ascending for trend calculation
    from collections import defaultdict
    persona_rows: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        persona_rows[_canonical(r["persona"])].append(r)

    per_persona = {}
    for persona, prows in persona_rows.items():
        prows_asc = sorted(prows, key=lambda x: x["recorded_at"])
        count     = len(prows_asc)
        avg_open  = sum(r["open_rate"]        for r in prows_asc) / count
        avg_click = sum(r["click_rate"]       for r in prows_asc) / count
        avg_unsub = sum(r["unsubscribe_rate"] for r in prows_asc) / count

        if count >= 2:
            diff = prows_asc[-1]["open_rate"] - prows_asc[0]["open_rate"]
            trend = "up" if diff > 0.05 else ("down" if diff < -0.05 else "flat")
        else:
            trend = "flat"

        per_persona[persona] = {
            "count":     count,
            "avg_open":  avg_open,
            "avg_click": avg_click,
            "avg_unsub": avg_unsub,
            "trend":     trend,
        }

    # --- per_campaign aggregation ---
    campaign_rows: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        campaign_rows[r["campaign_id"]].append({**r, "persona": _canonical(r["persona"])})

    per_campaign = []
    for cid, crows in campaign_rows.items():
        count     = len(crows)
        avg_open  = sum(r["open_rate"]  for r in crows) / count
        avg_click = sum(r["click_rate"] for r in crows) / count
        avg_unsub = sum(r["unsubscribe_rate"] for r in crows) / count
        per_campaign.append({
            "campaign_id": cid,
            "blog_title":  crows[0]["blog_title"],
            "topic":       crows[0]["topic"],
            "sent_at":     crows[0].get("sent_at"),
            "avg_open":    avg_open,
            "avg_click":   avg_click,
            "avg_unsub":   avg_unsub,
        })

    per_campaign.sort(key=lambda x: (x["avg_click"], x["avg_open"]), reverse=True)
    return {"per_persona": per_persona, "per_campaign": per_campaign}


def get_historical_summary() -> str:
    """Return a plain-text table of all past campaign metrics."""
    rows = db.get_all_campaign_metrics()
    if not rows:
        return "No historical data yet."

    lines = [
        f"{'Blog':<35} {'Persona':<25} {'Open':>6} {'Click':>6} {'Unsub':>6}",
        "-" * 82,
    ]
    for r in rows:
        label = persona_label(r["persona"])
        lines.append(
            f"{r['blog_title'][:34]:<35} {label:<25} "
            f"{r['open_rate']:>5.1%} {r['click_rate']:>5.1%} {r['unsubscribe_rate']:>5.2%}"
        )
    return "\n".join(lines)
