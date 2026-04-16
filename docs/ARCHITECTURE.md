# Architecture

## Pipeline Overview

```
User runs: python agent.py run --topic "..."
│
├── Step 1: Content Generation (Claude AI)
│   ├── generate_outline()     → 5-7 structured sections (goal + persona tags)
│   ├── generate_blog()        → 400-600 word post, section-by-section
│   └── generate_newsletters() → 3 persona-specific emails (120-180 words each)
│
├── Step 2: CRM Setup (Brevo)
│   ├── Create/find persona lists (Agency Founder, Creative Professional, Marketing Manager)
│   └── Upsert 3 mock contacts into their persona lists
│
├── Step 3: Campaign Creation (Brevo)
│   └── Create and send one email campaign per persona
│
└── Step 4: Performance Analysis
    ├── Simulate engagement metrics (open, click, unsubscribe rates)
    └── Generate AI analyst report with recommendations
```

---

## How the Outline Controls Everything

The outline is generated once and drives all downstream content:

```
Outline (5-7 sections)
│   each section: { title, goal, persona_relevance }
│
├── generate_blog()
│   writes one content block per outline section
│
└── generate_newsletters()
    filters sections by persona_relevance
    Marketing Manager    → sees only sections tagged for them
    Creative Professional → sees only sections tagged for them
    Agency Founder       → sees only sections tagged for them
```

This means newsletters are derived from the blog, not summaries of it.

---

## Model

Claude Sonnet 4.6 (`claude-sonnet-4-6`) via the Anthropic SDK.

Temperature varies by task:

- `0.3` — outline + performance summary (structured reasoning)
- `0.4` — blog + newsletters (some stylistic variation)

---

## Flags


| Flag        | Effect                                                |
| ----------- | ----------------------------------------------------- |
| *(none)*    | Full pipeline: Claude + Brevo                         |
| `--mock-ai` | Skip Claude, use hardcoded content. Brevo still runs. |
| `--dry-run` | Skip Brevo entirely. Content only.                    |


---

## Storage


| What                 | Where                         |
| -------------------- | ----------------------------- |
| Blog + newsletters   | `output/YYYY-MM-DD_HH-MM/`    |
| Performance reports  | `reports/campaign_<id>.md` and `reports/dashboard.md` |
| Campaign history     | `data/novamind.db` (SQLite)   |
| Contacts + campaigns | Brevo (live)                  |
