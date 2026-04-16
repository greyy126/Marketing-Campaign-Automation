"""
AI content generation via the Anthropic Claude SDK.

Three-step pipeline where the outline is the central control layer:

  1. generate_outline(topic)      — structured outline drives everything downstream
  2. generate_blog(topic, outline) — section-by-section, each aligned to goal
  3. generate_newsletters(blog)   — persona-filtered from outline, not full summary

Top-level entry point:
  generate_content(topic) → { outline, blog, newsletters }

Use MockContentGenerator to run the full pipeline without an Anthropic API key.
"""

import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import requests
from src.personas import NEWSLETTER_PERSONAS, persona_label

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.md"

def _load_system_prompt() -> str:
    """Load the system prompt from prompts/system_prompt.md if it exists."""
    if _SYSTEM_PROMPT_PATH.exists():
        return _SYSTEM_PROMPT_PATH.read_text().strip()
    return ""

# ── Constants ──────────────────────────────────────────────────────────────────

VALID_GOALS = frozenset({"hook", "pain_point", "context", "proof", "solution", "how_to", "cta"})

EM_DASH_RE = re.compile(r"\u2014|—")
HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
    re.IGNORECASE | re.DOTALL,
)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
WORD_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a", "an", "and", "are", "for", "from", "how", "in", "of", "on", "or",
    "report", "state", "the", "to", "with",
}


# ── Validation ─────────────────────────────────────────────────────────────────

class ValidationError(ValueError):
    pass


def _validate_sources(sources: list) -> None:
    """Raise ValidationError if the research output is malformed."""
    if not isinstance(sources, list):
        raise ValidationError("Research step did not return a JSON array.")
    if not (4 <= len(sources) <= 6):
        raise ValidationError(f"Research step must return 4-6 sources, got {len(sources)}.")

    for i, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValidationError(f"Source {i} is not an object.")
        for key in ("title", "publisher", "published_date", "key_takeaways"):
            if key not in source:
                raise ValidationError(f"Source {i} missing key '{key}'.")
        if not isinstance(source["key_takeaways"], list) or not source["key_takeaways"]:
            raise ValidationError(
                f"Source {i} 'key_takeaways' must be a non-empty list."
            )


def _extract_html_title(html: str) -> str:
    match = HTML_TITLE_RE.search(html or "")
    if not match:
        return ""
    title = unescape(match.group(1))
    return re.sub(r"\s+", " ", title).strip()


def _extract_verification_candidates(html: str) -> list[str]:
    candidates = []

    title = _extract_html_title(html)
    if title:
        candidates.append(title)

    og_match = OG_TITLE_RE.search(html or "")
    if og_match:
        candidates.append(re.sub(r"\s+", " ", unescape(og_match.group(1))).strip())

    h1_match = H1_RE.search(html or "")
    if h1_match:
        h1_text = re.sub(r"<[^>]+>", " ", h1_match.group(1))
        candidates.append(re.sub(r"\s+", " ", unescape(h1_text)).strip())

    # Preserve order while removing duplicates
    unique = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def _title_tokens(text: str) -> set[str]:
    return {
        token for token in WORD_RE.findall(text.lower())
        if len(token) > 2 and token not in STOPWORDS
    }


def _title_matches_source(source_title: str, page_title: str) -> bool:
    source_tokens = _title_tokens(source_title)
    page_tokens = _title_tokens(page_title)
    if not source_tokens or not page_tokens:
        return False

    overlap = source_tokens & page_tokens
    if len(overlap) >= 3:
        return True

    return len(overlap) / len(source_tokens) >= 0.6


def _canonical_blog_title(topic: str, fallback: str = "") -> str:
    """Use the requested topic as the canonical blog title whenever possible."""
    title = re.sub(r"\s+", " ", (topic or "").strip())
    if title:
        return title
    return re.sub(r"\s+", " ", (fallback or "").strip())


def _validate_outline(outline: list) -> None:
    """Raise ValidationError if the structured outline fails any quality gate."""
    if not isinstance(outline, list):
        raise ValidationError("Outline must be a JSON array.")
    if not (5 <= len(outline) <= 7):
        raise ValidationError(
            f"Outline must have 5-7 sections, got {len(outline)}."
        )
    for i, item in enumerate(outline):
        for key in ("title", "goal", "persona_relevance"):
            if key not in item:
                raise ValidationError(f"Outline section {i} missing key '{key}'.")
        if item["goal"] not in VALID_GOALS:
            raise ValidationError(
                f"Outline section {i} has invalid goal '{item['goal']}'. "
                f"Must be one of: {sorted(VALID_GOALS)}"
            )
        if not isinstance(item["persona_relevance"], list) or not item["persona_relevance"]:
            raise ValidationError(
                f"Outline section {i} 'persona_relevance' must be a non-empty list."
            )
        unknown = set(item["persona_relevance"]) - set(NEWSLETTER_PERSONAS)
        if unknown:
            raise ValidationError(
                f"Outline section {i} references unknown personas: {unknown}"
            )


def _validate_blog(blog: dict) -> None:
    """Raise ValidationError if the blog fails any quality gate."""
    for key in ("title", "outline", "sections", "draft"):
        if key not in blog:
            raise ValidationError(f"Blog JSON missing required key: '{key}'.")

    draft = blog["draft"]
    if EM_DASH_RE.search(draft):
        raise ValidationError("Blog draft contains an em dash. Rewrite without em dashes.")

    word_count = len(draft.split())
    if not (400 <= word_count <= 600):
        raise ValidationError(
            f"Blog draft is {word_count} words — must be between 400 and 600."
        )

    if len(blog["sections"]) != len(blog["outline"]):
        raise ValidationError(
            f"sections count ({len(blog['sections'])}) does not match "
            f"outline count ({len(blog['outline'])})."
        )
    for i, section in enumerate(blog["sections"]):
        for key in ("title", "goal", "content"):
            if key not in section:
                raise ValidationError(f"Blog section {i} missing key '{key}'.")

    assembled = "\n\n".join(s["content"] for s in blog["sections"])
    if blog["draft"] != assembled:
        raise ValidationError(
            "Blog 'draft' does not equal sections joined by double newlines."
        )

    for i, section in enumerate(blog["sections"]):
        expected = blog["outline"][i]["goal"]
        if section["goal"] != expected:
            raise ValidationError(
                f"Blog section {i} goal '{section['goal']}' does not match "
                f"outline goal '{expected}'."
            )


