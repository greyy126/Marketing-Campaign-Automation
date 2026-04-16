# NovaMind Marketing Automation Agent

## Project overview

AI-powered content pipeline for NovaMind — a fictional early-stage startup that helps small creative agencies automate workflows. The agent takes a blog topic end-to-end: outline generation, blog generation, newsletter distribution via Brevo CRM, and performance reporting.

## Architecture

```
topic input
  → generate_outline()     structured control layer (5-7 sections with goal + persona_relevance)
  → generate_blog()        section-by-section from the outline
  → generate_newsletters() persona-filtered from outline sections, not full blog summary
  → Brevo CRM              contacts upserted, lists created, campaigns created and sent
  → simulate_metrics()     engagement data per persona
  → generate_performance_summary()  AI growth analyst report
```

## Running the agent

```bash
# Full pipeline (requires ANTHROPIC_API_KEY + BREVO_API_KEY)
python agent.py run --topic "AI in creative automation"

# Skip Claude API — use mock content (requires BREVO_API_KEY only)
python agent.py run --topic "AI in creative automation" --mock-ai

# Content generation only — skip Brevo entirely
python agent.py run --topic "AI in creative automation" --mock-ai --dry-run

# View historical campaign metrics
python agent.py history

# Run content generator tests
python test_content.py
```

## Personas

Defined in `src/mock_data.py`. Must stay in sync with `NEWSLETTER_PERSONAS` in `src/content_generator.py`.

| Slug | Display name | Focus |
|---|---|---|
| `agency_founder` | Agency Founder | Growth, scaling, leverage |
| `creative_professional` | Creative Professional | Tools, workflow, creative time |
| `marketing_manager` | Marketing Manager | ROI, efficiency, proving value |

## Key constraints enforced by validators

- Blog draft: 400-600 words, no em dashes, must have `title`, `outline`, `sections`, `draft` keys
- Outline: 5-7 sections, valid goals only, `cta` must be last, each persona in >= 2 sections
- Newsletter bodies: 120-180 words, no em dashes, must include `[READ MORE]` CTA
- Valid outline goals: `hook`, `pain_point`, `context`, `proof`, `solution`, `how_to`, `cta`

## Content generation rules

- Content should be specific, credible, and internally consistent
- No em dashes anywhere in generated content (use commas, colons, or restructure)
- Newsletters are persona-filtered from outline sections, not summaries of the full blog
- `generate_content(topic)` is the single entry point — chains all three content steps

## File structure

```
agent.py                   # CLI entry point
prompts/
  system_prompt.md         # System prompt loaded by ContentGenerator on every Claude call
src/
  content_generator.py     # All AI generation logic + MockContentGenerator
  mock_data.py             # Personas, mock contacts, performance benchmarks
  crm_manager.py           # Brevo CRM: contacts, folders, persona lists
  campaign_manager.py      # Brevo: campaign creation + HTML templating
  performance_tracker.py   # Metric simulation + historical summary
  database.py              # SQLite schema and helpers
test_content.py            # Content generator tests (93 assertions)
output/                    # Generated blog.md, newsletters.md, campaign.json
reports/                   # Performance reports per run
data/novamind.db           # SQLite — campaigns, newsletters, metrics
```

## Environment variables

```
ANTHROPIC_API_KEY   # Required unless --mock-ai is set
BREVO_API_KEY       # Required unless --dry-run is set
```

## Do not

- Change CRM or campaign logic when working on content generation
- Add em dashes to any generated or mock content
- Modify persona slugs without updating mock_data.py, content_generator.py, and agent.py together
- Rename `generate_content()` — it is the public entry point called by agent.py
- Edit `prompts/system_prompt.md` without also reviewing the per-call prompts in content_generator.py for overlap or contradiction
