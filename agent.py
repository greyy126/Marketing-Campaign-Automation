#!/usr/bin/env python3
"""
NovaMind Marketing Automation Agent
------------------------------------
Usage:
    python agent.py run --topic "AI in creative automation"
    python agent.py run --topic "AI in creative automation" --dry-run
    python agent.py history
"""

import os
import sys
import json
import click
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.rule import Rule
from src.personas import DISPLAY_TO_SLUG, persona_label

load_dotenv()

console = Console()
PT = ZoneInfo("America/Los_Angeles")


def _persona_label(persona: str) -> str:
    return persona_label(persona)


def _pt_now() -> datetime:
    return datetime.now(PT)


def _newsletter_status_table(newsletters: list[dict]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta")
    table.add_column("Persona", style="cyan", min_width=25)
    table.add_column("Brevo ID", justify="right")
    table.add_column("CRM Status", style="yellow")
    table.add_column("CRM Sent", style="green")

    for nl in newsletters:
        table.add_row(
            _persona_label(nl["persona"]),
            str(nl.get("brevo_campaign_id") or ""),
            nl.get("crm_status") or "unknown",
            (nl.get("crm_sent_at") or "")[:19],
        )
    return table

# ── Guard: check env vars early ───────────────────────────────────────────────

def _require_env(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val or val.startswith("your_"):
        console.print(f"[bold red]Missing env var:[/] {key}  →  add it to your .env file")
        sys.exit(1)
    return val


# ── Output helpers ─────────────────────────────────────────────────────────────

def _save_output(content: dict, campaign_id: int) -> Path:
    out_dir = Path("output") / _pt_now().strftime("%Y-%m-%d_%H-%M")
    out_dir.mkdir(parents=True, exist_ok=True)

    blog_post        = content["blog"]
    newsletters_list = content["newsletters"]   # [{ persona, subject, body }, ...]

    # Blog markdown — hook as bold pre-header, then max 3 ## sections, no ###
    blog_path = out_dir / "blog.md"
    sections  = blog_post.get("sections", [])

    # Groups rendered as ## blocks (order matters; proof merges into problem group)
    CONTENT_GROUPS = [
        ["pain_point", "context", "proof"],
        ["solution", "how_to"],
        ["cta"],
    ]

    sections_by_goal: dict[str, list[dict]] = {}
    for s in sections:
        sections_by_goal.setdefault(s.get("goal", ""), []).append(s)

    blog_lines = [
        f"# {blog_post['title']}",
        f"_Generated: {_pt_now().strftime('%Y-%m-%d')}_",
        "",
        "---",
        "",
    ]

    # Opening hook — bold paragraph before any ## headers
    for s in sections_by_goal.get("hook", []):
        hook_content = s["content"]
        if not hook_content.strip().startswith("**"):
            paras = hook_content.split("\n\n")
            paras[0] = f"**{paras[0].strip()}**"
            hook_content = "\n\n".join(paras)
        blog_lines += [hook_content, "", "---", ""]

    # Main ## sections — content merged directly, no ### sub-headings
    cta_content = None
    assigned: set[str] = {"hook"}

    for goal_list in CONTENT_GROUPS:
        group_sections = []
        for goal in goal_list:
            group_sections.extend(sections_by_goal.get(goal, []))
        assigned.update(goal_list)

        if not group_sections:
            continue

        group_title = group_sections[0]["title"]
        blog_lines += [f"## {group_title}", ""]

        for s in group_sections:
            blog_lines += [s["content"], ""]
            if s.get("goal") == "cta":
                cta_content = s["content"]

        blog_lines += ["---", ""]

    # Catch any unmatched goals
    for s in sections:
        if s.get("goal") not in assigned:
            blog_lines += [f"## {s['title']}", "", s["content"], "", "---", ""]

    if cta_content:
        first_sentence = cta_content.split(".")[0].strip() + "."
        first_sentence = first_sentence.replace("**", "")
        blog_lines += [
            "> **Key Takeaway**",
            f"> {first_sentence}",
            "",
        ]

    blog_path.write_text("\n".join(blog_lines))

    # Newsletters markdown (uses display persona names)
    nl_path  = out_dir / "newsletters.md"
    nl_lines = ["# Newsletters\n"]
    for nl in newsletters_list:
        nl_lines += [
            f"## {nl['persona']}\n",
            f"**Subject:** {nl['subject']}\n",
            nl["body"],
            "\n---\n",
        ]
    nl_path.write_text("\n".join(nl_lines))

    # Full JSON snapshot
    json_path = out_dir / "campaign.json"
    json_path.write_text(json.dumps({"campaign_id": campaign_id, **content}, indent=2))

    return out_dir


def _save_report(summary_result: dict, metrics: list[dict], campaign_id: int,
                 blog_title: str, topic: str, sent_at: str | None) -> Path:
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / f"campaign_{campaign_id}.md"

    ranked = sorted(metrics, key=lambda m: (m["click_rate"], m["open_rate"]), reverse=True)
    now = _pt_now().strftime("%Y-%m-%d %H:%M PT")
    sent_label = sent_at[:10] if sent_at else "unsent"

    def persona_label(persona: str) -> str:
        return _persona_label(persona)

    status = summary_result.get("status", "valid")
    text   = summary_result.get("text", "")

    lines = [
        f"# Campaign Report — {topic}",
        f"_Campaign #{campaign_id} · {topic} · Sent {sent_label} · Last refreshed {now}_",
        "",
        "---",
        "",
        "## Campaign Status",
        f"Status: {status}",
        "",
    ]

    # live: show one-liner inline with status before scorecard
    if status == "live" and text:
        lines += [text, ""]

    lines += [
        "---",
        "",
        "## Segment Scorecard\n",
        "| Persona | Sent | Opens | Clicks | Open% | Click% | Unsub% |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in ranked:
        lines.append(
            f"| {persona_label(m['persona'])} | {m['total_sent']} | {m['opens']} | {m['clicks']} "
            f"| {m['open_rate']:.1%} | {m['click_rate']:.1%} "
            f"| {m['unsubscribe_rate']:.2%} |"
        )

    # low_confidence / valid: insights text (with its own ## headings) after scorecard
    if status != "live" and text:
        lines += ["", text, ""]

    report_path.write_text("\n".join(lines))
    return report_path


def _save_dashboard_report(campaigns: list[dict], dashboard_data: dict,
                           insights_text: str) -> Path:
    """
    Write reports/dashboard.md and return the path.

    Structure:
      1. Summary         — total campaigns, best persona, avg open rate
      2. Persona Performance — all-time averages + trend per persona
      3. Recent Campaigns    — topic, open%, click%, view report link
      4-5. From insights_text (Key Signals, What to Do Next)
    """
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / "dashboard.md"

    now          = _pt_now().strftime("%Y-%m-%d %H:%M PT")
    n            = len(campaigns)
    per_persona  = dashboard_data.get("per_persona", {})
    per_campaign = dashboard_data.get("per_campaign", [])

    def _plabel(p: str) -> str:
        return _persona_label(p)

    # ── Compute summary metrics ────────────────────────────────────────────────
    best_persona = (
        max(per_persona.items(), key=lambda x: x[1]["avg_click"])[0]
        if per_persona else None
    )
    avg_open_all = (
        sum(d["avg_open"] for d in per_persona.values()) / len(per_persona)
        if per_persona else 0
    )

    lines: list[str] = [
        "# NovaMind · Campaign Dashboard",
        "",
        f"_Last updated: {now}_",
        "",
        "---",
        "",
        # ── 1. Summary ─────────────────────────────────────────────────────────
        "## Summary",
        "",
        f"**Total Campaigns:** {n}  ",
        f"**Best Performing Persona:** {_plabel(best_persona) if best_persona else 'N/A'}  ",
        f"**Average Open Rate:** {avg_open_all:.1%}",
        "",
        "---",
        "",
        # ── 2. Persona Performance ─────────────────────────────────────────────
        "## Persona Performance",
        "",
    ]

    if per_persona:
        lines += [
            "| Persona | Campaigns | Avg Open% | Avg Click% | Avg Unsub% | Trend |",
            "|---|---|---|---|---|---|",
        ]
        for p, d in sorted(per_persona.items(), key=lambda x: x[1]["avg_click"], reverse=True):
            trend_str = d["trend"] if d["count"] >= 2 else "n/a"
            lines.append(
                f"| {_plabel(p)} | {d['count']} | {d['avg_open']:.1%} "
                f"| {d['avg_click']:.1%} | {d['avg_unsub']:.2%} | {trend_str} |"
            )
    else:
        lines.append("_No engagement data yet._")

    lines += ["", "---", ""]

    # ── 3-4. Key Signals + Recommended Actions (from insights_text) ───────────
    if insights_text:
        lines += [insights_text, "", "---", ""]

    # ── 5. Recent Campaigns ────────────────────────────────────────────────────
    lines += ["## Recent Campaigns", ""]
    recent = sorted(campaigns, key=lambda c: c["created_at"], reverse=True)[:5]
    if recent:
        pc_lookup = {c["campaign_id"]: c for c in per_campaign}
        lines += [
            "| Campaign | Sent | Open % | Click % | Unsub % | Report |",
            "|---|---|---|---|---|---|",
        ]
        for c in recent:
            sent      = c.get("sent_at", "")[:10] if c.get("sent_at") else "unsent"
            pc        = pc_lookup.get(c["id"], {})
            open_str  = f"{pc['avg_open']:.1%}"  if pc else "—"
            click_str = f"{pc['avg_click']:.1%}" if pc else "—"
            unsub_str = f"{pc['avg_unsub']:.2%}" if pc else "—"
            lines.append(
                f"| {c['topic']} | {sent} | {open_str} | {click_str} | {unsub_str} "
                f"| [View](campaign_{c['id']}.md) |"
            )

    report_path.write_text("\n".join(lines))
    return report_path


def _build_historical_context(rows: list[dict]) -> str | None:
    """Compress recent campaign performance into a short prompt-ready summary."""
    if not rows:
        return None

    # rows arrive ORDER BY recorded_at DESC — first occurrence per persona is the most recent
    latest_by_persona: dict[str, dict] = {}
    for row in rows:
        if row["persona"] not in latest_by_persona:
            latest_by_persona[row["persona"]] = row

    if not latest_by_persona:
        return None

    lines = ["Use these recent results as framing guidance, not as factual claims:"]
    for persona, row in sorted(latest_by_persona.items()):
        lines.append(
            f"- {_persona_label(persona)}: recent open {row['open_rate']:.1%}, click {row['click_rate']:.1%}, "
            f"unsubscribe {row['unsubscribe_rate']:.2%} on campaign '{row['blog_title']}'"
        )
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@click.option("--topic", required=True, help='Blog topic, e.g. "AI in creative automation"')
@click.option("--dry-run", is_flag=True, default=False,
              help="Generate content only; skip CRM and campaign steps")
@click.option("--mock-ai", is_flag=True, default=False,
              help="Use hardcoded mock content — no Anthropic API key needed")
def run(topic: str, dry_run: bool, mock_ai: bool):
    """Run the full NovaMind marketing pipeline for a given topic."""

    if not mock_ai:
        anthropic_key = _require_env("ANTHROPIC_API_KEY")

    brevo_key = None if dry_run else _require_env("BREVO_API_KEY")

    # Lazy imports so --history works without Anthropic key
    from src import database as db
    from src.content_generator  import ContentGenerator, MockContentGenerator
    from src.crm_manager        import CRMManager
    from src.campaign_manager   import CampaignManager
    from src.performance_tracker import simulate_metrics
    from src.mock_data          import MOCK_CONTACTS, PERSONAS

    db.init_db()
    historical_context = _build_historical_context(db.get_all_campaign_metrics())

    # ── Step 1: Generate content ───────────────────────────────────────────────
    console.print(Rule("[bold cyan]Step 1 · AI Content Generation[/]"))
    if mock_ai:
        console.print("  [yellow]--mock-ai active — skipping Claude API calls[/]")
        gen = MockContentGenerator()
    else:
        gen = ContentGenerator(api_key=anthropic_key)

    console.print(f"  Generating blog post for: [italic]{topic}[/]")
    content = gen.generate_content(topic, historical_context=historical_context)

    blog_post        = content["blog"]
    newsletters_list = content["newsletters"]   # [{ persona, subject, body }, ...]

    console.print(f"  [green]✓[/] Outline: {len(content['outline'])} sections")
    for item in content["outline"]:
        console.print(f"    [dim]{item['goal']:12}[/] {item['title']}")
    console.print(f"  [green]✓[/] Blog: [bold]{blog_post['title']}[/] "
                  f"[dim]({len(blog_post['draft'].split())} words)[/]")
    for nl in newsletters_list:
        console.print(f"  [green]✓[/] Newsletter → {nl['persona']}")

    # Slug-keyed dict for CRM + campaign manager
    newsletters = {
        DISPLAY_TO_SLUG[nl["persona"]]: {"subject": nl["subject"], "body": nl["body"]}
        for nl in newsletters_list
        if nl["persona"] in DISPLAY_TO_SLUG
    }

    # ── Save to DB ─────────────────────────────────────────────────────────────
    campaign_id = db.save_campaign(
        topic        = topic,
        blog_title   = blog_post["title"],
        blog_draft   = blog_post["draft"],
        blog_outline = json.dumps(blog_post["outline"]),
    )

    out_dir = _save_output(content, campaign_id)
    console.print(f"\n  [dim]Content saved → {out_dir}[/]")

    if dry_run:
        console.print("\n[yellow]Dry-run mode — skipping CRM + campaign steps.[/]")
        return

    # ── Step 2: CRM setup ─────────────────────────────────────────────────────
    console.print(Rule("[bold cyan]Step 2 · CRM & Segmentation[/]"))
    crm = CRMManager(api_key=brevo_key)

    account = crm.get_account()
    sender_email = account["email"]
    console.print(f"  Brevo account: [bold]{account.get('companyName', sender_email)}[/] ({sender_email})")

    console.print("  Setting up persona lists …")
    list_ids = crm.setup_persona_lists(PERSONAS)
    for slug, lid in list_ids.items():
        console.print(f"  [green]✓[/] List [{lid}] → {slug}")

    console.print(f"  Upserting {len(MOCK_CONTACTS)} mock contacts …")
    count = crm.upsert_contacts_bulk(MOCK_CONTACTS, list_ids)
    console.print(f"  [green]✓[/] {count} contacts synced to Brevo")

    # ── Step 3: Create campaigns ───────────────────────────────────────────────
    console.print(Rule("[bold cyan]Step 3 · Campaign Creation[/]"))
    camp_mgr = CampaignManager(api_key=brevo_key, sender_email=sender_email)

    brevo_campaign_ids = camp_mgr.create_all_campaigns(
        newsletters        = newsletters,
        list_ids_by_persona = list_ids,
        blog_title         = blog_post["title"],
        personas_meta      = PERSONAS,
    )

    for persona, newsletter in newsletters.items():
        brevo_id = brevo_campaign_ids[persona]
        db.save_newsletter(
            campaign_id       = campaign_id,
            persona           = persona,
            subject           = newsletter["subject"],
            body              = newsletter["body"],
            brevo_campaign_id = brevo_id,
            brevo_list_id     = list_ids[persona],
            crm_status        = "draft",
        )
        console.print(f"  [green]✓[/] Campaign #{brevo_id} created → {persona}")

    console.print("  Sending campaigns …")
    camp_mgr.send_all_campaigns(brevo_campaign_ids)
    for persona, brevo_id in brevo_campaign_ids.items():
        state = camp_mgr.get_campaign_state(brevo_id)
        db.update_newsletter_crm_state(campaign_id, persona, **state)
        try:
            crm.log_campaign_note(
                topic=topic,
                blog_title=blog_post["title"],
                persona_slug=persona,
                persona_label=PERSONAS[persona]["label"],
                brevo_campaign_id=brevo_id,
                brevo_list_id=list_ids.get(persona),
                crm_status=state.get("crm_status"),
                crm_sent_at=state.get("crm_sent_at"),
                crm_status_reason=state.get("crm_status_reason"),
            )
        except Exception as exc:
            console.print(
                f"  [yellow]![/] CRM note logging failed for {persona}: {exc}"
            )
        console.print(
            f"  [green]✓[/] {persona} → Brevo #{brevo_id} status: "
            f"{state.get('crm_status') or 'unknown'}"
        )

    db.mark_campaign_sent(campaign_id)
    console.print(f"\n  [dim]Emails dispatched to real contacts via Brevo[/]")
    console.print(_newsletter_status_table(db.get_newsletters_for_campaign(campaign_id)))

    # ── Step 4: Record baseline metrics ───────────────────────────────────────
    console.print(Rule("[bold cyan]Step 4 · Baseline Metrics[/]"))
    metrics = simulate_metrics(campaign_id, seed=campaign_id, brevo_api_key=brevo_key)

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta")
    table.add_column("Persona",    style="cyan",  min_width=25)
    table.add_column("Sent",       justify="right")
    table.add_column("Opens",      justify="right")
    table.add_column("Clicks",     justify="right")
    table.add_column("Open %",     justify="right", style="green")
    table.add_column("Click %",    justify="right", style="green")
    table.add_column("Unsub %",    justify="right", style="yellow")

    for m in metrics:
        table.add_row(
            _persona_label(m["persona"]),
            str(m["total_sent"]),
            str(m["opens"]),
            str(m["clicks"]),
            f"{m['open_rate']:.1%}",
            f"{m['click_rate']:.1%}",
            f"{m['unsubscribe_rate']:.2%}",
        )
    console.print(table)

    console.print("  Generating initial campaign report …")
    summary_result = gen.generate_performance_summary(metrics, blog_post["title"])
    report_path = _save_report(
        summary_result, metrics,
        campaign_id=campaign_id,
        blog_title=blog_post["title"],
        topic=topic,
        sent_at=db.get_campaign(campaign_id).get("sent_at"),
    )
    console.print(f"  [dim]Campaign report saved → {report_path}[/]")
    console.print(f"  [dim]Run refresh-stats --campaign-id {campaign_id} after recipients engage.[/]")

    # ── Auto-update collective dashboard ──────────────────────────────────────
    from src.performance_tracker import build_dashboard_data
    _all_campaigns = [c for c in db.get_all_campaigns() if c["status"] == "sent"]
    _sent_ids      = {c["id"] for c in _all_campaigns}
    _all_metrics   = [r for r in db.get_all_campaign_metrics() if r["campaign_id"] in _sent_ids]
    _dash_data     = build_dashboard_data(_all_metrics)
    _dash_insights = MockContentGenerator().generate_dashboard_insights(_dash_data) if _all_metrics else ""
    _dash_path     = _save_dashboard_report(_all_campaigns, _dash_data, _dash_insights)
    console.print(f"  [dim]Dashboard updated → {_dash_path}[/]")

    console.print(Rule("[bold green]Pipeline complete ✓[/]"))


@cli.command()
def history():
    """Print a table of all historical campaign performance metrics."""
    from src import database as db
    from src.performance_tracker import get_historical_summary
    db.init_db()
    console.print(get_historical_summary())


@cli.command("refresh-stats")
@click.option("--campaign-id", required=True, type=int, help="Campaign ID to refresh stats for")
def refresh_stats(campaign_id: int):
    """Fetch live Brevo engagement stats, update the DB, and generate a performance report."""
    brevo_key = _require_env("BREVO_API_KEY")

    from src import database as db
    from src.content_generator import ContentGenerator
    from src.campaign_manager import CampaignManager
    from src.performance_tracker import refresh_metrics
    db.init_db()

    campaign = db.get_campaign(campaign_id)
    if not campaign:
        console.print(f"[bold red]Error:[/] Campaign #{campaign_id} not found.")
        sys.exit(1)

    blog_title = campaign["blog_title"]
    newsletters = db.get_newsletters_for_campaign(campaign_id)
    if newsletters:
        camp_mgr = CampaignManager(api_key=brevo_key, sender_email="noreply@novamind.local")
        for nl in newsletters:
            brevo_id = nl.get("brevo_campaign_id")
            if not brevo_id:
                continue
            state = camp_mgr.get_campaign_state(brevo_id)
            db.update_newsletter_crm_state(campaign_id, nl["persona"], **state)

    console.print(Rule(f"[bold cyan]Refreshing stats · Campaign #{campaign_id}[/]"))
    try:
        metrics = refresh_metrics(campaign_id, brevo_key)
    except ValueError as e:
        console.print(f"[bold red]Error:[/] {e}")
        sys.exit(1)

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta")
    table.add_column("Persona",  style="cyan", min_width=25)
    table.add_column("Sent",     justify="right")
    table.add_column("Opens",    justify="right")
    table.add_column("Clicks",   justify="right")
    table.add_column("Open %",   justify="right", style="green")
    table.add_column("Click %",  justify="right", style="green")
    table.add_column("Unsub %",  justify="right", style="yellow")

    for m in metrics:
        table.add_row(
            _persona_label(m["persona"]),
            str(m["total_sent"]),
            str(m["opens"]),
            str(m["clicks"]),
            f"{m['open_rate']:.1%}",
            f"{m['click_rate']:.1%}",
            f"{m['unsubscribe_rate']:.2%}",
        )
    console.print(table)
    console.print(f"  [dim]Stats updated in DB from Brevo[/]")
    console.print(_newsletter_status_table(db.get_newsletters_for_campaign(campaign_id)))

    anthropic_key = _require_env("ANTHROPIC_API_KEY")
    console.print("  Analysing engagement data …")
    gen = ContentGenerator(api_key=anthropic_key)
    result = gen.generate_performance_summary(metrics, blog_title)

    console.print(Panel(result["text"], title=f"[bold]NovaMind Growth Analyst · {result['status'].replace('_', ' ').title()}[/]",
                        border_style="cyan", padding=(1, 2)))

    report_path = _save_report(
        result, metrics,
        campaign_id=campaign_id,
        blog_title=blog_title,
        topic=campaign["topic"],
        sent_at=campaign.get("sent_at"),
    )
    console.print(f"\n  [dim]Dashboard updated → {report_path}[/]")


@cli.command()
@click.option("--mock-ai", is_flag=True, default=False,
              help="Use heuristic insights — no Anthropic API key needed")
def dashboard(mock_ai: bool):
    """
    Refresh the collective dashboard with full AI analysis.

    The dashboard auto-updates after every `run`. Use this command explicitly
    to regenerate the AI-powered insights sections (Key Signals and
    Recommended Actions). Only sent campaigns with recorded metrics are
    included. Requires ANTHROPIC_API_KEY unless --mock-ai is set.
    """
    from src import database as db
    from src.content_generator import ContentGenerator, MockContentGenerator
    from src.performance_tracker import build_dashboard_data
    db.init_db()

    campaigns = [c for c in db.get_all_campaigns() if c["status"] == "sent"]
    if not campaigns:
        console.print("[yellow]No sent campaigns yet. Run[/] python agent.py run --topic '...' [yellow]to create one.[/]")
        return

    sent_ids       = {c["id"] for c in campaigns}
    rows           = [r for r in db.get_all_campaign_metrics() if r["campaign_id"] in sent_ids]
    dashboard_data = build_dashboard_data(rows)
    per_persona    = dashboard_data.get("per_persona", {})

    # ── Terminal persona table ─────────────────────────────────────────────────
    table = Table(
        title="Persona Performance (All-Time Averages)",
        box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta",
    )
    table.add_column("Persona",    style="cyan",  min_width=25)
    table.add_column("Campaigns",  justify="right")
    table.add_column("Avg Open%",  justify="right", style="green")
    table.add_column("Avg Click%", justify="right", style="green")
    table.add_column("Avg Unsub%", justify="right", style="yellow")
    table.add_column("Trend",      justify="center")

    for p, d in sorted(per_persona.items(), key=lambda x: x[1]["avg_click"], reverse=True):
        trend_str = d["trend"] if d["count"] >= 2 else "n/a"
        table.add_row(
            _persona_label(p),
            str(d["count"]),
            f"{d['avg_open']:.1%}",
            f"{d['avg_click']:.1%}",
            f"{d['avg_unsub']:.2%}",
            trend_str,
        )
    console.print(table)

    # ── Generate insights ──────────────────────────────────────────────────────
    insights = ""
    if rows:
        if mock_ai:
            gen = MockContentGenerator()
        else:
            anthropic_key = _require_env("ANTHROPIC_API_KEY")
            gen = ContentGenerator(api_key=anthropic_key)
            console.print("  Generating AI insights …")
        insights = gen.generate_dashboard_insights(dashboard_data)

    report_path = _save_dashboard_report(campaigns, dashboard_data, insights)
    console.print(f"\n  [dim]Dashboard saved → {report_path}[/]")


if __name__ == "__main__":
    cli()