def _validate_newsletters(result: dict) -> None:
    """Raise ValidationError if any newsletter fails quality gates."""
    if "newsletters" not in result:
        raise ValidationError("Newsletter result missing 'newsletters' key.")

    newsletters    = result["newsletters"]
    found_personas = {nl.get("persona") for nl in newsletters}
    missing        = set(NEWSLETTER_PERSONAS) - found_personas
    if missing:
        raise ValidationError(f"Missing persona newsletters: {missing}")

    for nl in newsletters:
        persona = nl.get("persona", "unknown")
        body    = nl.get("body", "")

        if EM_DASH_RE.search(body):
            raise ValidationError(f"Newsletter '{persona}' contains an em dash.")

        word_count = len(body.split())
        if not (120 <= word_count <= 180):
            raise ValidationError(
                f"Newsletter '{persona}' is {word_count} words — must be 120-180."
            )
        if not nl.get("subject", "").strip():
            raise ValidationError(f"Newsletter '{persona}' has an empty subject line.")


def _build_performance_summary(
    metrics: list[dict],
    persona_label,
) -> dict:
    """
    Build a campaign-level performance summary from persona metrics.
    Returns { "status": "live" | "low_confidence" | "valid", "text": str }.
    """
    min_per_persona = min((m.get("total_sent", 0) for m in metrics), default=0)
    has_engagement = any(
        m.get("open_rate", 0) > 0 or m.get("click_rate", 0) > 0
        for m in metrics
    )

    if not has_engagement:
        return {
            "status": "live",
            "text": "No engagement data yet to generate insights on.",
        }

    status = "low_confidence" if min_per_persona <= 5 else "valid"
    by_click = sorted(
        metrics,
        key=lambda m: (m["click_rate"], m["open_rate"], -m["unsubscribe_rate"]),
        reverse=True,
    )
    by_open = sorted(
        metrics,
        key=lambda m: (m["open_rate"], m["click_rate"], -m["unsubscribe_rate"]),
        reverse=True,
    )
    best_click = by_click[0]
    worst_click = by_click[-1]
    best_open = by_open[0]
    worst_open = by_open[-1]
    highest_unsub = max(metrics, key=lambda m: m["unsubscribe_rate"])

    click_gap = best_click["click_rate"] - worst_click["click_rate"]
    open_gap = best_open["open_rate"] - worst_open["open_rate"]
    total_opens = sum(m["opens"] for m in metrics)
    total_clicks = sum(m["clicks"] for m in metrics)
    campaign_ctor = (total_clicks / total_opens) if total_opens else 0.0
    avg_open = sum(m["open_rate"] for m in metrics) / len(metrics)
    avg_click = sum(m["click_rate"] for m in metrics) / len(metrics)

    insight_lines = ["## Insights", ""]
    if status == "low_confidence":
        insight_lines += [
            f"_Early signal only: {min_per_persona} send(s) per segment, so use these as directional reads._",
            "",
        ]

    insights: list[str] = []
    actions: list[tuple[str, str]] = []

    # Insight 1: strongest segment
    if best_click["click_rate"] > 0:
        if best_open["persona"] == best_click["persona"]:
            insights.append(
                f"- **{persona_label(best_click['persona'])} is the clearest fit in this send.** "
                f"It led both opens and clicks, which suggests the subject line and message body were aligned for that segment."
            )
        else:
            insights.append(
                f"- **{persona_label(best_click['persona'])} converts best once it opens.** "
                f"{persona_label(best_open['persona'])} won the initial open, but {persona_label(best_click['persona'])} turned interest into stronger click intent."
            )
        actions.append((
            f"Use the {persona_label(best_click['persona'])} angle as the control for the next campaign",
            "it is the strongest proof of message-market fit in this send",
        ))

    # Insight 2: weakest segment by open or click behavior
    if worst_open["open_rate"] == 0 and best_open["open_rate"] > 0:
        insights.append(
            f"- **{persona_label(worst_open['persona'])} is dropping out at the top of the funnel.** "
            f"Other segments opened, but this segment did not, which points to a subject-line or positioning mismatch before the body copy is even seen."
        )
        actions.append((
            f"Test a sharper subject line for {persona_label(worst_open['persona'])}",
            "the current framing is not earning opens from that segment",
        ))
    elif worst_click["open_rate"] > 0 and click_gap >= 0.02:
        insights.append(
            f"- **{persona_label(worst_click['persona'])} is losing momentum after the open.** "
            f"The segment is entering the email but not taking the next step, which points to a CTA or payoff problem rather than a reach problem."
        )
        actions.append((
            f"Rewrite the CTA and body promise for {persona_label(worst_click['persona'])}",
            "interest is not converting into action for that segment",
        ))

    # Insight 3: overall campaign pattern
    if highest_unsub["unsubscribe_rate"] > 0.005:
        insights.append(
            f"- **{persona_label(highest_unsub['persona'])} shows the highest unsubscribe friction.** "
            f"The message may be over-promising or attracting the wrong expectation for that audience."
        )
        actions.append((
            f"Tighten the claim and expectation-setting for {persona_label(highest_unsub['persona'])}",
            "this segment shows the clearest risk of message-to-payoff mismatch",
        ))
    elif campaign_ctor < 0.25 and avg_open > avg_click:
        insights.append(
            "- **The campaign is generating more curiosity than follow-through.** "
            "Open behavior is healthier than click behavior, which suggests the email promise is stronger than the in-email payoff."
        )
        actions.append((
            "Make the next campaign CTA more specific and outcome-led across all segments",
            "the campaign is winning attention more easily than action",
        ))
    elif open_gap >= 0.15:
        insights.append(
            "- **Performance is spread unevenly across segments.** "
            "One segment is clearly resonating while another is being left behind, so the current framing is too narrow to travel evenly."
        )
        actions.append((
            "Keep the winning angle, but adapt the framing for the weakest segment in the next send",
            "the current theme works, but not equally across personas",
        ))

    if not insights:
        insights.append(
            "- **This campaign shows early engagement, but the differences between personas are still narrow.** "
            "Keep collecting data before making a large messaging change."
        )
        actions.append((
            "Run one more send with the same core angle and one controlled subject-line variation",
            "the signal is still too thin for a bigger strategic shift",
        ))

    # Ensure exactly three high-signal actions when possible.
    unique_actions: list[tuple[str, str]] = []
    for action, why in actions:
        if (action, why) not in unique_actions:
            unique_actions.append((action, why))

    fallback_actions = [
        (
            f"Publish a follow-up angle for {persona_label(best_click['persona'])}",
            "that segment showed the strongest evidence of message-market fit",
        ),
        (
            f"Test a different hook for {persona_label(worst_open['persona'])}",
            "the weakest segment needs a clearer reason to enter the email",
        ),
        (
            "Shorten the distance between the opening promise and the CTA",
            "the campaign should make the payoff feel clearer earlier in the email",
        ),
    ]
    for action in fallback_actions:
        if len(unique_actions) >= 3:
            break
        if action not in unique_actions:
            unique_actions.append(action)

    insight_lines += insights[:3]
    if unique_actions:
        insight_lines += ["", "---", "", "## Recommended Actions", ""]
        for action, why in unique_actions[:3]:
            insight_lines += [f"- {action} -- {why}"]

    return {"status": status, "text": "\n".join(insight_lines)}


