"""
Shared persona constants and conversion helpers.
"""

CANONICAL_PERSONA_LABELS = {
    "agency_founder": "Agency Founder",
    "creative_professional": "Creative Professional",
    "marketing_manager": "Marketing Manager",
}

LEGACY_PERSONA_ALIASES = {
    "creative_agency_owner": "agency_founder",
    "freelance_creator": "creative_professional",
}

DISPLAY_TO_SLUG = {label: slug for slug, label in CANONICAL_PERSONA_LABELS.items()}
NEWSLETTER_PERSONAS = list(DISPLAY_TO_SLUG.keys())
PERSONA_DISPLAY_NAMES = NEWSLETTER_PERSONAS[:]


def canonical_persona_slug(slug: str) -> str:
    return LEGACY_PERSONA_ALIASES.get(slug, slug)


def persona_label(slug: str) -> str:
    canonical = canonical_persona_slug(slug)
    return CANONICAL_PERSONA_LABELS.get(canonical, canonical.replace("_", " ").title())
