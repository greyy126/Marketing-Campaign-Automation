# NovaMind Marketing Automation Agent

An end-to-end marketing automation pipeline that generates blogs, sends persona-targeted campaigns via Brevo, and optimizes content using real engagement data.

## Overview

This project implements a lightweight AI-powered marketing pipeline. Given a blog topic, the system:

1. generates a structured blog outline and a 400-600-word blog post
2. creates three persona-specific newsletter variants
3. syncs contacts and segments in Brevo
4. creates and sends one campaign per persona
5. stores campaign history and performance data in SQLite
6. generates campaign and dashboard-level performance summaries

The repo includes both:

- a CLI pipeline in `agent.py`
- a Streamlit UI in `app.py`

## Run Locally

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

Create a `.env` file based on `.env.example`:

```env
ANTHROPIC_API_KEY=your_anthropic_api_key
BREVO_API_KEY=your_brevo_api_key
```

Notes:

- `ANTHROPIC_API_KEY` is required unless you use `--mock-ai`
- `BREVO_API_KEY` is required unless you use `--dry-run`

## Streamlit UI

Start the web app:

```bash
streamlit run app.py
```

The Streamlit app is the easiest way to demo the project. It lets you:

- trigger a new pipeline run
- inspect the latest blog and newsletters
- refresh campaign stats and view the campaign report
- review the dashboard
- explore suggested next topics based on prior campaign performance

## Architecture Overview

The pipeline is organized as four stages:

1. AI content generation
2. CRM setup and audience segmentation
3. campaign creation and delivery
4. performance logging and analysis

### Flow Diagram

```text
Topic Input
   |
   v
Content Generation (Anthropic Claude)
   |- generate_outline()
   |- generate_blog()
   `- generate_newsletters()
   |
   v
Structured Output Saved
   |- output/YYYY-MM-DD_HH-MM/blog.md
   |- output/YYYY-MM-DD_HH-MM/newsletters.md
   `- output/YYYY-MM-DD_HH-MM/campaign.json
   |
   v
CRM Setup (Brevo)
   |- create/find persona lists
   `- upsert mock contacts
   |
   v
Campaign Delivery (Brevo)
   |- create one email campaign per persona
   |- send campaigns
   `- write CRM-side campaign notes
   |
   v
Local Persistence (SQLite)
   |- campaigns
   |- newsletters
   `- performance_metrics
   |
   v
Performance Analysis
   |- simulate baseline metrics or fetch live Brevo metrics
   |- generate campaign report
   `- generate dashboard insights and next-topic suggestions
```

### Module Responsibilities

- `agent.py`: CLI entry point and end-to-end pipeline orchestration
- `app.py`: Streamlit UI for running, reviewing, and refreshing the pipeline
- `src/content_generator.py`: AI generation, validation, campaign summaries, dashboard insights
- `src/crm_manager.py`: Brevo CRM integration for contacts, lists, and CRM-side audit notes
- `src/campaign_manager.py`: Brevo email campaign creation and sending
- `src/performance_tracker.py`: simulated/live metrics, aggregation, dashboard data
- `src/database.py`: SQLite schema and persistence helpers
- `src/mock_data.py`: personas, mock contacts, benchmark ranges
- `src/personas.py`: canonical persona definitions and mapping helpers

## Tools, APIs, and Models Used

### Languages and Libraries

- Python 3
- `anthropic`
- `requests`
- `python-dotenv`
- `rich`
- `click`
- `streamlit`
- built-in `sqlite3`

### External Services

- Anthropic API for content generation and AI-written analysis
- Brevo API for CRM contacts, segmentation lists, email campaigns, and CRM-side campaign notes

### Model

- Claude Sonnet 4.6 via the Anthropic SDK
- model ID in code: `claude-sonnet-4-6`

### Output Formats

- Markdown for generated blogs, newsletters, and reports
- JSON for run snapshots
- SQLite for campaign/newsletter/metrics history

## Assumptions and Simplifications

This project intentionally uses a few pragmatic shortcuts:

- mock contacts are used instead of a real lead database
- only three mock contacts are included by default, one per persona
- simulated performance metrics are generated immediately after send to create a baseline report
- live Brevo metrics can later replace those simulated values when available
- newsletters are sent through Brevo, but campaign analytics are also persisted locally in SQLite for historical comparison
- campaign history informs future generation, reporting, and topic recommendations
- the Streamlit app launches the CLI as a subprocess instead of duplicating pipeline logic inside the UI

## Important Design Choices

### How the Content Pipeline Works

The outline is the control layer for downstream content.

- `generate_outline(topic)` creates 5-7 sections with a `goal` and `persona_relevance`
- `generate_blog(topic, outline)` writes the blog section-by-section from that outline
- `generate_newsletters(blog)` produces three persona-specific newsletters based on the relevant outline sections