# ── Main generator ─────────────────────────────────────────────────────────────

class ContentGenerator:
    MAX_JSON_ATTEMPTS = 3
    REQUEST_TIMEOUT_SECONDS = 8

    def __init__(self, api_key: str):
        import anthropic
        self.api_key       = api_key
        self.client        = anthropic.Anthropic(api_key=api_key)
        self.model         = "claude-sonnet-4-6"
        self.system_prompt = _load_system_prompt()

    # ------------------------------------------------------------------
    # Internal SDK wrapper
    # ------------------------------------------------------------------

    def _call_claude(self, prompt: str, temperature: float = 0.3,
                     max_tokens: int = 1500) -> str:
        """Single reusable wrapper. Strips markdown fences before returning."""
        kwargs: dict = dict(
            model       = self.model,
            max_tokens  = max_tokens,
            temperature = temperature,
            messages    = [{"role": "user", "content": prompt}],
        )
        if self.system_prompt:
            kwargs["system"] = self.system_prompt
        response = self.client.messages.create(**kwargs)
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$",           "", raw)
        return raw

    def _call_json(self, prompt: str, *, temperature: float, max_tokens: int,
                   validator=None):
        """
        Call Claude for JSON and retry with corrective feedback if parsing or
        validation fails.
        """
        retry_prompt = prompt
        last_error = None

        for attempt in range(1, self.MAX_JSON_ATTEMPTS + 1):
            raw = self._call_claude(
                retry_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            try:
                data = json.loads(raw)
                if validator is not None:
                    validator(data)
                return data
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                retry_prompt = (
                    f"{prompt}\n\n"
                    "Your previous response was invalid.\n"
                    f"Validation error: {exc}\n"
                    "Return corrected raw JSON only. Do not add commentary."
                )

        raise ValidationError(
            f"Model failed to return valid JSON after {self.MAX_JSON_ATTEMPTS} attempts: "
            f"{last_error}"
        )

    def _format_historical_context(self, historical_context: str | None) -> str:
        if not historical_context:
            return "No historical campaign data available yet."
        return historical_context

    def _verify_source(self, source: dict) -> dict | None:
        """
        Two-level verification:
        - URL must resolve (rejects hallucinated URLs) → always required
        - Page title plausibly matches proposed title → preferred, not required

        A source whose URL resolves but whose title doesn't match is accepted
        with verified_title=None, flagging that the URL is real but may point
        to a different article than Claude proposed.
        """
        url = source.get("url", "").strip()
        if not url:
            return None

        try:
            response = requests.get(
                url,
                timeout=self.REQUEST_TIMEOUT_SECONDS,
                headers={"User-Agent": "Mozilla/5.0 (compatible; NovaMindBot/1.0)"},
            )
        except (requests.ConnectionError, requests.Timeout):
            # Network-level failure — domain likely hallucinated
            return None
        except requests.RequestException:
            return None

        # Accept any HTTP response — even 403/404 means the domain is real.
        # Only network-level errors (above) indicate a fully hallucinated URL.
        final_url = response.url
        verified = dict(source)
        verified["url"] = final_url
        verified["domain"] = urlparse(final_url).netloc

        page_titles = _extract_verification_candidates(response.text)
        matched_title = next(
            (
                candidate for candidate in page_titles
                if _title_matches_source(source.get("title", ""), candidate)
            ),
            None,
        ) if page_titles else None
        verified["verified_title"] = matched_title  # None = domain real, title unconfirmed
        return verified

    def verify_sources(self, candidates: list[dict]) -> list[dict]:
        verified = []
        for candidate in candidates:
            source = self._verify_source(candidate)
            if source:
                verified.append(source)

        if len(verified) < 2:
            raise ValidationError(
                "Could not verify enough sources from live URLs. "
                "Try a broader topic or check connectivity."
            )
        return verified

    def _sources_verified_count(self, sources: list[dict]) -> tuple[int, int]:
        """Return (title_matched, url_only) counts for display."""
        title_matched = sum(1 for s in sources if s.get("verified_title"))
        url_only      = len(sources) - title_matched
        return title_matched, url_only

    # ------------------------------------------------------------------
    # Step 1 — Research
    # ------------------------------------------------------------------

    def research_topic(self, topic: str) -> list[dict]:
        """
        Identify 4-6 source candidates from Claude's training knowledge.
        Returns [{ title, url, publisher, published_date, key_takeaways }, ...]
        """
        prompt = f"""You are a research assistant for NovaMind, an AI startup that helps
small creative agencies automate their daily workflows.

Identify 4 to 6 source candidates related to this topic: "{topic}"
Only include sources you are genuinely confident exist.

Return a JSON array where each element has exactly:
- "title"         : exact article or report title
- "url"           : full URL if known, omit if uncertain
- "publisher"     : publication or organisation name
- "published_date": year or year-month (e.g. "2024-03"), or "unknown"
- "key_takeaways" : array of 2-3 concise strings summarising the source's main points

Rules: do NOT invent statistics, quotes, URLs, or author names.
Respond with raw JSON array only."""

        candidates = self._call_json(
            prompt,
            temperature=0.2,
            max_tokens=1200,
            validator=_validate_sources,
        )
        return self.verify_sources(candidates)

    # ------------------------------------------------------------------
    # Step 2 — Structured outline (control layer)
    # ------------------------------------------------------------------

    def generate_outline(self, topic: str,
                         historical_context: str | None = None) -> list[dict]:
        """
        Generate the structured outline that drives both blog and newsletter generation.

        Returns:
          [
            {
              "title"             : str,
              "goal"              : one of VALID_GOALS,
              "persona_relevance" : [subset of NEWSLETTER_PERSONAS]
            },
            ...  (5-7 items)
          ]

        Raises ValidationError if output fails quality gates.
        """
        prompt = f"""You are a content strategist for NovaMind, an AI startup that helps
small creative agencies automate their workflows.

Create a structured blog outline for the topic: "{topic}"

Recent campaign learnings:
{self._format_historical_context(historical_context)}

Return a JSON array of 5 to 7 section objects. Each object must have exactly:
- "title"             : clear section heading (no em dashes)
- "goal"              : exactly one of: hook, pain_point, context, proof, solution, how_to, cta
- "persona_relevance" : array containing one or more of:
                        "Marketing Manager", "Creative Professional", "Agency Founder"

Rules:
- Logical narrative flow: hook must be first, cta must be last
- Include at least one "proof" section with concrete data or examples
- Include exactly one "cta" section (final section)
- Every persona must appear in at least two sections
- No em dashes in titles
- If historical learnings are provided, use them to improve framing
- Respond with raw JSON array only — no explanation"""

        return self._call_json(
            prompt,
            temperature=0.3,
            max_tokens=800,
            validator=_validate_outline,
        )

    # ------------------------------------------------------------------
    # Step 3 — Section-by-section blog generation
    # ------------------------------------------------------------------

    def generate_blog(self, topic: str, outline: list[dict]) -> dict:
        """
        Generate a blog post section-by-section, each aligned to its outline goal.

        Returns:
          {
            "title"   : str,
            "outline" : [structured outline],
            "sections": [{ title, goal, content }, ...],
            "draft"   : str  (sections assembled, 400-600 words, validated)
          }
        """
        outline_block = "\n".join(
            f"{i+1}. [{item['goal'].upper()}] {item['title']}  "
            f"(personas: {', '.join(item['persona_relevance'])})"
            for i, item in enumerate(outline)
        )

        prompt = f"""You are NovaMind's Content & Growth Analyst.

Topic: "{topic}"

STRUCTURED OUTLINE (write each section aligned with its goal):
{outline_block}

Goal definitions:
  hook       — bold 1-2 sentence opener that establishes the core tension; renders before any section header so keep it punchy and under 40 words total
  pain_point — establish the problem with concrete detail
  context    — background, framing, industry landscape
  proof      — concrete data, examples, or industry context that supports the argument
  solution   — describe NovaMind's approach clearly
  how_to     — practical, actionable steps
  cta        — forward-looking close: state the opportunity, the cost of inaction, or the next step. End on momentum.

Return a JSON object with exactly these keys:
- "title"    : SEO-friendly blog title, 60 characters or fewer, no em dashes
- "sections" : array with one object per outline section, in order:
               [{{"title": "...", "goal": "...", "content": "60-90 words aligned with goal"}}]
- "draft"    : all section contents joined by double newlines (must total 400-600 words)

Writing rules per section:
- Break content into short paragraphs of 2-3 sentences max — no dense text blocks
- For "proof" and "how_to" goals: use markdown bullet points (- item) to list data points or steps
- Bold the single most important claim in each section using **bold text**
- No em dashes anywhere
- Be specific and credible — use concrete examples and practical detail
- For "hook" sections: bold the first sentence using **bold**, follow with one short supporting sentence; no heading will be rendered above it
- HARD RULE: Never start any paragraph or sentence with a company name, report name,
  or phrases like "According to", "Research shows", "Studies find", "McKinsey found", "HBR says"
- HARD RULE: State the insight or claim first — support it with examples or observed patterns, not generic filler
- For "cta" goal: write a forward-looking close with a clear action
- draft must equal sections[0].content + "\\n\\n" + sections[1].content + ... (assembled exactly)
- Respond with raw JSON only"""

        def _validator(d: dict) -> None:
            d["title"] = _canonical_blog_title(topic, d.get("title", ""))
            d["outline"] = outline
            _validate_blog(d)

        data = self._call_json(
            prompt,
            temperature=0.4,
            max_tokens=2000,
            validator=_validator,
        )
        data["title"] = _canonical_blog_title(topic, data.get("title", ""))
        return data

    # ------------------------------------------------------------------
    # Step 4 — Persona-filtered newsletter generation
    # ------------------------------------------------------------------

    def generate_newsletters(self, blog: dict,
                             historical_context: str | None = None) -> dict:
        """
        Generate three persona-specific newsletters.

        Each newsletter draws ONLY from blog sections where that persona
        appears in the outline's persona_relevance — not from the full blog.

        Returns:
          {
            "newsletters": [
              { "persona": "Marketing Manager",    "subject": str, "body": str },
              { "persona": "Creative Professional","subject": str, "body": str },
              { "persona": "Agency Founder",       "subject": str, "body": str }
            ]
          }
        """
        # Build persona → relevant sections mapping
        persona_sections: dict[str, list[str]] = {}
        for persona in NEWSLETTER_PERSONAS:
            relevant = [
                f"[{s['goal'].upper()}] {s['title']}: {s['content']}"
                for s, o in zip(blog["sections"], blog["outline"])
                if persona in o["persona_relevance"]
            ]
            persona_sections[persona] = relevant

        persona_blocks = "\n\n".join(
            f"--- {persona} (select sections only) ---\n" + "\n\n".join(sections)
            for persona, sections in persona_sections.items()
        )

        prompt = f"""You are NovaMind's email copywriter.

BLOG TITLE: {blog['title']}

Each persona below has been assigned specific blog sections based on relevance.
Write one newsletter per persona using ONLY the sections assigned to them.

CRITICAL RULE: Do not introduce new facts. Do not reference sections not shown
for that persona. Only reframe the content already provided.

{persona_blocks}

Recent campaign learnings:
{self._format_historical_context(historical_context)}

Persona focus areas:
- Marketing Manager:     ROI, performance metrics, efficiency gains
- Creative Professional: tools, workflow improvement, reclaiming creative time
- Agency Founder:        growth, leverage, scaling without adding headcount

Each newsletter must:
- Be 120 to 180 words (body only)
- Open with a strong hook specific to that persona's assigned content
- End with exactly: [READ MORE]
- Contain no em dashes
- Have a subject line of 60 characters or fewer
- If historical learnings are provided, use them only to adjust framing, not to add facts

Return this exact JSON:
{{
  "newsletters": [
    {{"persona": "Marketing Manager",    "subject": "...", "body": "..."}},
    {{"persona": "Creative Professional","subject": "...", "body": "..."}},
    {{"persona": "Agency Founder",       "subject": "...", "body": "..."}}
  ]
}}

Respond with raw JSON only."""

        return self._call_json(
            prompt,
            temperature=0.4,
            max_tokens=2000,
            validator=_validate_newsletters,
        )

    # ------------------------------------------------------------------
    # Top-level pipeline
    # ------------------------------------------------------------------

    def generate_content(self, topic: str, historical_context: str | None = None) -> dict:
        """
        Three-step pipeline: outline → blog → newsletters.

        Returns:
          {
            "outline"    : [{ title, goal, persona_relevance }, ...],
            "blog"       : { title, outline, sections, draft },
            "newsletters": [{ persona, subject, body }, ...]
          }
        """
        outline     = self.generate_outline(topic, historical_context=historical_context)
        blog        = self.generate_blog(topic, outline)
        newsletters = self.generate_newsletters(
            blog,
            historical_context=historical_context,
        )
        return {
            "outline"    : outline,
            "blog"       : blog,
            "newsletters": newsletters["newsletters"],
        }

    def generate_blog_post(self, topic: str, historical_context: str | None = None) -> dict:
        """Backwards-compat wrapper — returns the generated blog dict."""
        content = self.generate_content(topic, historical_context=historical_context)
        return content["blog"]

    # ------------------------------------------------------------------
    # Performance analysis
    # ------------------------------------------------------------------

    def generate_performance_summary(self, metrics: list[dict], blog_title: str) -> dict:
        return _build_performance_summary(metrics, persona_label)

    def generate_dashboard_insights(self, dashboard_data: dict) -> str:
        """
        Generate cross-campaign insights using Claude.
        Returns two markdown sections:
          - ## Key Signals: up to 3 bullets, each as observation + 'Why it matters:' implication
          - ## ⚡ Recommended Actions: exactly 3 actionable steps tied to specific metric gaps
        Returns an empty string if per_persona data is absent.
        """
        per_persona  = dashboard_data.get("per_persona", {})
        per_campaign = dashboard_data.get("per_campaign", [])

        if not per_persona:
            return ""

        persona_lines = "\n".join(
            f"- {persona_label(p)}: {d['count']} campaign(s), avg open {d['avg_open']:.1%}, "
            f"avg click {d['avg_click']:.1%}, avg unsub {d['avg_unsub']:.2%}, trend: {d['trend']}"
            for p, d in sorted(per_persona.items())
        )
        campaign_lines = "\n".join(
            f"- {c['blog_title']} (topic: {c['topic']}): avg open {c['avg_open']:.1%}, "
            f"avg click {c['avg_click']:.1%}"
            for c in per_campaign[:5]
        ) or "No campaign data."

        prompt = f"""You are the growth analyst for NovaMind.

Persona performance (all campaigns):
{persona_lines}

Top campaigns by engagement:
{campaign_lines}

Write exactly the two sections below. No other sections, no preamble.

## Key Signals
Always include. Write a maximum of 3 bullets.
- Each bullet must combine multiple signals into one pattern
- Each bullet must include:
  1. Observation: the pattern across personas or metrics
  2. Implication: what the pattern means
- Format each bullet exactly like:
  "- [Insight]"
  "  Why it matters: [Implication]"
- Do not list metrics or trends individually
- Do not repeat the same persona across multiple bullets
- Keep every insight unique
- Do not restate raw statistics unless necessary
- Avoid generic statements
- Prioritize improvement needs before broad thematic observations
- If any persona has a down trend, at least one Key Signal must explicitly address that persona
- Include at most one non-persona or overall-theme insight, and only after persona issues are covered

## ⚡ Recommended Actions
Always include. Exactly 3 actionable steps.
- Each tied to a specific pattern from the data above
- Format: "- [what to do next] -- [why]"
- Actions must be specific and immediately executable (rewrite X, test Y, publish Z)
- Do not repeat personas across the first two actions
- If a persona has a down trend, one action must directly respond to that persona's decline

Hard rules:
- No em dashes (use -- instead)
- No headers other than the two above
- Max 200 words total"""

        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()


# ── Mock generator (no API key required) ──────────────────────────────────────

class MockContentGenerator:
    """
    Hardcoded content that mirrors ContentGenerator's exact return shapes.
    All mock content passes the same validation rules as the real generator.
    """

    def research_topic(self, topic: str) -> list[dict]:
        return [
            {
                "title"         : "The State of AI Adoption in Creative Industries 2024",
                "url"           : "https://www.mckinsey.com/capabilities/mckinsey-digital/our-insights",
                "publisher"     : "McKinsey & Company",
                "published_date": "2024-02",
                "key_takeaways" : [
                    "61% of creative agencies report using AI tools in at least one workflow",
                    "Agencies using automation report 35% faster project turnaround",
                    "The biggest barrier to adoption is integration complexity, not cost",
                ],
            },
            {
                "title"         : "How Small Agencies Are Competing With AI-Assisted Workflows",
                "url"           : "https://hbr.org/2024/01/how-small-agencies-ai-workflows",
                "publisher"     : "Harvard Business Review",
                "published_date": "2024-01",
                "key_takeaways" : [
                    "Boutique agencies using AI tools are winning pitches against larger firms",
                    "Automation frees 6-10 hours per team member per week on average",
                    "Client satisfaction scores rise when teams focus on strategy over admin",
                ],
            },
            {
                "title"         : "Zapier's Annual Automation Report",
                "url"           : "https://zapier.com/blog/automation-report",
                "publisher"     : "Zapier",
                "published_date": "2024-03",
                "key_takeaways" : [
                    "76% of knowledge workers now use some form of workflow automation",
                    "Most impactful automations: status updates, file routing, and reporting",
                    "Teams that automate routine tasks report higher job satisfaction",
                ],
            },
            {
                "title"         : "AI Tools Benchmark for Creative Teams",
                "url"           : "https://www.figma.com/blog/ai-tools-creative-teams",
                "publisher"     : "Figma",
                "published_date": "2023-11",
                "key_takeaways" : [
                    "Design teams using AI for asset tagging save 3-4 hours per week",
                    "AI-assisted briefing reduces revision rounds by an average of 1.8 cycles",
                    "Adoption is highest in studios with 5-15 people",
                ],
            },
        ]

    def generate_outline(self, topic: str,
                         historical_context: str | None = None) -> list[dict]:
        return [
            {
                "title"             : "The Hidden Cost of Coordination",
                "goal"              : "hook",
                "persona_relevance" : ["Marketing Manager", "Creative Professional", "Agency Founder"],
            },
            {
                "title"             : "The Admin Tax Every Small Agency Pays",
                "goal"              : "pain_point",
                "persona_relevance" : ["Marketing Manager", "Creative Professional", "Agency Founder"],
            },
            {
                "title"             : "What AI Automation Actually Looks Like in Practice",
                "goal"              : "context",
                "persona_relevance" : ["Marketing Manager", "Creative Professional", "Agency Founder"],
            },
            {
                "title"             : "Real Results: What the Research Shows",
                "goal"              : "proof",
                "persona_relevance" : ["Marketing Manager", "Agency Founder"],
            },
            {
                "title"             : "The NovaMind Approach: Orchestrate, Don't Replace",
                "goal"              : "solution",
                "persona_relevance" : ["Agency Founder", "Creative Professional"],
            },
            {
                "title"             : "Getting Started Without a Technical Team",
                "goal"              : "how_to",
                "persona_relevance" : ["Creative Professional", "Agency Founder"],
            },
            {
                "title"             : "What to Measure in the First 90 Days",
                "goal"              : "cta",
                "persona_relevance" : ["Marketing Manager", "Agency Founder"],
            },
        ]

    def generate_blog(self, topic: str, outline: list[dict]) -> dict:
        sections = [
            {
                "title"  : "The Hidden Cost of Coordination",
                "goal"   : "hook",
                "content": (
                    "**Most small creative agencies are not limited by talent. "
                    "They are limited by how much work happens between the work.**\n\n"
                    "That gap shows up in status updates, asset handoffs, and "
                    "coordination overhead that quietly eats into billable time."
                ),
            },
            {
                "title"  : "The Admin Tax Every Small Agency Pays",
                "goal"   : "pain_point",
                "content": (
                    "The bottleneck at most small creative agencies is not talent: it is "
                    "coordination overhead. Every project status email, every manual asset "
                    "handoff, every client update drafted from scratch is time that should go "
                    "into billable work. **Agencies that automate this layer finish projects "
                    "35% faster** (McKinsey, 2024). Most founders do not realize how much "
                    "capacity disappears into this invisible administrative tax until they actually measure it."
                ),
            },
            {
                "title"  : "What AI Automation Actually Looks Like in Practice",
                "goal"   : "context",
                "content": (
                    "Automation for creative teams is not about replacing designers or strategists. "
                    "It is about removing the repetitive layer that surrounds every deliverable. "
                    "**The highest-impact automations are status updates, file routing, and project "
                    "reporting** (Zapier, 2024): tasks that consume hours weekly yet require no "
                    "creative judgement. Most teams still do all of this by hand, draining hours "
                    "that should go toward billable work."
                ),
            },
            {
                "title"  : "Real Results: What the Research Shows",
                "goal"   : "proof",
                "content": (
                    "The results across agencies that have made this shift are consistent:\n\n"
                    "- **35% faster project turnaround** at agencies using workflow automation "
                    "(McKinsey, 2024)\n"
                    "- **3 to 4 hours saved weekly** on asset tagging alone for design teams "
                    "(Figma)\n"
                    "- Boutique agencies winning pitches against firms twice their size through "
                    "AI-assisted workflows\n\n"
                    "The competitive advantage is not budget. It is how efficiently a team "
                    "operates between deliverables."
                ),
            },
            {
                "title"  : "The NovaMind Approach: Orchestrate, Don't Replace",
                "goal"   : "solution",
                "content": (
                    "**NovaMind is built for the agency that is too big to wing operations and "
                    "too small to hire a dedicated ops team.** The approach is not to replace "
                    "creative work but to remove the friction surrounding it.\n\n"
                    "NovaMind connects your existing tools so information flows automatically. "
                    "A client approval triggers updates in project tracking, timelines, and team "
                    "notifications without anyone touching it manually."
                ),
            },
            {
                "title"  : "Getting Started Without a Technical Team",
                "goal"   : "how_to",
                "content": (
                    "Getting started does not require a technical hire or a lengthy setup "
                    "project. Here is how most teams begin:\n\n"
                    "- Connect NovaMind to the tools you already use\n"
                    "- **Start with one high-friction task** such as project status updates "
                    "or asset handoffs\n"
                    "- Most teams have their first automated workflow running within 48 hours\n\n"
                    "Studios with 5 to 15 people see the fastest results: every hour reclaimed "
                    "shows immediately in daily output."
                ),
            },
            {
                "title"  : "What to Measure in the First 90 Days",
                "goal"   : "cta",
                "content": (
                    "**The agencies building a competitive advantage right now are the ones "
                    "acting before their competitors do.** In the first 90 days, track time "
                    "saved on status updates, revision cycles, and project reporting. Those "
                    "numbers make the ROI case to leadership and clients.\n\n"
                    "Every project you run with automation in place compounds the advantage. "
                    "The question is not whether this shift is coming to your market. "
                    "It is whether you lead it."
                ),
            },
        ]
        draft = "\n\n".join(s["content"] for s in sections)
        return {
            "title"   : _canonical_blog_title(topic),
            "outline" : outline,
            "sections": sections,
            "draft"   : draft,
        }

    def generate_newsletters(self, blog: dict,
                             historical_context: str | None = None) -> dict:
        # Marketing Manager: pain_point(all), context(all), proof(MM+AF), cta(MM+AF)
        # Creative Professional: pain_point(all), context(all), solution(AF+CP), how_to(CP+AF)
        # Agency Founder: all sections
        return {
            "newsletters": [
                {
                    "persona": "Marketing Manager",
                    "subject": "35% faster delivery: the AI automation case",
                    "body"   : (
                        "Your agency's biggest efficiency drain is not a capability gap, "
                        "and it is rarely a talent problem. "
                        "It is coordination overhead that should be automated.\n\n"
                        "Teams that eliminate manual status updates, asset handoffs, and "
                        "project reporting reclaim 6 to 10 hours per person per week (HBR). "
                        "That is not a marginal improvement: it is a full working day "
                        "returned to billable output every week.\n\n"
                        "Boutique agencies are winning pitches against firms twice their size "
                        "by operating more efficiently, not by hiring more people.\n\n"
                        "In the first 90 days after implementing NovaMind, track time saved "
                        "on status updates, revision cycles, and project reporting. Those "
                        "numbers make the ROI case concrete for leadership and clients alike.\n\n"
                        "Read the full breakdown: [READ MORE]"
                    ),
                },
                {
                    "persona": "Creative Professional",
                    "subject": "Get 4 hours back every week: here's how",
                    "body"   : (
                        "You did not get into creative work to spend your afternoons on "
                        "project status emails and asset handoffs. Those tasks consume "
                        "hours every week without requiring a single creative decision.\n\n"
                        "Status updates, file routing, and project reporting are the "
                        "highest-impact automations for creative teams: work that can be "
                        "eliminated entirely with the right setup (Zapier, 2024).\n\n"
                        "NovaMind connects the tools you already use and routes information "
                        "automatically between them. A file approved in one tool updates "
                        "your project tracker without you touching it.\n\n"
                        "Most NovaMind users have their first workflow running within "
                        "48 hours. No technical setup required, no new tools to learn.\n\n"
                        "Start by automating the one task that disrupts your creative flow "
                        "most. That single change usually shows what the rest can become.\n\n"
                        "See how it works for creative professionals: [READ MORE]"
                    ),
                },
                {
                    "persona": "Agency Founder",
                    "subject": "Winning bigger pitches without growing headcount",
                    "body"   : (
                        "Scaling an agency without growing headcount requires that "
                        "coordination overhead stays flat as client count grows. Most "
                        "agencies hit a ceiling not because they lack talent but because "
                        "their operations do not scale.\n\n"
                        "**Agencies using workflow automation report 35% faster project "
                        "turnaround** (McKinsey, 2024). Boutique agencies are winning "
                        "pitches against firms twice their size by freeing their teams "
                        "from administrative work.\n\n"
                        "NovaMind connects your existing tools so information flows "
                        "automatically across every active project. Client approvals "
                        "trigger tracking updates. Status reports generate without "
                        "manual input.\n\n"
                        "The studios that act on this now are building a structural "
                        "advantage that compounds with every project and every quarter. "
                        "Your competitors are not waiting.\n\n"
                        "Read the full breakdown on scaling a leaner creative agency: "
                        "[READ MORE]"
                    ),
                },
            ]
        }

    def generate_content(self, topic: str, historical_context: str | None = None) -> dict:
        outline     = self.generate_outline(topic, historical_context=historical_context)
        blog        = self.generate_blog(topic, outline)
        newsletters = self.generate_newsletters(
            blog,
            historical_context=historical_context,
        )
        return {
            "outline"    : outline,
            "blog"       : blog,
            "newsletters": newsletters["newsletters"],
        }

    def generate_blog_post(self, topic: str, historical_context: str | None = None) -> dict:
        content = self.generate_content(topic, historical_context=historical_context)
        return content["blog"]

    def generate_performance_summary(self, metrics: list[dict], blog_title: str) -> dict:
        return _build_performance_summary(metrics, persona_label)

    def generate_dashboard_insights(self, dashboard_data: dict) -> str:
        """
        Heuristic fallback -- no Claude API call.
        Produces two sections: Key Signals and What to Do Next.
        """
        per_persona  = dashboard_data.get("per_persona", {})
        per_campaign = dashboard_data.get("per_campaign", [])

        if not per_persona:
            return ""

        _persona_topics = {
            "creative_professional": [
                "How to Automate the Repetitive Parts of Client Projects",
                "AI Tools That Actually Fit Into a Creative Workflow",
            ],
            "marketing_manager": [
                "Proving Campaign ROI with AI-Assisted Reporting",
                "How to Cut Campaign Build Time with Workflow Automation",
            ],
            "agency_founder": [
                "Building Agency Systems That Scale Without Hiring",
                "How Founders Are Using AI to Win and Deliver More Work",
            ],
        }
        _trending = [
            "AI Agents for Client Reporting: What's Actually Ready to Use",
            "No-Code AI Workflows That Save 10 or More Hours a Week",
        ]

        sorted_by_click          = sorted(per_persona.items(), key=lambda x: x[1]["avg_click"], reverse=True)
        best_p,  best_d          = sorted_by_click[0]
        worst_p, worst_d         = sorted_by_click[-1]
        sorted_by_open           = sorted(per_persona.items(), key=lambda x: x[1]["avg_open"])
        lowest_open_p, lowest_open_d = sorted_by_open[0]
        sorted_by_unsub          = sorted(per_persona.items(), key=lambda x: x[1]["avg_unsub"], reverse=True)
        highest_unsub_p, highest_unsub_d = sorted_by_unsub[0]
        down_trend_personas      = [
            (p, d) for p, d in per_persona.items() if d.get("trend") == "down"
        ]
        click_gap = best_d["avg_click"] - worst_d["avg_click"]
        open_gap  = best_d["avg_open"]  - lowest_open_d["avg_open"]

        # ── Key Signals ───────────────────────────────────────────────────────
        signals: list[str] = []
        used_personas: set[str] = set()

        def add_signal(persona: str, observation: str, implication: str) -> None:
            if persona in used_personas or len(signals) >= 3:
                return
            signals.append(f"- {observation}\n  Why it matters: {implication}")
            used_personas.add(persona)

        for persona, pdata in sorted(
            down_trend_personas,
            key=lambda item: item[1]["avg_click"],
            reverse=True,
        ):
            add_signal(
                persona,
                f"{persona_label(persona)} is trending down despite still showing measurable engagement",
                f"That segment is not lost, but the current content mix is weakening and needs a sharper next angle before the decline compounds",
            )

        add_signal(
            best_p,
            f"{persona_label(best_p)} combines the strongest opens and clicks, which points to a content angle that is working from subject line through CTA",
            f"That segment has the clearest content-market fit and is the best source of repeatable content themes",
        )

        if click_gap >= 0.03:
            add_signal(
                worst_p,
                f"{persona_label(worst_p)} is not carrying interest through to action, even when stronger personas respond to the same campaign set",
                f"The current content promise is not translating into relevance once this audience starts reading",
            )

        if lowest_open_p != worst_p and open_gap >= 0.03:
            add_signal(
                lowest_open_p,
                f"{persona_label(lowest_open_p)} is hardest to pull into the funnel, which suggests the current framing is missing the first thing that audience cares about",
                f"The top-of-funnel angle is likely off, so this segment is under-entering the journey before content quality even matters",
            )

        if highest_unsub_p not in used_personas and highest_unsub_d["avg_unsub"] > 0.004:
            add_signal(
                highest_unsub_p,
                f"{persona_label(highest_unsub_p)} shows the most friction between engagement and retention, which signals a gap between promise and payoff",
                f"The audience is interested enough to engage, but not convinced enough to stay bought into the message",
            )

        output: list[str] = ["## Key Signals", ""]
        output += signals[:3]
        output += ["", "---", ""]

        # ── Recommended Actions (always exactly 3 actions) ────────────────────
        output += ["## ⚡ Recommended Actions", ""]

        used_action_personas: set[str] = set()
        action_lines: list[str] = []

        for persona, _pdata in sorted(
            down_trend_personas,
            key=lambda item: item[1]["avg_click"],
            reverse=True,
        ):
            action_lines.append(
                f"- Publish a fresh angle for {persona_label(persona)} built around its strongest recent topic pattern "
                f"-- that persona is trending down and needs a content reset before performance softens further"
            )
            used_action_personas.add(persona)
            if len(used_action_personas) == 1:
                break

        # Action 1 -- CTA fix for worst-click persona
        if worst_p not in used_action_personas:
            action_lines.append(
                f"- Rewrite {persona_label(worst_p)} CTA to focus on that segment's primary outcome "
                f"-- interest is not turning into action for this audience"
            )
            used_action_personas.add(worst_p)

        # Action 2 -- Subject line or body fix (different persona from action 1)
        if len(action_lines) < 3:
            if lowest_open_p != worst_p and lowest_open_p not in used_action_personas:
                action_lines.append(
                    f"- Test outcome-led vs question-led subject lines for {persona_label(lowest_open_p)} "
                    f"-- this segment is under-entering the funnel and needs sharper top-of-funnel framing"
                )
                used_action_personas.add(lowest_open_p)
            elif lowest_open_p not in used_action_personas:
                action_lines.append(
                    f"- Revise {persona_label(lowest_open_p)} body content to match subject line promise "
                    f"-- the message is creating curiosity but not enough downstream relevance"
                )
                used_action_personas.add(lowest_open_p)

        # Action 3 -- Content opportunity tied to best-performing persona
        if len(action_lines) < 3:
            best_topics = _persona_topics.get(best_p, _trending)
            action_lines.append(
                f"- Publish '{best_topics[0]}' targeting {persona_label(best_p)} "
                f"-- this is the strongest current source of repeatable engagement"
            )

        output += action_lines[:3]

        return "\n".join(output)
