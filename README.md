# NovaMind Marketing Automation Pipeline

An end-to-end marketing automation pipeline that generates blogs, sends persona-targeted campaigns via Brevo, and optimizes content using real engagement data.

Demo Walkthrough: https://drive.google.com/file/d/1v-bP_PeSGyTF3xeJuleeMnANA_58PGM5/view?usp=sharing

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

- The outline is generated first and reused across the blog and persona-specific newsletters to keep content consistent
- Only three mock contacts are included by default, one per persona
- Brevo handles contacts, lists, campaigns, and send status, while SQLite stores local history for reporting and comparison
- Simulated performance metrics are generated immediately after send to create a baseline report and replaced by live Brevo data
- Campaign history informs future generation, reporting, and topic recommendations
- Generated content is validated for structure, word count, persona coverage, and formatting rules before downstream use
- The Streamlit app launches the CLI as a subprocess instead of duplicating pipeline logic inside the UI

## Personas

The pipeline targets three personas:


| Slug                    | Display Name          | Focus                              |
| ----------------------- | --------------------- | ---------------------------------- |
| `agency_founder`        | Agency Founder        | growth, scaling, leverage          |
| `creative_professional` | Creative Professional | workflow, tools, creative time     |
| `marketing_manager`     | Marketing Manager     | ROI, efficiency, measurable impact |


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

The content layer includes explicit validation rules:

- outline must contain 5-7 sections
- each section must have valid goals and persona relevance
- blog draft must be 400-600 words
- newsletter bodies must be 120-180 words
- generated content avoids em dashes
- blog sections must align with the outline exactly

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

## Future Features

- **Knowledge-Backed Generation** : Ground content in verified sources or internal documents instead of relying solely on model-generated outputs.

- **Scheduling and Orchestration** : Support recurring runs, queued jobs, retries, and structured workflows like "publish then send."

- **Improved Attribution** : Extend beyond email metrics to track downstream outcomes such as site visits, signups, and revenue impact for better ROI visibility.

- **Send-Time Optimization** : Use historical engagement data to recommend or automatically select optimal send times per persona.

- **Real-Time Performance Tracking** : Replace manual refresh with webhook-based ingestion of Brevo events such as opens, clicks, and bounces for faster reporting.

- **Cost Tracking Per Run** : Log API usage and estimated cost per run, then surface it in campaign reports for better budget awareness.

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
- `prompts/system_prompt.md` contains the reusable system prompt applied to Claude content-generation calls
- `docs/Claude.md` is a maintainer guide describing the Claude-driven content pipeline, constraints, and editing rules
- the repo also includes generated outputs and reports from prior runs
- the app is designed to keep the core pipeline logic in the CLI and use the UI as a thin orchestration layer