This means the newsletters are not generic summaries of the whole blog. They are derived from the parts of the outline intended for each audience.

### Personas

The pipeline targets three personas:

| Slug | Display Name | Focus |
| --- | --- | --- |
| `agency_founder` | Agency Founder | growth, scaling, leverage |
| `creative_professional` | Creative Professional | workflow, tools, creative time |
| `marketing_manager` | Marketing Manager | ROI, efficiency, measurable impact |

### CRM and Logging Behavior

Brevo is used as the operational CRM and email delivery system.

The pipeline currently:

- creates or reuses persona lists in Brevo
- upserts mock contacts into those lists
- creates one Brevo email campaign per persona
- sends the campaigns
- fetches campaign status from Brevo
- creates a CRM-side campaign note in Brevo containing:
  - topic
  - blog title
  - persona
  - Brevo campaign ID
  - list ID
  - status
  - send date

In addition to the CRM-side record, the app stores campaign metadata and historical metrics locally in SQLite for analysis and reporting.

### Performance Tracking

The pipeline supports two performance modes:

- baseline simulated metrics right after sending
- live Brevo stat refresh later using `refresh-stats`

Tracked metrics:

- open rate
- click rate
- unsubscribe rate
- total sent

Historical performance is stored in `data/novamind.db` and used in two ways:

- campaign-level AI summaries
- dashboard-level cross-campaign insights and suggested future topics

### Why Brevo instead of HubSpot?

The assignment allowed HubSpot or a similar CRM. Brevo was used here as the CRM and email-delivery platform because it supports:

- contact management
- segmentation via lists
- campaign creation and sending
- API-driven status checks
- CRM-side note logging

### Why SQLite?

Brevo is used for operational delivery, but SQLite is better suited for:

- persistent local history
- campaign-to-campaign comparison
- dashboard aggregation
- offline inspection during development

### Why both CLI and UI?

- the CLI is the clean automation entry point
- the Streamlit app makes the pipeline easy to demo, inspect, and refresh

## Run via CLI

Run the full live pipeline:

```bash
python agent.py run --topic "AI in creative automation"
```

Run with mock AI content but still use Brevo:

```bash
python agent.py run --topic "AI in creative automation" --mock-ai
```

Generate content only and skip CRM/campaign steps:

```bash
python agent.py run --topic "AI in creative automation" --mock-ai --dry-run
```

View historical metrics:

```bash
python agent.py history
```

Refresh live campaign stats from Brevo for a specific campaign:

```bash
python agent.py refresh-stats --campaign-id 29
```

Regenerate the dashboard:

```bash
python agent.py dashboard
```

Or use heuristic dashboard insights without Anthropic:

```bash
python agent.py dashboard --mock-ai
```

## Storage and Outputs

### Generated Content

- `output/YYYY-MM-DD_HH-MM/blog.md`
- `output/YYYY-MM-DD_HH-MM/newsletters.md`
- `output/YYYY-MM-DD_HH-MM/campaign.json`

### Reports

- `reports/campaign_<id>.md`
- `reports/dashboard.md`

### Database

- `data/novamind.db`

Tables include:

- `campaigns`
- `newsletters`
- `performance_metrics`

## Validation and Quality Controls

The content layer includes explicit validation rules.

- outline must contain 5-7 sections
- each section must have valid goals and persona relevance
- blog draft must be 400-600 words
- newsletter bodies must be 120-180 words
- generated content avoids em dashes
- blog sections must align with the outline exactly

These checks are designed to make the pipeline more deterministic and less fragile than a pure prompt-only workflow.

## Repository Structure

```text
.
├── agent.py
├── app.py
├── README.md
├── docs/
│   ├── ARCHITECTURE.md
│   └── Claude.md
├── requirements.txt
├── .env.example
├── prompts/
│   └── system_prompt.md
├── src/
│   ├── campaign_manager.py
│   ├── content_generator.py
│   ├── crm_manager.py
│   ├── database.py
│   ├── mock_data.py
│   ├── performance_tracker.py
│   └── personas.py
├── output/
├── reports/
└── data/
    └── novamind.db
```

## Known Limitations

- performance is initially simulated unless refreshed from Brevo later
- there is no formal scheduler; runs are manually triggered
- current mock dataset is intentionally small
- the included test script focuses on content generation behavior, not full end-to-end integration

## Suggested Demo Flow

If you are reviewing the project manually, the fastest path is:

1. install dependencies and configure environment variables
2. start the Streamlit app
3. trigger a pipeline run from the Run tab
4. inspect the generated blog and newsletters
5. open the Campaign Report tab and refresh stats
6. open the Dashboard tab to review historical insights and topic suggestions

## Additional Notes

- `docs/ARCHITECTURE.md` contains a concise pipeline summary
- the repo also includes generated outputs and reports from prior runs
- the app is designed to keep the core pipeline logic in the CLI and use the UI as a thin orchestration layer
