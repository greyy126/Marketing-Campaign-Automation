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
    latest_dir = get_latest_output_dir()
    if latest_dir is None:
        return None
    campaign_json = latest_dir / "campaign.json"
    if not campaign_json.exists():
        return None
    try:
        data = json.loads(campaign_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    campaign_id = data.get("campaign_id")
    return campaign_id if isinstance(campaign_id, int) else None


def load_campaign_json() -> dict | None:
    latest_dir = get_latest_output_dir()
    if latest_dir is None:
        return None
    campaign_json = latest_dir / "campaign.json"
    if not campaign_json.exists():
        return None
    try:
        return json.loads(campaign_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def get_all_runs() -> list[dict]:
    """Scan output/ folders and return run metadata, newest first."""
    if not OUTPUT_DIR.exists():
        return []
    runs = []
    for folder in sorted(OUTPUT_DIR.iterdir(), key=lambda p: p.name, reverse=True):
        if not folder.is_dir():
            continue
        cj = folder / "campaign.json"
        if not cj.exists():
            continue
        try:
            data = json.loads(cj.read_text(encoding="utf-8"))
            campaign_id = data.get("campaign_id")
            blog_title = (data.get("blog") or {}).get("title") or "Untitled"
            # Format timestamp: "2026-04-15_04-33" → "2026-04-15  04:33"
            parts = folder.name.split("_")
            ts_display = (
                f"{parts[0]}  {parts[1].replace('-', ':')}"
                if len(parts) == 2
                else folder.name
            )
            runs.append(
                {
                    "folder": folder,
                    "ts_display": ts_display,
                    "blog_title": blog_title,
                    "campaign_id": campaign_id,
                    "has_blog": (folder / "blog.md").exists(),
                    "has_newsletters": (folder / "newsletters.md").exists(),
                    "has_report": bool(campaign_id)
                    and (REPORTS_DIR / f"campaign_{campaign_id}.md").exists(),
                }
            )
        except (json.JSONDecodeError, OSError):
            continue
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
    st.session_state.setdefault("last_suggested_topics", ())


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


def _fallback_topic_suggestions(persona: str, themes: list[str], variant: int = 0) -> list[str]:
    base_themes = list(themes[:3]) if themes else []
    while len(base_themes) < 3:
        base_themes.append(
            ["workflow efficiency", "client reporting", "team capacity"][
                len(base_themes)
            ]
        )
    templates = [
        f"How to improve {base_themes[0]} with AI automation",
        f"A practical guide to better {base_themes[1]}",
        f"How small teams can scale {base_themes[2]} without extra overhead",
        f"What high-performing teams get right about {base_themes[0]}",
        f"How to turn {base_themes[1]} into a repeatable growth system",
        f"Why better {base_themes[2]} matters more than adding headcount",
    ]
    if not templates:
        return []
    start = variant % len(templates)
    ordered = templates[start:] + templates[:start]
    return ordered[:3]


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

    prompt = f"""You are refining blog topic titles for NovaMind.

Top performing persona:
{persona}

High-performing campaign themes:
{", ".join(theme_list)}

Task:
- Generate exactly 3 concise, practical blog topic titles
- Use only the persona and themes above
- Refine and combine these themes, do not introduce unrelated angles
- Persona should influence prioritization only, not appear in the titles
- Write broad blog topics, not persona-targeted headlines or email subject lines
- Keep each title specific and useful for a marketing content calendar
- Do not include outdated years; prefer evergreen titles unless a year is necessary
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


def render_campaign_caption(campaign_data: dict | None) -> None:
    if campaign_data is None:
        return
    cid = campaign_data.get("campaign_id", "?")
    blog_title = (campaign_data.get("blog") or {}).get("title", "")
    topic_str = st.session_state.get("active_topic", "")
    parts = [f"Campaign #{cid}"]
    if topic_str:
        parts.append(topic_str)
    if blog_title:
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


def start_pipeline(topic: str, mock_ai: bool) -> None:
    if not topic.strip():
        st.error("Enter a topic before running the pipeline.")
        return
    if st.session_state["active_process"] is not None:
        st.warning("A pipeline run is already in progress.")
        return

    cmd = [sys.executable, "agent.py", "run", "--topic", topic.strip()]
    label = "Run Mock Pipeline" if mock_ai else "Run Pipeline"
    if mock_ai:
        cmd.append("--mock-ai")

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

    st.session_state.update(
        {
            "last_run_mode": "mock" if mock_ai else "live",
            "last_command_result": None,
            "last_command_label": label,
            "last_pipeline_result": None,
            "active_process": proc,
            "active_queue": output_queue,
            "active_stdout": "",
            "active_stderr": "",
            "active_label": label,
            "active_topic": topic.strip(),
            "pipeline_started": True,
        }
    )


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

    st.session_state["last_command_result"] = {
        "returncode": proc.returncode,
        "stdout": st.session_state["active_stdout"],
        "stderr": st.session_state["active_stderr"],
    }
    st.session_state["last_command_label"] = st.session_state.get("active_label")
    st.session_state["last_pipeline_result"] = dict(st.session_state["last_command_result"])
    st.session_state["active_process"] = None
    st.session_state["active_queue"] = None


# ── Pipeline status widget ─────────────────────────────────────────────────────


def infer_status(stdout: str) -> dict[str, bool]:
    return {
        "step1_started": "Step 1 · AI Content Generation" in stdout,
        "outline_done": "Outline:" in stdout,
        "blog_done": "Blog:" in stdout,
        "newsletters_done": "Content saved" in stdout,
        "step2_done": "contacts synced to Brevo" in stdout,
        "step3_done": "Emails dispatched to real contacts via Brevo" in stdout,
        "step4_started": "Step 4 · Baseline Metrics" in stdout,
    }


def _indicator(done: bool, label: str, in_progress: bool = False) -> str:
    if done:
        return f"{label} &nbsp;✓"
    if in_progress:
        return f"{label} &nbsp;..."
    return f"<span style='color:#bbb'>{label}</span>"


def render_pipeline_status() -> None:
    active_stdout = st.session_state.get("active_stdout", "")
    is_running = st.session_state.get("active_process") is not None
    run_result = st.session_state.get("last_pipeline_result")

    if not is_running and run_result is None:
        return

    source_stdout = active_stdout if is_running else str(run_result.get("stdout", ""))
    s = infer_status(source_stdout)

    outline_ip = s["step1_started"] and not s["outline_done"]
    blog_ip = s["outline_done"] and not s["blog_done"]
    newsletters_ip = s["blog_done"] and not s["newsletters_done"]
    step2_ip = s["newsletters_done"] and not s["step2_done"]
    step3_ip = s["step2_done"] and not s["step3_done"]
    step4_ip = s["step3_done"] and is_running

    status_html = f"""
    <div class="pipeline-card">
        <h4>Pipeline Status</h4>
        <div class="pipeline-step">
            <strong>Step 1 &middot; AI Content Generation</strong>
            <div>{_indicator(s["outline_done"], "Outline generated", outline_ip)}</div>
            <div>{_indicator(s["blog_done"], "Blog generated", blog_ip)}</div>
            <div>{_indicator(s["newsletters_done"], "Newsletters generated", newsletters_ip)}</div>
        </div>
        <div class="pipeline-step">
            <strong>Step 2 &middot; CRM Setup</strong>
            <div>{_indicator(s["step2_done"], "Contacts synced", step2_ip)}</div>
        </div>
        <div class="pipeline-step">
            <strong>Step 3 &middot; Campaign Creation</strong>
            <div>{_indicator(s["step3_done"], "Campaigns created", step3_ip)}</div>
        </div>
        <div class="pipeline-step">
            <strong>Step 4 &middot; Performance</strong>
            <div>{_indicator(False, "Waiting for engagement data...", step4_ip)}</div>
        </div>
        <div class="pipeline-hints">
            <span>Refresh stats to view results</span>
            <span>View campaign report in the Campaign Report tab</span>
        </div>
    </div>
    """

    if is_running:
        st.markdown(status_html, unsafe_allow_html=True)
    else:
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

st.set_page_config(page_title="NovaMind Marketing Agent", layout="wide")
inject_css()

st.title("NovaMind Marketing Agent")
st.caption(
    "An end-to-end marketing automation pipeline that generates blogs, sends "
    "persona-targeted campaigns via Brevo, and optimizes content using real "
    "engagement data"
)

# Shared state — computed once, referenced across tabs
latest_output_dir = get_latest_output_dir()
latest_campaign_id = load_latest_campaign_id()
campaign_data = load_campaign_json()
pipeline_started: bool = st.session_state["pipeline_started"]

tabs = st.tabs(["Run", "Blog", "Newsletters", "Campaign Report", "Dashboard"])

# ── Run tab ────────────────────────────────────────────────────────────────────
with tabs[0]:
    st.markdown("#### Start a New Run")
    topic = st.text_input(
        "Topic",
        placeholder='e.g. "AI in creative automation"',
        label_visibility="collapsed",
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Run Pipeline", use_container_width=True):
            start_pipeline(topic, mock_ai=False)
    with col2:
        if st.button("Run Mock Pipeline", use_container_width=True):
            start_pipeline(topic, mock_ai=True)

    top_persona, top_themes = get_suggested_topic_inputs()
    if top_persona and top_themes:
        recent_topics = tuple(get_recent_campaign_topics())
        previous_topics = tuple(st.session_state.get("last_suggested_topics", ()))
        suggested_topics = generate_suggested_topics(
            top_persona,
            tuple(top_themes),
            recent_topics,
            previous_topics,
            int(st.session_state.get("suggested_topics_refresh_nonce", 0)),
        )
        st.session_state["last_suggested_topics"] = tuple(suggested_topics)
        if suggested_topics:
            topics_html = "".join(
                f"<div style='margin-top:0.4rem;'>➡️ {topic}</div>"
                for topic in suggested_topics[:3]
            )
            st.markdown(
                f"""
                <div style="
                    background:#edf4f8;
                    border:1px solid #d2e0e8;
                    border-radius:0.6rem;
                    padding:0.9rem 1rem;
                    margin-top:0.75rem;
                    margin-bottom:0.25rem;
                ">
                    <div style="font-size:1.05rem; font-weight:600; margin-bottom:0.35rem;">
                        💡 Suggested Topics (based on past campaign performance)
                    </div>
                    {topics_html}
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button("Refresh Suggested Topics"):
                st.session_state["suggested_topics_refresh_nonce"] = (
                    int(st.session_state.get("suggested_topics_refresh_nonce", 0)) + 1
                )
                st.rerun()

    render_pipeline_status()

    # Previous runs
    st.divider()
    st.markdown("#### Previous Runs")
    all_runs = get_all_runs()
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
    if not pipeline_started:
        st.warning("⚠️ No active run. Run the pipeline to generate content.")
    elif latest_output_dir is None:
        st.info("No output found.")
    else:
        render_campaign_caption(campaign_data)
        show_markdown_file(
            latest_output_dir / "blog.md",
            "No blog.md found in the latest output folder.",
        )

# ── Newsletters tab ────────────────────────────────────────────────────────────
with tabs[2]:
    if not pipeline_started:
        st.warning("⚠️ No active run. Run the pipeline to generate content.")
    elif latest_output_dir is None:
        st.info("No output found.")
    else:
        render_campaign_caption(campaign_data)
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
