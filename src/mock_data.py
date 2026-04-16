"""
Mock contact data for NovaMind's three audience personas.
Slugs and labels match the shared persona definitions in src/personas.py.
In production these would come from your real CRM or lead database.
"""

PERSONAS = {
    "agency_founder": {
        "label": "Agency Founder",
        "description": "Founders and principals of small creative agencies (5-20 people)",
        "tone": "growth-focused, decisive, scaling-minded",
        "pain_points": "coordination overhead, scaling without hiring, winning bigger clients",
        "cta": "See how NovaMind gives your agency the leverage of a larger operation",
    },
    "creative_professional": {
        "label": "Creative Professional",
        "description": "In-house and freelance designers, videographers, and content creators",
        "tone": "practical, tool-curious, protective of creative time",
        "pain_points": "admin overhead, context-switching, repetitive non-creative tasks",
        "cta": "Automate the admin. Keep doing the work you actually care about",
    },
    "marketing_manager": {
        "label": "Marketing Manager",
        "description": "In-house marketers at small-to-medium businesses",
        "tone": "data-driven, channel-focused, ROI-conscious",
        "pain_points": "proving ROI, managing multiple channels, limited budget and team",
        "cta": "Get measurable results from AI-assisted content without the agency price tag",
    },
}

MOCK_CONTACTS = [
    {
        "email": "greshashah2601@gmail.com",
        "firstName": "Gresha",
        "lastName": "Shah",
        "persona": "agency_founder",
    },
    {
        "email": "hanschemicalspl@gmail.com",
        "firstName": "Hans",
        "lastName": "Chemicals",
        "persona": "creative_professional",
    },
    {
        "email": "dcbaa1001@gmail.com",
        "firstName": "Dev",
        "lastName": "Shah",
        "persona": "marketing_manager",
    },
]

# Realistic email benchmark ranges per persona (used for simulation)
PERFORMANCE_BENCHMARKS = {
    "agency_founder": {
        "open_rate": (0.28, 0.36),
        "click_rate": (0.04, 0.07),
        "unsubscribe_rate": (0.003, 0.006),
    },
    "creative_professional": {
        "open_rate": (0.33, 0.44),
        "click_rate": (0.06, 0.10),
        "unsubscribe_rate": (0.002, 0.004),
    },
    "marketing_manager": {
        "open_rate": (0.24, 0.31),
        "click_rate": (0.03, 0.055),
        "unsubscribe_rate": (0.004, 0.007),
    },
}
