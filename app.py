from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv
from src.personas import CANONICAL_PERSONA_LABELS, PERSONA_DISPLAY_NAMES, persona_label


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
REPORTS_DIR = ROOT / "reports"

load_dotenv()


# ── CSS ────────────────────────────────────────────────────────────────────────


def inject_css() -> None:
    st.markdown(
        """
        <style>
        /* Page padding */
        .block-container {
            padding-top: 1.25rem;
            padding-bottom: 2rem;
        }

        /* Content typography — blogs, newsletters, reports */
        div[role="tabpanel"] div[data-testid="stMarkdownContainer"] p,
        div[role="tabpanel"] div[data-testid="stMarkdownContainer"] li,
        div[role="tabpanel"] div[data-testid="stMarkdownContainer"] blockquote,
        div[role="tabpanel"] div[data-testid="stMarkdownContainer"] td,
        div[role="tabpanel"] div[data-testid="stMarkdownContainer"] th {
            font-size: 0.87rem;
            line-height: 1.6;
            text-align: justify;
        }
        div[role="tabpanel"] div[data-testid="stMarkdownContainer"] h1 {
            font-size: 1.3rem;
            margin-top: 0.4rem;
            margin-bottom: 0.5rem;
        }
        div[role="tabpanel"] div[data-testid="stMarkdownContainer"] h2 {
            font-size: 1.05rem;
            margin-top: 0.8rem;
            margin-bottom: 0.35rem;
        }
        div[role="tabpanel"] div[data-testid="stMarkdownContainer"] h3 {
            font-size: 0.92rem;
            margin-top: 0.6rem;
            margin-bottom: 0.25rem;
        }

        /* Pipeline status card */
        .pipeline-card {
            border: 1px solid rgba(49, 51, 63, 0.15);
            border-radius: 0.5rem;
            padding: 0.9rem 1.1rem;
            margin-top: 0.8rem;
            margin-bottom: 0.5rem;
        }
        .pipeline-card h4 {
            margin: 0 0 0.65rem 0;
            font-size: 0.9rem;
            font-weight: 600;
            letter-spacing: 0.01em;
        }
        .pipeline-step {
            margin-bottom: 0.6rem;
        }
        .pipeline-step strong {
            display: block;
            font-size: 0.85rem;
            font-weight: 600;
            margin-bottom: 0.2rem;
        }
        .pipeline-step div {
            font-size: 0.82rem;
            line-height: 1.45;
            padding-left: 0.6rem;
            color: #555;
        }
        .pipeline-hints {
            font-size: 0.78rem;
            color: #999;
            margin-top: 0.65rem;
            border-top: 1px solid rgba(49,51,63,0.08);
            padding-top: 0.5rem;
        }
        .pipeline-hints span { margin-right: 1.2rem; }

        /* Previous runs list */
        .run-meta {
            font-size: 0.78rem;
            color: #888;
            margin-top: 0.1rem;
        }

        /* Approval flow — stepper */
        .stepper {
            display: flex;
            margin: 0.8rem 0 0.6rem 0;
            border: 1px solid rgba(49,51,63,0.1);
            border-radius: 0.6rem;
            overflow: hidden;
            background: #fafafa;
        }
        .stepper-step {
            flex: 1;
            padding: 0.8rem 1rem;
            border-right: 1px solid rgba(49,51,63,0.08);
        }
        .stepper-step:last-child { border-right: none; }
        .stepper-step.s-active   { background: #eff6ff; }
        .stepper-step.s-done     { background: #f0fdf4; }
        .stepper-step.s-awaiting { background: #fffbeb; }
        .stepper-step.s-pending  { background: #fafafa; }
        .stepper-seq {
            font-size: 0.68rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #9ca3af;
            margin-bottom: 0.18rem;
        }
        .stepper-name {
            font-size: 0.86rem;
            font-weight: 700;
            margin-bottom: 0.22rem;
            color: #111;
        }
        .stepper-name.s-pending { color: #9ca3af; }
        .stepper-badge {
            font-size: 0.72rem;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 3px;
        }
        .stepper-badge.s-pending  { color: #9ca3af; }
        .stepper-badge.s-active   { color: #2563eb; }
        .stepper-badge.s-awaiting { color: #d97706; }
        .stepper-badge.s-done     { color: #16a34a; }
        .stepper-feedback {
            font-size: 0.78rem;
            color: #6b7280;
            margin: 0.3rem 0 0.8rem 0;
            min-height: 1.1rem;
        }

        /* Approval flow — step action panel */
        .step-panel {
            border: 1px solid rgba(49,51,63,0.12);
            border-radius: 0.6rem;
            padding: 1.1rem 1.2rem;
            margin: 0.25rem 0 1rem 0;
            background: #fafafa;
        }
        .step-panel-title {
            font-size: 0.95rem;
            font-weight: 600;
            margin-bottom: 0.15rem;
        }
        .step-panel-sub {
            font-size: 0.8rem;
            color: #6b7280;
            margin-bottom: 0.9rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ── Data helpers ───────────────────────────────────────────────────────────────


def get_latest_output_dir() -> Path | None:
    if not OUTPUT_DIR.exists():
        return None
    folders = sorted(
        [p for p in OUTPUT_DIR.iterdir() if p.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    return folders[0] if folders else None


def read_text_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def load_latest_campaign_id() -> int | None:
    try:
        import sqlite3 as _sqlite3

        db_path = ROOT / "data" / "novamind.db"
        if not db_path.exists():
            return None
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        row = conn.execute(
            """
            SELECT id
            FROM campaigns
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        conn.close()
        if row is None:
            return None
        campaign_id = row["id"]
        return campaign_id if isinstance(campaign_id, int) else None
    except Exception:
        return None


def get_output_dir_for_campaign(campaign_id: int | None) -> Path | None:
    if campaign_id is None or not OUTPUT_DIR.exists():
        return None

    for folder in sorted(OUTPUT_DIR.iterdir(), key=lambda p: p.name, reverse=True):
        if not folder.is_dir():
            continue
        campaign_json = folder / "campaign.json"
        if not campaign_json.exists():
            continue
        try:
            data = json.loads(campaign_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("campaign_id") == campaign_id:
            return folder
    return None


def load_campaign_json(campaign_id: int | None = None) -> dict | None:
    campaign_dir = get_output_dir_for_campaign(campaign_id)
    if campaign_dir is None:
        return None
    campaign_json = campaign_dir / "campaign.json"
    if not campaign_json.exists():
        return None
    try:
        return json.loads(campaign_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_campaign_record(campaign_id: int | None) -> dict | None:
    if campaign_id is None:
        return None
    try:
        import sqlite3 as _sqlite3

        db_path = ROOT / "data" / "novamind.db"
        if not db_path.exists():
            return None
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        row = conn.execute(
            "SELECT * FROM campaigns WHERE id=?",
            (campaign_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def get_all_runs(hidden_campaign_ids: set[int] | None = None) -> list[dict]:
    """Return completed historical runs, newest first."""
    if not OUTPUT_DIR.exists():
        return []
    hidden_campaign_ids = hidden_campaign_ids or set()
    runs = []
    for folder in OUTPUT_DIR.iterdir():
        if not folder.is_dir():
            continue
        cj = folder / "campaign.json"
        if not cj.exists():
            continue
        try:
            data = json.loads(cj.read_text(encoding="utf-8"))
            campaign_id = data.get("campaign_id")
            if not isinstance(campaign_id, int) or campaign_id in hidden_campaign_ids:
                continue

            campaign_record = load_campaign_record(campaign_id)
            if not campaign_record:
                continue
            if campaign_record.get("status") != "sent" or not campaign_record.get("sent_at"):
                continue

            has_blog = (folder / "blog.md").exists()
            has_newsletters = (folder / "newsletters.md").exists()
            if not (has_blog and has_newsletters):
                continue

            blog_title = (data.get("blog") or {}).get("title") or "Untitled"
            # Use file mtime in local time — avoids PT-vs-local timezone confusion
            mtime = cj.stat().st_mtime
            local_dt = datetime.fromtimestamp(mtime)
            ts_display = local_dt.strftime("%Y-%m-%d  %H:%M")
            runs.append(
                {
                    "folder": folder,
                    "ts_display": ts_display,
                    "mtime": mtime,
                    "blog_title": blog_title,
                    "campaign_id": campaign_id,
                    "has_blog": has_blog,
                    "has_newsletters": has_newsletters,
                    "has_report": (REPORTS_DIR / f"campaign_{campaign_id}.md").exists(),
                }
            )
        except (json.JSONDecodeError, OSError):
            continue
    runs.sort(key=lambda r: r["mtime"], reverse=True)
    return runs


# ── Shell helpers ──────────────────────────────────────────────────────────────


def run_command(args: list[str]) -> dict[str, str | int]:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def enqueue_stream(stream, output_queue: queue.Queue, stream_name: str) -> None:
    try:
        for line in iter(stream.readline, ""):
            output_queue.put((stream_name, line))
    finally:
        stream.close()


# ── Session state ──────────────────────────────────────────────────────────────


def init_session_state() -> None:
    st.session_state.setdefault("last_run_mode", None)
    st.session_state.setdefault("last_command_result", None)
    st.session_state.setdefault("last_command_label", None)
    st.session_state.setdefault("last_pipeline_result", None)
    st.session_state.setdefault("campaign_report_sync_status", None)
    st.session_state.setdefault("active_process", None)
    st.session_state.setdefault("active_queue", None)
    st.session_state.setdefault("active_stdout", "")
    st.session_state.setdefault("active_stderr", "")
    st.session_state.setdefault("active_label", None)
    st.session_state.setdefault("active_topic", "")
    st.session_state.setdefault("pipeline_started", False)
    st.session_state.setdefault("suggested_topics_refresh_nonce", 0)
    st.session_state.setdefault("suggested_topics_computed_nonce", -1)
    st.session_state.setdefault("last_suggested_topics", ())
    st.session_state.setdefault("pipeline_stage", "idle")
    st.session_state.setdefault("pending_campaign_id", None)
    st.session_state.setdefault("pending_mock_ai", False)
    st.session_state.setdefault("distribute_completed", False)
    st.session_state.setdefault("session_campaign_ids", set())
    st.session_state.setdefault("cached_previous_runs", None)
    st.session_state.setdefault("pipeline_error", None)


# ── UI helpers ─────────────────────────────────────────────────────────────────


def show_command_result(label: str, result: dict[str, str | int]) -> None:
    returncode = int(result["returncode"])
    if returncode == 0:
        st.success(f"{label} completed successfully.")
    else:
        st.error(f"{label} failed.")
    with st.expander(f"{label} logs"):
        stdout = str(result["stdout"])
        stderr = str(result["stderr"])
        if stdout.strip():
            st.code(stdout, language="text")
        if stderr.strip():
            st.code(stderr, language="text")
        if not stdout.strip() and not stderr.strip():
            st.caption("No command output.")


def show_markdown_file(path: Path, empty_message: str) -> None:
    content = read_text_file(path)
    if content is None:
        st.info(empty_message)
        return
    if path.parent == REPORTS_DIR:
        content = content.replace("# Campaign Dashboard —", "# Campaign Report —")
    st.markdown(content)


def parse_report_header(text: str) -> tuple[str | None, str | None]:
    title = None
    subtitle = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and title is None:
            title = stripped[2:].strip()
        elif stripped.startswith("_") and stripped.endswith("_") and subtitle is None:
            subtitle = stripped.strip("_").strip()
            break
    return title, subtitle


def parse_md_sections(text: str) -> dict[str, str]:
    """Parse ## headings into {heading_text: body_text}. Strips leading/trailing --- from body."""
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    def _flush():
        if current_heading:
            body_lines = current_lines[:]
            while body_lines and body_lines[0].strip() in ("", "---"):
                body_lines.pop(0)
            while body_lines and body_lines[-1].strip() in ("", "---"):
                body_lines.pop()
            sections[current_heading] = "\n".join(body_lines).strip()

    for line in text.splitlines():
        if line.startswith("## "):
            _flush()
            current_heading = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    _flush()
    return sections


def render_key_signals_section(text: str) -> None:
    entries: list[tuple[str, str, str]] = []
    current_insight = ""
    current_detail = ""
    current_label = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            if current_insight:
                entries.append((current_insight, current_label, current_detail))
            current_insight = line[2:].strip()
            current_detail = ""
            current_label = ""
        elif line.startswith("Why it matters:"):
            current_label = "Why it matters"
            current_detail = line.split(":", 1)[1].strip()
        elif line.startswith("→"):
            current_label = "Action"
            current_detail = line[1:].strip()
        elif current_detail:
            current_detail = f"{current_detail} {line}".strip()
        elif current_insight:
            current_insight = f"{current_insight} {line}".strip()

    if current_insight:
        entries.append((current_insight, current_label, current_detail))

    if not entries:
        st.markdown(text)
        return

    for insight, label, detail in entries:
        st.markdown(f"- **{insight}**")
        if detail:
            prefix = f"{label}: " if label else ""
            st.caption(f"{prefix}{detail}")


def parse_key_signal_entries(text: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    current_insight = ""
    current_detail = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            if current_insight:
                entries.append((current_insight, current_detail))
            current_insight = line[2:].strip()
            current_detail = ""
        elif line.startswith("Why it matters:"):
            current_detail = line.split(":", 1)[1].strip()
        elif line.startswith("→"):
            current_detail = line[1:].strip()
        elif current_detail:
            current_detail = f"{current_detail} {line}".strip()
        elif current_insight:
            current_insight = f"{current_insight} {line}".strip()

    if current_insight:
        entries.append((current_insight, current_detail))

    return entries


def parse_bullet_list(text: str) -> list[str]:
    items: list[str] = []
    current = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            if current:
                items.append(current)
            current = line[2:].strip()
        elif current:
            current = f"{current} {line}".strip()

    if current:
        items.append(current)

    return items


def parse_persona_performance_metrics(text: str) -> dict[str, dict[str, str]]:
    metrics: dict[str, dict[str, str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 6 or cells[0] in {"Persona", "---"}:
            continue
        metrics[cells[0]] = {
            "open": cells[2],
            "click": cells[3],
            "unsub": cells[4],
            "trend": cells[5].lower(),
        }
    return metrics


def parse_segment_scorecard_metrics(text: str) -> dict[str, dict[str, str]]:
    metrics: dict[str, dict[str, str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 7 or cells[0] in {"Persona", "---"}:
            continue
        metrics[cells[0]] = {
            "sent": cells[1],
            "opens": cells[2],
            "clicks": cells[3],
            "open": cells[4],
            "click": cells[5],
            "unsub": cells[6],
        }
    return metrics


def detect_persona_name(*parts: str) -> str | None:
    combined = " ".join(parts)
    for persona in PERSONA_DISPLAY_NAMES:
        if persona in combined:
            return persona
    return None


def emphasize_persona(text: str, persona: str | None) -> str:
    if not persona:
        return text
    return text.replace(persona, f"**{persona}**", 1)


def emphasize_persona_html(text: str, persona: str | None) -> str:
    if not persona:
        return text
    return text.replace(persona, f"<strong>{persona}</strong>", 1)


def parse_pct_value(value: str) -> float:
    try:
        return float((value or "").replace("%", "").strip()) / 100.0
    except ValueError:
        return 0.0


def persona_priority_score(metrics: dict[str, str]) -> float:
    if not metrics:
        return 0.0

    open_rate = parse_pct_value(metrics.get("open", "0%"))
    click_rate = parse_pct_value(metrics.get("click", "0%"))
    unsub_rate = parse_pct_value(metrics.get("unsub", "0%"))
    trend = (metrics.get("trend") or "").lower()

    score = 0.0
    score += max(0.0, 0.06 - click_rate) * 2.0
    score += max(0.0, 0.30 - open_rate)
    score += max(0.0, unsub_rate - 0.005) * 2.0

    if trend == "down":
        score += 0.03
    elif trend == "flat":
        score += 0.01

    return score


def render_signal_action_pairs(
    signals_text: str,
    actions_text: str,
    persona_metrics: dict[str, dict[str, str]],
) -> bool:
    signal_entries = parse_key_signal_entries(signals_text)
    action_entries = parse_bullet_list(actions_text)
    if not signal_entries or not action_entries:
        return False

    remaining_actions = action_entries[:]
    paired_entries: list[tuple[str, str, str]] = []

    for idx, (insight, why) in enumerate(signal_entries):
        persona = detect_persona_name(insight)
        matched_action = None

        if persona:
            for action in remaining_actions:
                if persona in action:
                    matched_action = action
                    break

        if matched_action is None and idx < len(remaining_actions):
            matched_action = remaining_actions[idx]

        if matched_action is None and remaining_actions:
            matched_action = remaining_actions[0]

        if matched_action is None:
            continue

        if matched_action in remaining_actions:
            remaining_actions.remove(matched_action)

        paired_entries.append((insight, why, matched_action))

    if not paired_entries:
        return False

    prioritized_entries: list[tuple[float, str, str, str]] = []
    for insight, why, action in paired_entries:
        persona = detect_persona_name(insight) or detect_persona_name(matched_action or "")
        metrics = persona_metrics.get(persona or "", {})
        prioritized_entries.append(
            (persona_priority_score(metrics), insight, why, action)
        )

    prioritized_entries.sort(key=lambda item: item[0], reverse=True)
    high_priority_entries = [item for item in prioritized_entries if item[0] >= 0.08]
    if high_priority_entries:
        selected_entries = high_priority_entries[:]
        if len(selected_entries) < min(3, len(prioritized_entries)):
            for item in prioritized_entries:
                if item not in selected_entries:
                    selected_entries.append(item)
                if len(selected_entries) == min(3, len(prioritized_entries)):
                    break
        prioritized_entries = selected_entries
    else:
        prioritized_entries = prioritized_entries[:3]

    st.markdown("#### ⚡️ Insights and Actions")
    for _score, insight, why, action in prioritized_entries:
        persona = detect_persona_name(insight) or detect_persona_name(action)
        metrics = persona_metrics.get(persona or "", {})
        metrics_html = ""
        if metrics:
            metrics_html = (
                '<div style="display:flex; gap:2.5rem; margin:0.45rem 0 0.25rem 0;">'
                f'<div><strong>Open %</strong> <mark>{metrics["open"]}</mark></div>'
                f'<div><strong>Click %</strong> <mark>{metrics["click"]}</mark></div>'
                '</div>'
            )
        why_html = (
            f'<div style="margin-top:0.35rem; color:#5f5a4f;">'
            f'<strong>Why it matters:</strong> {why}</div>'
            if why else ""
        )
        action_html = (
            f'<div style="margin-top:0.35rem;"><strong>Next move</strong> {action}</div>'
        )
        st.markdown(
            (
                '<div style="background:#fbf4df; border:1px solid #ead9a7; '
                'border-radius:0.75rem; padding:0.95rem 1rem; margin-bottom:0.8rem;">'
                f'<div>{emphasize_persona_html(insight, persona)}</div>'
                f'{metrics_html}'
                f'{why_html}'
                f'{action_html}'
                '</div>'
            ),
            unsafe_allow_html=True,
        )

    return True


def parse_campaign_insight_entries(text: str) -> list[str]:
    entries = parse_bullet_list(text)
    if entries:
        return entries

    observation = ""
    interpretation = ""
    current_label = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Observation:"):
            current_label = "observation"
            observation = line.split(":", 1)[1].strip()
        elif line.startswith("Interpretation:"):
            current_label = "interpretation"
            interpretation = line.split(":", 1)[1].strip()
        elif current_label == "observation":
            observation = f"{observation} {line}".strip()
        elif current_label == "interpretation":
            interpretation = f"{interpretation} {line}".strip()

    combined = " ".join(part for part in [observation, interpretation] if part).strip()
    return [combined] if combined else []


def render_campaign_insights_and_actions(
    insights_text: str,
    actions_text: str,
    scorecard_metrics: dict[str, dict[str, str]],
) -> bool:
    insight_entries = parse_campaign_insight_entries(insights_text)
    action_entries = parse_bullet_list(actions_text)

    if not insight_entries and not action_entries:
        return False

    st.markdown("#### ⚡️ Insights and Actions")

    if not insight_entries:
        with st.container(border=True):
            st.markdown(actions_text)
        return True

    paired: list[tuple[str, str]] = []
    remaining_actions = action_entries[:]
    for idx, insight in enumerate(insight_entries):
        persona = detect_persona_name(insight)
        matched_action = None
        if persona:
            for action in remaining_actions:
                if persona in action:
                    matched_action = action
                    break
        if matched_action is None and idx < len(remaining_actions):
            matched_action = remaining_actions[idx]
        if matched_action is None:
            matched_action = ""
        elif matched_action in remaining_actions:
            remaining_actions.remove(matched_action)
        paired.append((insight, matched_action))

    for insight, action in paired:
        persona = detect_persona_name(insight)
        metrics = scorecard_metrics.get(persona or "", {})
        metrics_html = ""
        if metrics:
            metrics_html = (
                '<div style="display:flex; gap:2.5rem; margin:0.45rem 0 0.25rem 0;">'
                f'<div><strong>Open %</strong> <mark>{metrics["open"]}</mark></div>'
                f'<div><strong>Click %</strong> <mark>{metrics["click"]}</mark></div>'
                '</div>'
            )

        why_text = ""
        if " It " in insight:
            parts = insight.split(" It ", 1)
            insight_title = parts[0].replace("**", "").strip()
            why_text = f"It {parts[1].strip()}"
        else:
            insight_title = insight.replace("**", "").strip()

        why_html = (
            f'<div style="margin-top:0.35rem; color:#5f5a4f;">'
            f'<strong>Why it matters:</strong> {why_text}</div>'
            if why_text else ""
        )
        action_html = (
            f'<div style="margin-top:0.35rem;"><strong>Next move</strong> {action}</div>'
            if action else ""
        )
        st.markdown(
            (
                '<div style="background:#fbf4df; border:1px solid #ead9a7; '
                'border-radius:0.75rem; padding:0.95rem 1rem; margin-bottom:0.8rem;">'
                f'<div>{emphasize_persona_html(insight_title, persona)}</div>'
                f'{metrics_html}'
                f'{why_html}'
                f'{action_html}'
                '</div>'
            ),
            unsafe_allow_html=True,
        )

    return True


def render_campaign_report_file(path: Path, empty_message: str) -> None:
    content = read_text_file(path)
    if content is None:
        st.info(empty_message)
        return

    content = content.replace("# Campaign Dashboard —", "# Campaign Report —")
    title, subtitle = parse_report_header(content)
    sections = parse_md_sections(content)
    scorecard_metrics = parse_segment_scorecard_metrics(sections.get("Segment Scorecard", ""))

    if title:
        st.markdown(f"#### {title}")
    if subtitle:
        st.caption(subtitle)

    if "Campaign Status" in sections:
        st.markdown("#### Campaign Status")
        st.markdown(sections["Campaign Status"])
        st.divider()

    if "Segment Scorecard" in sections:
        st.markdown("#### Segment Scorecard")
        st.markdown(sections["Segment Scorecard"])
        st.divider()

    insights_text = sections.get("Insights", "").strip()
    actions_text = sections.get("Recommended Actions", "").strip()
    if insights_text or actions_text:
        if render_campaign_insights_and_actions(insights_text, actions_text, scorecard_metrics):
            st.divider()

    remaining_keys = [
        key for key in sections
        if key not in {"Campaign Status", "Segment Scorecard", "Insights", "Recommended Actions"}
    ]
    for key in remaining_keys:
        st.markdown(f"#### {key}")
        st.markdown(sections[key])


def load_campaign_metrics_summary() -> list[dict]:
    """Query SQLite for recent campaigns with averaged metrics. Returns newest-first."""
    try:
        import sqlite3 as _sqlite3

        db_path = ROOT / "data" / "novamind.db"
        if not db_path.exists():
            return []
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        rows = conn.execute("""
            SELECT c.id, c.topic, c.status, c.sent_at, c.created_at,
                   AVG(pm.open_rate)        AS avg_open,
                   AVG(pm.click_rate)       AS avg_click,
                   AVG(pm.unsubscribe_rate) AS avg_unsub
            FROM campaigns c
            LEFT JOIN performance_metrics pm ON pm.campaign_id = c.id
            GROUP BY c.id
            ORDER BY c.created_at DESC
            LIMIT 5
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


TOPIC_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "automation",
    "best",
    "blog",
    "campaign",
    "content",
    "creative",
    "for",
    "from",
    "how",
    "ideas",
    "in",
    "is",
    "marketing",
    "mind",
    "novamind",
    "of",
    "on",
    "or",
    "small",
    "startup",
    "the",
    "to",
    "using",
    "with",
}
PERSONA_LABELS = CANONICAL_PERSONA_LABELS
YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _extract_theme_candidates(topic: str) -> list[str]:
    topic = YEAR_RE.sub("", topic or "")
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", topic.lower())
        if len(token) > 2 and token not in TOPIC_STOPWORDS
    ]
    if not tokens:
        return []

    phrases: list[str] = []
    for n in (3, 2, 1):
        for i in range(len(tokens) - n + 1):
            phrase = " ".join(tokens[i : i + n])
            if phrase not in phrases:
                phrases.append(phrase)
    return phrases


def get_recent_campaign_topics(limit: int = 6) -> list[str]:
    try:
        import sqlite3 as _sqlite3

        db_path = ROOT / "data" / "novamind.db"
        if not db_path.exists():
            return []

        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            """
            SELECT topic
            FROM campaigns
            WHERE topic IS NOT NULL AND TRIM(topic) != ''
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [str(row["topic"]).strip() for row in rows if str(row["topic"]).strip()]
    except Exception:
        return []


def _topic_similarity(a: str, b: str) -> float:
    a_tokens = set(_extract_theme_candidates(a))
    b_tokens = set(_extract_theme_candidates(b))
    if not a_tokens or not b_tokens:
        return 0.0
    overlap = len(a_tokens & b_tokens)
    return overlap / max(1, min(len(a_tokens), len(b_tokens)))


def _dedupe_suggested_topics(topics: list[str], recent_topics: list[str]) -> list[str]:
    filtered: list[str] = []
    for topic in topics:
        if any(_topic_similarity(topic, recent) >= 0.5 for recent in recent_topics):
            continue
        if any(_topic_similarity(topic, existing) >= 0.5 for existing in filtered):
            continue
        filtered.append(topic)
    return filtered


def _fill_suggested_topics(
    deduped: list[str],
    fallback_topics: list[str],
    recent_topics: list[str],
    target: int = 3,
) -> list[str]:
    """Ensure the UI always has up to `target` topic suggestions."""
    filled = deduped[:]
    for topic in fallback_topics:
        normalized = _normalize_suggested_topic(topic)
        if normalized in filled:
            continue
        if any(_topic_similarity(normalized, existing) >= 0.5 for existing in filled):
            continue
        filled.append(normalized)
        if len(filled) >= target:
            break

    if len(filled) < target:
        for topic in fallback_topics:
            normalized = _normalize_suggested_topic(topic)
            if normalized not in filled:
                filled.append(normalized)
            if len(filled) >= target:
                break

    return filled[:target]


def get_suggested_topic_inputs() -> tuple[str | None, list[str]]:
    """Return the top-performing persona plus the strongest recurring themes."""
    try:
        import sqlite3 as _sqlite3

        db_path = ROOT / "data" / "novamind.db"
        if not db_path.exists():
            return None, []

        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row

        persona_rows = conn.execute("""
            SELECT pm.persona,
                   AVG(pm.click_rate) AS avg_click,
                   AVG(pm.open_rate)  AS avg_open
            FROM performance_metrics pm
            GROUP BY pm.persona
            ORDER BY avg_click DESC, avg_open DESC
        """).fetchall()

        campaign_rows = conn.execute("""
            SELECT c.topic,
                   AVG(pm.click_rate) AS avg_click,
                   AVG(pm.open_rate)  AS avg_open
            FROM campaigns c
            JOIN performance_metrics pm ON pm.campaign_id = c.id
            GROUP BY c.id
            HAVING AVG(pm.click_rate) IS NOT NULL
            ORDER BY avg_click DESC, avg_open DESC, c.created_at DESC
            LIMIT 8
        """).fetchall()
        conn.close()

        top_persona = None
        if persona_rows:
            persona_slug = persona_rows[0]["persona"]
            top_persona = persona_label(persona_slug)

        if not campaign_rows:
            return top_persona, []

        scored_themes: dict[str, float] = {}
        for row in campaign_rows:
            topic = row["topic"] or ""
            weight = float(row["avg_click"] or 0) * 2 + float(row["avg_open"] or 0)
            for phrase in _extract_theme_candidates(topic):
                scored_themes[phrase] = scored_themes.get(phrase, 0.0) + weight

        ranked_themes = sorted(
            scored_themes.items(),
            key=lambda item: (-item[1], -len(item[0]), item[0]),
        )

        themes: list[str] = []
        for phrase, _score in ranked_themes:
            if any(phrase in existing or existing in phrase for existing in themes):
                continue
            themes.append(phrase)
            if len(themes) == 5:
                break

        return top_persona, themes
    except Exception:
        return None, []


_DOMAIN_SEED_TOPICS = [
    "How AI Automation Is Reshaping Multi-Channel Marketing",
    "Building a Scalable Marketing Workflow Without a Larger Team",
    "Multi-Channel Campaign Automation: What Actually Works",
    "How to Keep Brand Voice Consistent Across Every Channel",
    "Why Marketing Workflows Are the Hidden Driver of Brand Growth",
    "AI-Powered Branding: Consistent Messaging at Scale",
    "How to Automate Your Marketing Funnel Without Losing the Human Touch",
    "What a Modern Marketing Automation Stack Should Look Like",
    "How Small Agencies Win with Automated Multi-Channel Campaigns",
    "Using AI to Strengthen Brand Identity Across Campaigns",
    "How to Build a Repeatable Multi-Channel Campaign Strategy",
    "Marketing Automation 101: Workflows That Save Time and Drive Results",
]


def _fallback_topic_suggestions(persona: str, themes: list[str], variant: int = 0) -> list[str]:
    base_themes = list(themes[:3]) if themes else []
    while len(base_themes) < 3:
        base_themes.append(
            ["marketing automation", "brand consistency", "multi-channel campaigns"][
                len(base_themes)
            ]
        )
    theme_templates = [
        f"How to improve {base_themes[0]} with AI automation",
        f"A practical guide to better {base_themes[1]} across every channel",
        f"How small teams can scale {base_themes[2]} without extra overhead",
        f"What high-performing teams get right about {base_themes[0]}",
        f"How to turn {base_themes[1]} into a repeatable growth system",
        f"Why better {base_themes[2]} matters more than adding headcount",
    ]
    # Interleave domain seeds so suggestions always cover AI automation,
    # workflows, branding, and multi-channel even when theme data is sparse.
    start_theme = variant % len(theme_templates)
    start_seed = variant % len(_DOMAIN_SEED_TOPICS)
    ordered_theme = theme_templates[start_theme:] + theme_templates[:start_theme]
    ordered_seed = _DOMAIN_SEED_TOPICS[start_seed:] + _DOMAIN_SEED_TOPICS[:start_seed]
    # Pick 2 from theme templates and 1 from domain seeds (or vice versa on refresh)
    if variant % 2 == 0:
        candidates = ordered_theme[:2] + ordered_seed[:1]
    else:
        candidates = ordered_seed[:2] + ordered_theme[:1]
    return candidates[:3]


def _normalize_suggested_topic(topic: str) -> str:
    current_year = datetime.now().year
    cleaned = re.sub(r"\s+", " ", (topic or "").strip())

    def _replace_year(match: re.Match[str]) -> str:
        year = int(match.group(1))
        return str(current_year) if year < current_year else match.group(1)

    cleaned = YEAR_RE.sub(_replace_year, cleaned)
    return cleaned.strip(" -:")


@st.cache_data(ttl=1800, show_spinner=False)
def generate_suggested_topics(
    persona: str,
    themes: tuple[str, ...],
    recent_topics: tuple[str, ...],
    previous_topics: tuple[str, ...],
    refresh_nonce: int,
) -> list[str]:
    theme_list = [theme for theme in themes if theme]
    if not persona or not theme_list:
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        fallback_topics = _fallback_topic_suggestions(persona, theme_list, refresh_nonce)
        deduped = _dedupe_suggested_topics(fallback_topics, list(recent_topics))
        return _fill_suggested_topics(deduped, fallback_topics, list(recent_topics))

    refresh_guidance = ""
    if refresh_nonce > 0:
        refresh_guidance = (
            "\nAdditional instruction:\n"
            "- Return a fresh alternative set of topic ideas from prior refreshes\n"
            "- Vary the angle and phrasing while staying grounded in the same themes\n"
            "- Avoid obvious rewordings of the most recent suggestions\n"
        )
    previous_titles_block = ""
    if previous_topics:
        previous_titles_block = (
            "\nAvoid reusing these exact currently displayed suggestions:\n- "
            + "\n- ".join(previous_topics)
            + "\n"
        )

    prompt = f"""You are generating blog topic suggestions for NovaMind, an AI marketing automation platform.

Top performing persona:
{persona}

High-performing campaign themes (from past campaigns):
{", ".join(theme_list)}

NovaMind's core content areas (always consider these as valid angles):
- AI automation in marketing workflows
- Multi-channel campaign automation (email, social, CRM)
- Brand consistency and voice across channels
- How AI helps small teams compete at scale
- Marketing workflow design and optimization
- Branding strategy for agencies and startups

Task:
- Generate exactly 3 concise, practical blog topic titles
- Draw from both the high-performing themes above AND NovaMind's core content areas
- At least one title should touch on AI automation, multi-channel campaigns, branding, or marketing workflows
- Persona should influence angle and prioritization only, not appear in the titles
- Write broad blog topics useful for a marketing content calendar, not email subject lines
- Do not include outdated years; prefer evergreen titles
- Return a JSON array of 3 strings only
{refresh_guidance}
{previous_titles_block}
"""

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=180,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        if isinstance(data, list):
            topics = [
                _normalize_suggested_topic(str(item))
                for item in data
                if str(item).strip()
            ]
            deduped = _dedupe_suggested_topics(topics, list(recent_topics))
            if len(deduped) >= 3:
                return deduped[:3]
            if deduped:
                fallback_fill = _fallback_topic_suggestions(persona, theme_list, refresh_nonce)
                return _fill_suggested_topics(deduped, fallback_fill, list(recent_topics))
    except Exception:
        pass

    fallback_topics = [
        _normalize_suggested_topic(topic)
        for topic in _fallback_topic_suggestions(persona, theme_list, refresh_nonce)
    ]
    deduped = _dedupe_suggested_topics(fallback_topics, list(recent_topics))
    return _fill_suggested_topics(deduped, fallback_topics, list(recent_topics))


def render_campaign_caption(campaign_data: dict | None, campaign_record: dict | None = None) -> None:
    if campaign_data is None and campaign_record is None:
        return
    cid = (
        campaign_data.get("campaign_id")
        if campaign_data is not None
        else campaign_record.get("id", "?")
    )
    blog_title = (
        (campaign_data.get("blog") or {}).get("title", "")
        if campaign_data is not None
        else (campaign_record or {}).get("blog_title", "")
    )
    topic_str = (campaign_record or {}).get("topic", "")
    parts = [f"Campaign #{cid}"]
    if topic_str:
        parts.append(topic_str)
    if blog_title and blog_title != topic_str:
        parts.append(blog_title)
    st.caption(" · ".join(parts))


# ── Dashboard renderer ────────────────────────────────────────────────────────


def render_dashboard_tab() -> None:
    dashboard_path = REPORTS_DIR / "dashboard.md"

    st.markdown("#### Campaign Dashboard")

    # Button click triggers refresh before we read the file
    refresh_clicked = st.button("Refresh Dashboard")

    if refresh_clicked:
        refresh_dashboard()

    content = read_text_file(dashboard_path)

    if content is None:
        st.info(
            "No dashboard report found yet. Click 'Refresh Dashboard' to generate one."
        )
        return

    # Extract "Last updated" timestamp — show below title
    for line in content.splitlines():
        if "_Last updated:" in line:
            st.caption(line.strip().strip("_"))
            break

    sections = parse_md_sections(content)
    persona_metrics = parse_persona_performance_metrics(
        sections.get("Persona Performance", "")
    )

    # ── 1. Summary ─────────────────────────────────────────────────────────────
    if "Summary" in sections:
        st.markdown("#### Summary")
        st.markdown(sections["Summary"])
        st.divider()

    # ── 2. Persona Performance ─────────────────────────────────────────────────
    if "Persona Performance" in sections:
        st.markdown("#### Persona Performance")
        st.markdown(sections["Persona Performance"])
        st.divider()

    # ── 3. Key Signals ─────────────────────────────────────────────────────────
    signals_key = next((k for k in sections if "Key Signals" in k), None)
    rec_key = next(
        (k for k in sections if "Recommended Actions" in k or "What to Do Next" in k),
        None,
    )
    signals_text = sections.get(signals_key, "").strip() if signals_key else ""
    actions_text = sections.get(rec_key, "").strip() if rec_key else ""

    if signals_text and actions_text and render_signal_action_pairs(
        signals_text,
        actions_text,
        persona_metrics,
    ):
        st.divider()
    elif signals_text:
        st.markdown("#### Key Signals")
        render_key_signals_section(signals_text)
        st.divider()

    # ── 4. What To Do Next ─────────────────────────────────────────────────────
    if actions_text and not signals_text:
        st.markdown("#### What To Do Next")
        with st.container(border=True):
            st.markdown(actions_text)
        st.divider()

    # ── 5. Recent Campaigns (table) ────────────────────────────────────────────
    st.markdown("#### Recent Campaigns")
    campaigns = load_campaign_metrics_summary()
    if not campaigns:
        st.caption("No campaigns found.")
    else:
        opens_with_data = [
            c["avg_open"] for c in campaigns if c["avg_open"] is not None
        ]
        best_open = max(opens_with_data) if opens_with_data else None

        rows = [
            "| Campaign | Status | Open % | Click % | Unsub % |",
            "|---|---|---|---|---|",
        ]
        for c in campaigns:
            topic = c["topic"] or "Untitled"
            status = c["status"] or "draft"
            avg_open = c["avg_open"]
            avg_click = c["avg_click"]
            avg_unsub = c["avg_unsub"]

            open_str = f"{avg_open:.1%}" if avg_open is not None else "—"
            click_str = f"{avg_click:.1%}" if avg_click is not None else "—"
            unsub_str = f"{avg_unsub:.2%}" if avg_unsub is not None else "—"

            is_best = (
                best_open is not None
                and avg_open is not None
                and avg_open >= best_open - 0.001
            )
            high_unsub = avg_unsub is not None and avg_unsub > 0.005

            if is_best:
                open_str = f"{open_str} ⭐"
            if high_unsub:
                unsub_str = f"{unsub_str} ⚠️"

            status_label = "Sent" if status == "sent" else "Draft"
            rows.append(
                f"| {topic} | {status_label} | {open_str} | {click_str} | {unsub_str} |"
            )

        st.markdown("\n".join(rows))


# ── Pipeline control ───────────────────────────────────────────────────────────


def _friendly_error(raw: str) -> str:
    """Convert raw subprocess stderr/stdout into a short, human-readable message."""
    if not raw:
        return "Unknown error. Check that your .env file is configured correctly."
    low = raw.lower()
    if "authentication_error" in low or "invalid x-api-key" in low or "authenticationerror" in low:
        return (
            "Invalid Anthropic API key (401). "
            "Open your .env file and set ANTHROPIC_API_KEY to a valid key from console.anthropic.com."
        )
    if "brevo" in low and ("401" in raw or "unauthorized" in low):
        return (
            "Invalid Brevo API key. "
            "Open your .env file and set BREVO_API_KEY to your Brevo API key."
        )
    if "contentvalidationerror" in low or "validation" in low and "words" in low:
        # Extract just the validation message line, not the full traceback
        for line in raw.splitlines():
            if "ContentValidationError" in line or ("words" in line and ("must" in line or "between" in line)):
                return f"Content validation failed: {line.strip()}"
        return "Content validation failed. The AI response did not meet quality constraints — try again."
    if "missing env var" in low:
        for line in raw.splitlines():
            if "Missing env var" in line:
                return line.strip()
        return "Missing environment variable. Check your .env file."
    if "ratelimit" in low or "rate_limit" in low or "529" in raw:
        return "Anthropic API rate limit hit. Wait a moment and try again."
    # Fallback: show only the last meaningful error line, not the full traceback
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("File ") and "Traceback" not in ln]
    return lines[-1] if lines else raw[:300]


def _launch_subprocess(cmd: list, label: str, mock_ai: bool, topic: str = "") -> None:
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    output_queue: queue.Queue = queue.Queue()
    threading.Thread(
        target=enqueue_stream, args=(proc.stdout, output_queue, "stdout"), daemon=True
    ).start()
    threading.Thread(
        target=enqueue_stream, args=(proc.stderr, output_queue, "stderr"), daemon=True
    ).start()
    updates = {
        "last_run_mode":        "mock" if mock_ai else "live",
        "last_command_result":  None,
        "last_command_label":   label,
        "last_pipeline_result": None,
        "active_process":       proc,
        "active_queue":         output_queue,
        "active_stdout":        "",
        "active_stderr":        "",
        "active_label":         label,
        "pipeline_started":     True,
    }
    if topic:
        updates["active_topic"] = topic
    st.session_state.update(updates)


def start_pipeline(topic: str, mock_ai: bool) -> None:
    if not topic.strip():
        st.error("Enter a topic before running the pipeline.")
        return
    if st.session_state["active_process"] is not None:
        st.warning("A pipeline run is already in progress.")
        return
    cmd = [sys.executable, "agent.py", "run", "--topic", topic.strip()]
    if mock_ai:
        cmd.append("--mock-ai")
    _launch_subprocess(cmd, "Run Mock Pipeline" if mock_ai else "Run Pipeline", mock_ai, topic.strip())


def start_blog_generation(topic: str, mock_ai: bool, feedback: str = "") -> None:
    if not topic.strip():
        st.error("Enter a topic before running the pipeline.")
        return
    if st.session_state["active_process"] is not None:
        st.warning("A pipeline run is already in progress.")
        return
    cmd = [sys.executable, "agent.py", "generate-blog", "--topic", topic.strip()]
    if mock_ai:
        cmd.append("--mock-ai")
    if feedback.strip():
        cmd += ["--feedback", feedback.strip()]
    st.session_state["pipeline_stage"]       = "generating_blog"
    st.session_state["pending_mock_ai"]      = mock_ai
    st.session_state["distribute_completed"] = False
    st.session_state["pipeline_error"]       = None
    _launch_subprocess(cmd, "Generating Blog", mock_ai, topic.strip())


def start_newsletter_generation(campaign_id: int, mock_ai: bool, feedback: str = "") -> None:
    if st.session_state["active_process"] is not None:
        st.warning("A pipeline run is already in progress.")
        return
    cmd = [sys.executable, "agent.py", "generate-newsletters", "--campaign-id", str(campaign_id)]
    if mock_ai:
        cmd.append("--mock-ai")
    if feedback.strip():
        cmd += ["--feedback", feedback.strip()]
    st.session_state["pipeline_stage"] = "generating_newsletters"
    _launch_subprocess(cmd, "Generating Newsletters", mock_ai)


def start_distribution(campaign_id: int, mock_ai: bool) -> None:
    if st.session_state["active_process"] is not None:
        st.warning("A pipeline run is already in progress.")
        return
    cmd = [sys.executable, "agent.py", "distribute", "--campaign-id", str(campaign_id)]
    if mock_ai:
        cmd.append("--mock-ai")
    st.session_state["pipeline_stage"] = "distributing"
    _launch_subprocess(cmd, "Distributing Campaign", mock_ai)


def poll_pipeline() -> None:
    proc = st.session_state.get("active_process")
    output_queue = st.session_state.get("active_queue")
    if proc is None or output_queue is None:
        return

    while True:
        try:
            stream_name, line = output_queue.get_nowait()
        except queue.Empty:
            break
        key = "active_stdout" if stream_name == "stdout" else "active_stderr"
        st.session_state[key] += line

    if proc.poll() is None:
        return

    time.sleep(0.1)
    while True:
        try:
            stream_name, line = output_queue.get_nowait()
        except queue.Empty:
            break
        key = "active_stdout" if stream_name == "stdout" else "active_stderr"
        st.session_state[key] += line

    stdout = st.session_state["active_stdout"]
    st.session_state["last_command_result"] = {
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": st.session_state["active_stderr"],
    }
    st.session_state["last_command_label"]   = st.session_state.get("active_label")
    st.session_state["last_pipeline_result"] = dict(st.session_state["last_command_result"])
    st.session_state["active_process"]       = None
    st.session_state["active_queue"]         = None

    # Stage transitions for the approval flow
    stage = st.session_state.get("pipeline_stage", "idle")
    if proc.returncode == 0:
        st.session_state["pipeline_error"] = None
        if stage == "generating_blog":
            for line in stdout.splitlines():
                if line.startswith("CAMPAIGN_ID:"):
                    try:
                        campaign_id = int(line.split(":")[1].strip())
                        st.session_state["pending_campaign_id"] = campaign_id
                        st.session_state["session_campaign_ids"].add(campaign_id)
                    except ValueError:
                        pass
            st.session_state["pipeline_stage"] = "awaiting_blog_approval"
        elif stage == "generating_newsletters":
            st.session_state["pipeline_stage"] = "awaiting_newsletter_approval"
        elif stage == "distributing":
            st.session_state["pipeline_stage"]       = "idle"
            st.session_state["distribute_completed"] = True
    else:
        # Subprocess failed — reset stage so user can retry
        stderr = st.session_state.get("active_stderr", "")
        raw = stderr.strip() or stdout.strip() or ""
        st.session_state["pipeline_error"] = _friendly_error(raw)
        st.session_state["pipeline_stage"] = "idle"


# ── Pipeline status widget ─────────────────────────────────────────────────────


def infer_status(stdout: str) -> dict[str, bool]:
    return {
        "step1_started":        "Step 1 · AI Content Generation" in stdout,
        "outline_done":         "Outline:" in stdout,
        "blog_done":            "Blog:" in stdout,
        "newsletters_done":     "Content saved" in stdout,
        "step2_done":           "contacts synced to Brevo" in stdout,
        "step3_done":           "Emails dispatched to real contacts via Brevo" in stdout,
        "step4_started":        "Step 4 · Baseline Metrics" in stdout,
        "blog_gen_done":        "CAMPAIGN_ID:" in stdout,
        "newsletters_gen_done": "NEWSLETTERS_SAVED" in stdout,
        "distribute_done":      "Pipeline complete" in stdout,
    }


def _indicator(done: bool, label: str, in_progress: bool = False) -> str:
    if done:
        return f"{label} &nbsp;✓"
    if in_progress:
        return f"{label} &nbsp;..."
    return f"<span style='color:#bbb'>{label}</span>"


def render_pipeline_status() -> None:
    active_stdout  = st.session_state.get("active_stdout", "")
    is_running     = st.session_state.get("active_process") is not None
    run_result     = st.session_state.get("last_pipeline_result")
    stage          = st.session_state.get("pipeline_stage", "idle")
    dist_completed = st.session_state.get("distribute_completed", False)

    in_approval_flow = stage != "idle" or dist_completed
    if not is_running and run_result is None and not in_approval_flow:
        return

    source_stdout = active_stdout if is_running else str((run_result or {}).get("stdout", ""))
    s = infer_status(source_stdout)

    # Stage-based completion truth (crosses subprocess boundaries)
    _past_blog = stage in ("generating_newsletters", "awaiting_newsletter_approval", "distributing") or dist_completed
    _past_nl   = stage == "distributing" or dist_completed
    _await_blog = stage == "awaiting_blog_approval"
    _await_nl   = stage == "awaiting_newsletter_approval"

    _outline_done     = s["outline_done"]    or _past_blog or _await_blog
    _blog_done        = s["blog_done"]       or _past_blog or _await_blog
    _newsletters_done = s["newsletters_done"] or s["newsletters_gen_done"] or _past_nl or _await_nl
    _step2_done       = s["step2_done"]      or dist_completed
    _step3_done       = s["step3_done"]      or dist_completed

    # In-progress guards per subprocess type
    _is_full_run  = is_running and stage == "idle"
    _is_blog_gen  = is_running and stage == "generating_blog"
    _is_nl_gen    = is_running and stage == "generating_newsletters"
    _is_dist      = is_running and stage == "distributing"

    outline_ip    = (_is_blog_gen or _is_full_run) and s["step1_started"] and not _outline_done
    blog_ip       = (_is_blog_gen or _is_full_run) and _outline_done and not _blog_done
    newsletters_ip = (_is_nl_gen or _is_full_run) and _blog_done and not _newsletters_done
    step2_ip      = (_is_dist or _is_full_run) and _newsletters_done and not _step2_done
    step3_ip      = (_is_dist or _is_full_run) and _step2_done and not _step3_done
    step4_ip      = (_is_dist or _is_full_run) and _step3_done and is_running

    # Approval indicator lines for Step 1
    blog_approval_html = ""
    if _await_blog:
        blog_approval_html = "<div style='padding-left:0.6rem;color:#b45309;'>&#8594; Awaiting blog approval</div>"
    elif _past_blog:
        blog_approval_html = "<div style='padding-left:0.6rem;color:#16a34a;'>&#10003; Blog approved</div>"

    nl_approval_html = ""
    if _await_nl:
        nl_approval_html = "<div style='padding-left:0.6rem;color:#b45309;'>&#8594; Awaiting newsletter approval</div>"
    elif _past_nl:
        nl_approval_html = "<div style='padding-left:0.6rem;color:#16a34a;'>&#10003; Newsletters approved</div>"

    _parts = [
        '<div class="pipeline-card">',
        "<h4>Pipeline Status</h4>",
        '<div class="pipeline-step">',
        "<strong>Step 1 &middot; AI Content Generation</strong>",
        f"<div>{_indicator(_outline_done, 'Outline generated', outline_ip)}</div>",
        f"<div>{_indicator(_blog_done, 'Blog generated', blog_ip)}</div>",
    ]
    if blog_approval_html:
        _parts.append(blog_approval_html)
    _parts.append(f"<div>{_indicator(_newsletters_done, 'Newsletters generated', newsletters_ip)}</div>")
    if nl_approval_html:
        _parts.append(nl_approval_html)
    _parts += [
        "</div>",
        '<div class="pipeline-step">',
        "<strong>Step 2 &middot; CRM Setup</strong>",
        f"<div>{_indicator(_step2_done, 'Contacts synced', step2_ip)}</div>",
        "</div>",
        '<div class="pipeline-step">',
        "<strong>Step 3 &middot; Campaign Creation</strong>",
        f"<div>{_indicator(_step3_done, 'Campaigns created', step3_ip)}</div>",
        "</div>",
        '<div class="pipeline-step">',
        "<strong>Step 4 &middot; Performance</strong>",
        f"<div>{_indicator(False, 'Waiting for engagement data...', step4_ip)}</div>",
        "</div>",
        '<div class="pipeline-hints">',
        "<span>Refresh stats to view results</span>",
        "<span>View campaign report in the Campaign Report tab</span>",
        "</div>",
        "</div>",
    ]
    status_html = "".join(_parts)

    if is_running:
        st.markdown(status_html, unsafe_allow_html=True)
    elif dist_completed:
        dist_stdout = str((run_result or {}).get("stdout", ""))
        any_suspended = "suspended" in dist_stdout.lower()
        if any_suspended:
            st.warning("Some campaigns may be suspended in Brevo — check your Brevo dashboard.")
        else:
            st.success("Campaign sent successfully.")
        with st.expander("Pipeline details", expanded=False):
            st.markdown(status_html, unsafe_allow_html=True)
    elif in_approval_flow:
        st.markdown(status_html, unsafe_allow_html=True)
    elif run_result is not None:
        if int(run_result["returncode"]) == 0:
            st.success("Pipeline completed successfully.")
        else:
            st.error("Pipeline failed.")
        with st.expander("Pipeline details", expanded=False):
            st.markdown(status_html, unsafe_allow_html=True)


# ── Action commands ────────────────────────────────────────────────────────────


def refresh_stats() -> None:
    campaign_id = load_latest_campaign_id()
    if campaign_id is None:
        st.error("Could not find a campaign_id in the latest output/campaign.json.")
        return
    result = run_command(
        [sys.executable, "agent.py", "refresh-stats", "--campaign-id", str(campaign_id)]
    )
    st.session_state["last_command_result"] = result
    st.session_state["last_command_label"] = "Refresh Stats"
    if int(result["returncode"]) == 0:
        st.session_state["campaign_report_sync_status"] = (
            "Latest stats synced from Brevo."
        )
        st.success("Stats refreshed.")
    else:
        st.session_state["campaign_report_sync_status"] = None
        st.error("Stats refresh failed.")


def refresh_dashboard() -> None:
    mode = st.session_state.get("last_run_mode")
    cmd = [sys.executable, "agent.py", "dashboard"]
    if mode != "live":
        cmd.append("--mock-ai")
    result = run_command(cmd)
    if int(result["returncode"]) == 0:
        st.success("Dashboard refreshed.")
    else:
        st.error("Dashboard refresh failed.")


# ── App ────────────────────────────────────────────────────────────────────────

init_session_state()
poll_pipeline()

st.set_page_config(page_title="CampaignFlow AI", layout="wide")
inject_css()

st.title("CampaignFlow AI")
st.caption(
    "An end-to-end marketing automation pipeline that generates blogs, sends "
    "persona-targeted campaigns via Brevo, and optimizes content using real "
    "engagement data"
)

# Shared state — computed once, referenced across tabs
latest_campaign_id = load_latest_campaign_id()
latest_output_dir = get_output_dir_for_campaign(latest_campaign_id)
campaign_data = load_campaign_json(latest_campaign_id)
campaign_record = load_campaign_record(latest_campaign_id)
pipeline_started: bool = st.session_state["pipeline_started"]

tabs = st.tabs(["Run", "Blog", "Newsletters", "Campaign Report", "Dashboard"])

# ── Run tab ────────────────────────────────────────────────────────────────────
with tabs[0]:
    _stage           = st.session_state.get("pipeline_stage", "idle")
    _pending_id      = st.session_state.get("pending_campaign_id")
    _pending_mock_ai = st.session_state.get("pending_mock_ai", False)
    _is_active       = st.session_state.get("active_process") is not None
    _dist_completed  = st.session_state.get("distribute_completed", False)
    _current_topic   = st.session_state.get("active_topic", "")

    # ── Topic input ────────────────────────────────────────────────────────────
    _pipeline_active  = _stage != "idle" or _is_active or _dist_completed
    _input_locked     = _pipeline_active and not _dist_completed

    nonce = int(st.session_state.get("suggested_topics_refresh_nonce", 0))
    computed_nonce = st.session_state.get("suggested_topics_computed_nonce", -1)
    existing_topics = st.session_state.get("last_suggested_topics", ())
    if not existing_topics or nonce != computed_nonce:
        top_persona, top_themes = get_suggested_topic_inputs()
        if top_persona and top_themes:
            recent_topics = tuple(get_recent_campaign_topics())
            previous_topics = tuple(existing_topics)
            new_topics = generate_suggested_topics(
                top_persona,
                tuple(top_themes),
                recent_topics,
                previous_topics,
                nonce,
            )
            if new_topics:
                st.session_state["last_suggested_topics"] = tuple(new_topics)
                st.session_state["suggested_topics_computed_nonce"] = nonce
    suggested_topics = list(st.session_state.get("last_suggested_topics", ()))

    st.markdown("#### Start a New Run")
    topic = st.text_input(
        "Topic",
        value=_current_topic if _input_locked else "",
        placeholder='e.g. "AI in creative automation"',
        label_visibility="collapsed",
        disabled=_input_locked,
    )
    if not _pipeline_active or _dist_completed:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Run Pipeline", use_container_width=True):
                start_blog_generation(topic, mock_ai=False)
        with col2:
            if st.button("Run Mock Pipeline", use_container_width=True):
                start_blog_generation(topic, mock_ai=True)

    if suggested_topics:
        topics_html = "".join(
            f"<div style='margin-top:0.4rem;'>➡️ {t}</div>"
            for t in suggested_topics[:3]
        )
        margin_top = "0.75rem" if not _pipeline_active else "0.5rem"
        st.markdown(
            f"""
            <div style="background:#edf4f8;border:1px solid #d2e0e8;border-radius:0.6rem;
                        padding:0.9rem 1rem;margin-top:{margin_top};margin-bottom:0.25rem;">
                <div style="font-size:1.05rem;font-weight:600;margin-bottom:0.35rem;">
                    💡 Suggested Topics (based on past campaign performance)
                </div>
                {topics_html}
            </div>
            """,
            unsafe_allow_html=True,
        )
        if not _pipeline_active and st.button("Refresh Suggested Topics"):
            st.session_state["suggested_topics_refresh_nonce"] = (
                int(st.session_state.get("suggested_topics_refresh_nonce", 0)) + 1
            )
            st.rerun()

    render_pipeline_status()

    # ── Pipeline error recovery ────────────────────────────────────────────────
    _pipeline_error = st.session_state.get("pipeline_error")
    if _pipeline_error and not _is_active:
        with st.expander("Pipeline error — click to expand", expanded=True):
            st.error("The pipeline step failed. Check the error below, then retry.")
            st.code(_pipeline_error, language="text")
        if st.button("Clear Error & Start New Run", type="primary"):
            st.session_state["pipeline_error"] = None
            st.session_state["pipeline_stage"] = "idle"
            st.session_state["distribute_completed"] = False
            st.session_state["cached_previous_runs"] = None
            st.rerun()

    # ── Approval panels ────────────────────────────────────────────────────────

    # Awaiting blog approval
    if _stage == "awaiting_blog_approval" and not _is_active:
        _blog_feedback = st.text_input(
            "Feedback for redo (optional)",
            placeholder="e.g. make the tone more conversational, focus more on ROI...",
            key="blog_feedback_input",
        )
        _col1, _col2 = st.columns(2)
        with _col1:
            if st.button("Approve & Generate Newsletters", use_container_width=True, type="primary"):
                start_newsletter_generation(_pending_id, _pending_mock_ai)
                st.rerun()
        with _col2:
            if st.button("Redo Blog", use_container_width=True):
                start_blog_generation(_current_topic, _pending_mock_ai, feedback=_blog_feedback)
                st.rerun()

    # Awaiting newsletter approval
    elif _stage == "awaiting_newsletter_approval" and not _is_active:
        _nl_feedback = st.text_input(
            "Feedback for redo (optional)",
            placeholder="e.g. make the Agency Founder newsletter more concise...",
            key="newsletter_feedback_input",
        )
        _col1, _col2 = st.columns(2)
        with _col1:
            if st.button("Approve & Send Campaign", use_container_width=True, type="primary"):
                start_distribution(_pending_id, _pending_mock_ai)
                st.rerun()
        with _col2:
            if st.button("Redo Newsletters", use_container_width=True):
                start_newsletter_generation(_pending_id, _pending_mock_ai, feedback=_nl_feedback)
                st.rerun()

    # Previous runs — always visible
    st.divider()
    st.markdown("#### Previous Runs")
    if st.session_state["cached_previous_runs"] is None:
        session_campaign_ids = {
            cid for cid in st.session_state.get("session_campaign_ids", set())
            if isinstance(cid, int)
        }
        st.session_state["cached_previous_runs"] = get_all_runs(
            hidden_campaign_ids=session_campaign_ids
        )
    all_runs = st.session_state["cached_previous_runs"]
    if not all_runs:
        st.caption("No previous runs found.")
    else:
        for i, run in enumerate(all_runs):
            with st.expander(f"{run['blog_title']}  ·  {run['ts_display']}"):
                run_tabs = st.tabs(["Blog", "Newsletters", "Report"])
                with run_tabs[0]:
                    if run["has_blog"]:
                        show_markdown_file(
                            run["folder"] / "blog.md", "Blog not available."
                        )
                    else:
                        st.caption("No blog file for this run.")
                with run_tabs[1]:
                    if run["has_newsletters"]:
                        show_markdown_file(
                            run["folder"] / "newsletters.md",
                            "Newsletters not available.",
                        )
                    else:
                        st.caption("No newsletters file for this run.")
                with run_tabs[2]:
                    if run["has_report"]:
                        render_campaign_report_file(
                            REPORTS_DIR / f"campaign_{run['campaign_id']}.md",
                            "Report not available.",
                        )
                    else:
                        st.caption(
                            "No report yet for this run. "
                            "Go to Campaign Report → Refresh Stats after campaigns are sent."
                        )

# ── Blog tab ───────────────────────────────────────────────────────────────────
with tabs[1]:
    _tab_stage   = st.session_state.get("pipeline_stage", "idle")
    _tab_pending = st.session_state.get("pending_campaign_id")
    _is_running  = st.session_state.get("active_process") is not None

    if _tab_stage == "awaiting_blog_approval" and not _is_running:
        st.warning("⏸ Awaiting your approval — review the blog below, then return to the **Run** tab to approve or redo.")
        _tab_out_dir = get_output_dir_for_campaign(_tab_pending)
        if _tab_out_dir:
            render_campaign_caption(load_campaign_json(_tab_pending), load_campaign_record(_tab_pending))
            show_markdown_file(_tab_out_dir / "blog.md", "Blog not found.")
    elif _tab_stage in ("awaiting_newsletter_approval",) and not _is_running:
        st.info("Blog approved. Review the newsletters in the **Newsletters** tab.")
        _tab_out_dir = get_output_dir_for_campaign(_tab_pending)
        if _tab_out_dir:
            render_campaign_caption(load_campaign_json(_tab_pending), load_campaign_record(_tab_pending))
            show_markdown_file(_tab_out_dir / "blog.md", "Blog not found.")
    elif _is_running:
        _s = infer_status(st.session_state.get("active_stdout", ""))
        if _s["newsletters_done"] or _s["blog_gen_done"]:
            if latest_output_dir is not None:
                render_campaign_caption(campaign_data, campaign_record)
                show_markdown_file(latest_output_dir / "blog.md", "Blog not yet saved.")
            else:
                st.info("Blog generated — loading...")
        elif _s["blog_done"]:
            st.info("Saving blog post...")
        elif _s["outline_done"]:
            st.info("Writing blog post...")
        elif _s["step1_started"]:
            st.info("Generating outline...")
        else:
            st.info("Pipeline starting...")
    elif st.session_state.get("pipeline_error"):
        st.error("Pipeline failed — no new content to show. Fix the error in the **Run** tab and try again.")
    elif not pipeline_started:
        st.warning("⚠️ No active run. Run the pipeline to generate content.")
    elif latest_output_dir is None:
        st.info("No output found.")
    else:
        render_campaign_caption(campaign_data, campaign_record)
        show_markdown_file(
            latest_output_dir / "blog.md",
            "No blog.md found in the latest output folder.",
        )

# ── Newsletters tab ────────────────────────────────────────────────────────────
with tabs[2]:
    _tab_stage   = st.session_state.get("pipeline_stage", "idle")
    _tab_pending = st.session_state.get("pending_campaign_id")
    _is_running  = st.session_state.get("active_process") is not None

    if _tab_stage == "awaiting_blog_approval" and not _is_running:
        st.warning("⏸ Awaiting blog approval — approve the blog in the **Run** tab first before newsletters are generated.")
    elif _tab_stage == "awaiting_newsletter_approval" and not _is_running:
        st.warning("⏸ Awaiting your approval — review the newsletters below, then return to the **Run** tab to approve or redo.")
        _tab_out_dir = get_output_dir_for_campaign(_tab_pending)
        if _tab_out_dir:
            render_campaign_caption(load_campaign_json(_tab_pending), load_campaign_record(_tab_pending))
            show_markdown_file(_tab_out_dir / "newsletters.md", "Newsletters not found.")
    elif _is_running:
        _s = infer_status(st.session_state.get("active_stdout", ""))
        if _s["newsletters_gen_done"]:
            if latest_output_dir is not None:
                render_campaign_caption(campaign_data, campaign_record)
                show_markdown_file(latest_output_dir / "newsletters.md", "Newsletters not yet saved.")
            else:
                st.info("Newsletters generated — loading...")
        elif _s["blog_gen_done"] or _s["blog_done"]:
            st.info("Writing newsletters...")
        elif _s["outline_done"]:
            st.info("Writing blog post...")
        elif _s["step1_started"]:
            st.info("Generating outline...")
        else:
            st.info("Pipeline starting...")
    elif st.session_state.get("pipeline_error"):
        st.error("Pipeline failed — no new content to show. Fix the error in the **Run** tab and try again.")
    elif not pipeline_started:
        st.warning("⚠️ No active run. Run the pipeline to generate content.")
    elif latest_output_dir is None:
        st.info("No output found.")
    else:
        render_campaign_caption(campaign_data, campaign_record)
        show_markdown_file(
            latest_output_dir / "newsletters.md",
            "No newsletters.md found in the latest output folder.",
        )

# ── Campaign Report tab ────────────────────────────────────────────────────────
with tabs[3]:
    if st.button("Refresh Stats"):
        refresh_stats()

    sync_status = st.session_state.get("campaign_report_sync_status")
    if sync_status:
        st.caption(sync_status)

    if not pipeline_started:
        st.warning("⚠️ No active run. Run the pipeline to generate content.")
    elif latest_campaign_id is None:
        st.info("No campaign ID found in the latest output.")
    else:
        report_path = REPORTS_DIR / f"campaign_{latest_campaign_id}.md"
        if report_path.exists():
            render_campaign_report_file(report_path, "")
        else:
            st.info(
                f"No report for campaign #{latest_campaign_id}. "
                "Click 'Refresh Stats' after emails have been sent and opened."
            )

# ── Dashboard tab ──────────────────────────────────────────────────────────────
with tabs[4]:
    render_dashboard_tab()

# ── Auto-rerun while pipeline is active ───────────────────────────────────────
if st.session_state.get("active_process") is not None:
    time.sleep(0.6)
    st.rerun()
