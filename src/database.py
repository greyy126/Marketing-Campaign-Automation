"""
SQLite persistence layer for campaigns, newsletters, and performance metrics.
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo


DB_PATH = Path(__file__).parent.parent / "data" / "novamind.db"
PT = ZoneInfo("America/Los_Angeles")


def _pt_now() -> datetime:
    return datetime.now(PT)


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't already exist."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                topic       TEXT    NOT NULL,
                blog_title  TEXT    NOT NULL,
                blog_draft  TEXT    NOT NULL,
                blog_outline TEXT   NOT NULL,
                created_at  TEXT    NOT NULL,
                sent_at     TEXT,
                status      TEXT    NOT NULL DEFAULT 'draft'
            );

            CREATE TABLE IF NOT EXISTS newsletters (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id         INTEGER NOT NULL,
                persona             TEXT    NOT NULL,
                subject             TEXT    NOT NULL,
                body                TEXT    NOT NULL,
                brevo_campaign_id   INTEGER,
                brevo_list_id       INTEGER,
                crm_status          TEXT,
                crm_sent_at         TEXT,
                crm_status_reason   TEXT,
                crm_last_synced_at  TEXT,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
            );

            CREATE TABLE IF NOT EXISTS performance_metrics (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id     INTEGER NOT NULL,
                persona         TEXT    NOT NULL,
                total_sent      INTEGER NOT NULL,
                opens           INTEGER NOT NULL,
                clicks          INTEGER NOT NULL,
                unsubscribes    INTEGER NOT NULL,
                open_rate       REAL    NOT NULL,
                click_rate      REAL    NOT NULL,
                unsubscribe_rate REAL   NOT NULL,
                recorded_at     TEXT    NOT NULL,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
            );
        """)
        _ensure_column(conn, "newsletters", "crm_status", "TEXT")
        _ensure_column(conn, "newsletters", "crm_sent_at", "TEXT")
        _ensure_column(conn, "newsletters", "crm_status_reason", "TEXT")
        _ensure_column(conn, "newsletters", "crm_last_synced_at", "TEXT")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def get_campaign(campaign_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM campaigns WHERE id=?", (campaign_id,)
        ).fetchone()
    return dict(row) if row else None


def save_campaign(topic: str, blog_title: str, blog_draft: str, blog_outline: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO campaigns (topic, blog_title, blog_draft, blog_outline, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (topic, blog_title, blog_draft, blog_outline, _pt_now().isoformat()),
        )
        return cur.lastrowid


def save_newsletter(campaign_id: int, persona: str, subject: str, body: str,
                    brevo_campaign_id: int = None, brevo_list_id: int = None,
                    crm_status: str | None = None, crm_sent_at: str | None = None,
                    crm_status_reason: str | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO newsletters
               (campaign_id, persona, subject, body, brevo_campaign_id, brevo_list_id,
                crm_status, crm_sent_at, crm_status_reason, crm_last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                campaign_id,
                persona,
                subject,
                body,
                brevo_campaign_id,
                brevo_list_id,
                crm_status,
                crm_sent_at,
                crm_status_reason,
                _pt_now().isoformat() if crm_status else None,
            ),
        )
        return cur.lastrowid


def mark_campaign_sent(campaign_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE campaigns SET status='sent', sent_at=? WHERE id=?",
            (_pt_now().isoformat(), campaign_id),
        )


def save_metrics(campaign_id: int, persona: str, total_sent: int,
                 opens: int, clicks: int, unsubscribes: int) -> None:
    open_rate        = opens        / total_sent if total_sent else 0
    click_rate       = clicks       / total_sent if total_sent else 0
    unsub_rate       = unsubscribes / total_sent if total_sent else 0
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO performance_metrics
               (campaign_id, persona, total_sent, opens, clicks, unsubscribes,
                open_rate, click_rate, unsubscribe_rate, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (campaign_id, persona, total_sent, opens, clicks, unsubscribes,
             open_rate, click_rate, unsub_rate, _pt_now().isoformat()),
        )


def update_metrics(campaign_id: int, persona: str, total_sent: int,
                   opens: int, clicks: int, unsubscribes: int) -> None:
    open_rate  = opens        / total_sent if total_sent else 0
    click_rate = clicks       / total_sent if total_sent else 0
    unsub_rate = unsubscribes / total_sent if total_sent else 0
    with get_connection() as conn:
        conn.execute(
            """UPDATE performance_metrics
               SET opens=?, clicks=?, unsubscribes=?,
                   open_rate=?, click_rate=?, unsubscribe_rate=?, recorded_at=?
               WHERE campaign_id=? AND persona=?""",
            (opens, clicks, unsubscribes,
             open_rate, click_rate, unsub_rate, _pt_now().isoformat(),
             campaign_id, persona),
        )


def get_metrics_for_campaign(campaign_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM performance_metrics WHERE campaign_id=?", (campaign_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_brevo_ids_for_campaign(campaign_id: int) -> dict[str, int]:
    """Return {persona_slug: brevo_campaign_id} for a campaign."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT persona, brevo_campaign_id FROM newsletters "
            "WHERE campaign_id=? AND brevo_campaign_id IS NOT NULL",
            (campaign_id,),
        ).fetchall()
    return {r["persona"]: r["brevo_campaign_id"] for r in rows}


def update_newsletter_crm_state(campaign_id: int, persona: str, *,
                                crm_status: str | None = None,
                                crm_sent_at: str | None = None,
                                crm_status_reason: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE newsletters
               SET crm_status=?, crm_sent_at=?, crm_status_reason=?, crm_last_synced_at=?
               WHERE campaign_id=? AND persona=?""",
            (
                crm_status,
                crm_sent_at,
                crm_status_reason,
                _pt_now().isoformat(),
                campaign_id,
                persona,
            ),
        )


def get_newsletters_for_campaign(campaign_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT *
               FROM newsletters
               WHERE campaign_id=?
               ORDER BY persona ASC""",
            (campaign_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_campaigns() -> list[dict]:
    """Return all campaigns ordered by creation date (oldest first)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM campaigns ORDER BY created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_campaign_metrics() -> list[dict]:
    """Return all historical metrics joined with campaign info."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT pm.*, c.blog_title, c.topic, c.sent_at
            FROM performance_metrics pm
            JOIN campaigns c ON c.id = pm.campaign_id
            ORDER BY pm.recorded_at DESC
        """).fetchall()
    return [dict(r) for r in rows]
