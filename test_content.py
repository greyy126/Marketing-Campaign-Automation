"""
Direct test of content_generator.py — no CRM, no Brevo, no DB.
Run: python test_content.py
"""

import json
import sys
from rich.console import Console
from rich.rule import Rule
from rich.panel import Panel
from rich import box
from rich.table import Table

sys.path.insert(0, ".")
from src.content_generator import (
    MockContentGenerator,
    _validate_outline, _validate_blog, _validate_newsletters,
    ValidationError, NEWSLETTER_PERSONAS, VALID_GOALS,
)

console = Console()
TOPIC  = "AI in creative automation"
PASSED = 0
FAILED = 0


def ok(label: str) -> None:
    global PASSED; PASSED += 1
    console.print(f"  [bold green]PASS[/] {label}")


def fail(label: str, reason="") -> None:
    global FAILED; FAILED += 1
    console.print(f"  [bold red]FAIL[/] {label}" + (f" — {reason}" if reason else ""))


def check(cond: bool, label: str, reason="") -> None:
    ok(label) if cond else fail(label, reason)


gen = MockContentGenerator()

# ══════════════════════════════════════════════════════════════════════════════
# 1. generate_outline
# ══════════════════════════════════════════════════════════════════════════════
console.print(Rule("[bold cyan]1 · generate_outline()[/]"))
outline = gen.generate_outline(TOPIC)

check(isinstance(outline, list),       "returns a list")
check(5 <= len(outline) <= 7,          f"5-7 sections (got {len(outline)})")

for key in ("title", "goal", "persona_relevance"):
    check(all(key in item for item in outline), f"all sections have key '{key}'")

check(all(item["goal"] in VALID_GOALS for item in outline),
      f"all goals valid (valid={sorted(VALID_GOALS)})")

check(all(isinstance(item["persona_relevance"], list) and item["persona_relevance"]
          for item in outline),
      "persona_relevance is non-empty list in every section")

check(any(item["goal"] == "proof" for item in outline),         "has at least one 'proof' section")
check(outline[-1]["goal"] == "cta",                              "last section goal is 'cta'")
check(
    all(item["goal"] not in ("hook", "pain_point") or outline.index(item) < 2
        for item in outline),
    "hook/pain_point sections appear early (index 0-1)",
)

# Every persona appears in at least two sections
for persona in NEWSLETTER_PERSONAS:
    count = sum(1 for item in outline if persona in item["persona_relevance"])
    check(count >= 2, f"{persona} appears in >= 2 sections (got {count})")

# Outline table
ot = Table(box=box.SIMPLE_HEAD, header_style="bold magenta")
ot.add_column("#",        width=3)
ot.add_column("Goal",     style="yellow",  min_width=12)
ot.add_column("Title",    style="cyan",    min_width=38)
ot.add_column("Personas", min_width=20)
for i, item in enumerate(outline):
    ot.add_row(str(i+1), item["goal"], item["title"], ", ".join(item["persona_relevance"]))
console.print(ot)

# ══════════════════════════════════════════════════════════════════════════════
# 2. generate_blog
# ══════════════════════════════════════════════════════════════════════════════
console.print(Rule("[bold cyan]2 · generate_blog()[/]"))
blog = gen.generate_blog(TOPIC, outline)

for key in ("title", "outline", "sections", "draft"):
    check(key in blog, f"has key '{key}'")

check(len(blog["title"]) <= 60,  f"title <= 60 chars (got {len(blog['title'])})")
check(blog["outline"] is outline, "blog['outline'] is the passed-in outline object")
check(len(blog["sections"]) == len(outline),
      f"sections count matches outline ({len(blog['sections'])} == {len(outline)})")

for i, section in enumerate(blog["sections"]):
    for key in ("title", "goal", "content"):
        check(key in section, f"section[{i}] has key '{key}'")

wc = len(blog["draft"].split())
check(400 <= wc <= 600, f"draft word count 400-600 (got {wc})")
check("\u2014" not in blog["draft"] and "—" not in blog["draft"], "no em dashes in draft")

# Draft equals assembled sections
assembled = "\n\n".join(s["content"] for s in blog["sections"])
check(blog["draft"] == assembled, "draft == assembled section contents")

console.print(Panel(
    f"[bold]{blog['title']}[/]\n\n"
    + "\n".join(f"  [{s['goal']}] {s['title']}" for s in blog["sections"])
    + f"\n\n[dim]{blog['draft'][:250]}...[/]",
    title="Blog Preview", border_style="cyan", padding=(1, 2),
))

# ══════════════════════════════════════════════════════════════════════════════
# 3. generate_newsletters
# ══════════════════════════════════════════════════════════════════════════════
console.print(Rule("[bold cyan]3 · generate_newsletters()[/]"))
nl_result = gen.generate_newsletters(blog)

check("newsletters" in nl_result,          "result has 'newsletters' key")
newsletters = nl_result["newsletters"]
check(isinstance(newsletters, list),        "newsletters is a list")
check(len(newsletters) == 3,                "exactly 3 newsletters")

found_personas = {nl["persona"] for nl in newsletters}
check(found_personas == set(NEWSLETTER_PERSONAS),
      f"all personas present: {sorted(found_personas)}")

for nl in newsletters:
    p   = nl["persona"]
    wc  = len(nl.get("body","").split())
    check(120 <= wc <= 180,                   f"{p}: body 120-180 words (got {wc})")
    check("\u2014" not in nl.get("body","") and "—" not in nl.get("body",""),
          f"{p}: no em dashes in body")
    check("[READ MORE]" in nl.get("body",""), f"{p}: has [READ MORE] CTA")
    check(len(nl.get("subject","")) <= 60,    f"{p}: subject <= 60 chars")
    check(bool(nl.get("subject","").strip()), f"{p}: subject not empty")

nlt = Table(box=box.SIMPLE_HEAD, header_style="bold magenta")
nlt.add_column("Persona",  style="cyan", min_width=22)
nlt.add_column("Subject",  min_width=42)
nlt.add_column("Words",    justify="right")
for nl in newsletters:
    nlt.add_row(nl["persona"], nl["subject"], str(len(nl["body"].split())))
console.print(nlt)

# ══════════════════════════════════════════════════════════════════════════════
# 4. generate_content  (full pipeline)
# ══════════════════════════════════════════════════════════════════════════════
console.print(Rule("[bold cyan]4 · generate_content() — full three-step pipeline[/]"))
content = gen.generate_content(TOPIC)

for key in ("outline", "blog", "newsletters"):
    check(key in content, f"result has key '{key}'")

check(isinstance(content["outline"], list),      "outline is a list")
check(isinstance(content["newsletters"], list),   "newsletters is flat list")
check("sections" in content["blog"],             "blog has 'sections' key in full result")

try:
    json.dumps(content)
    ok("full result is JSON-serialisable")
except Exception as e:
    fail("JSON-serialisable", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 5. Outline validation layer
# ══════════════════════════════════════════════════════════════════════════════
console.print(Rule("[bold cyan]5 · Outline validation layer[/]"))

try:
    _validate_outline("not a list")
    fail("rejects non-list outline")
except ValidationError as e:
    ok(f"rejects non-list: {e}")

try:
    _validate_outline([{"title": "t", "goal": "proof", "persona_relevance": ["Marketing Manager"]}] * 3)
    fail("rejects outline with < 5 sections")
except ValidationError as e:
    ok(f"rejects < 5 sections: {e}")

try:
    bad = [dict(item) for item in outline]
    bad[0] = {**bad[0], "goal": "invalid_goal"}
    _validate_outline(bad)
    fail("rejects invalid goal")
except ValidationError as e:
    ok(f"rejects invalid goal: {e}")

try:
    bad = [dict(item) for item in outline]
    bad[1] = {**bad[1], "persona_relevance": ["Unknown Persona"]}
    _validate_outline(bad)
    fail("rejects unknown persona in outline")
except ValidationError as e:
    ok(f"rejects unknown persona: {e}")

try:
    bad = [dict(item) for item in outline]
    bad[2] = {k: v for k, v in bad[2].items() if k != "goal"}
    _validate_outline(bad)
    fail("rejects missing 'goal' key")
except ValidationError as e:
    ok(f"rejects missing key: {e}")

try:
    _validate_outline(outline)
    ok("valid outline passes cleanly")
except ValidationError as e:
    fail("valid outline passes cleanly", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 7. Blog validation layer
# ══════════════════════════════════════════════════════════════════════════════
console.print(Rule("[bold cyan]7 · Blog validation layer[/]"))

try:
    _validate_blog({**blog, "draft": blog["draft"] + " bad \u2014 dash"})
    fail("rejects em dash")
except ValidationError as e:
    ok(f"rejects em dash: {e}")

try:
    _validate_blog({**blog, "draft": "Too short."})
    fail("rejects short draft")
except ValidationError as e:
    ok(f"rejects short draft: {e}")

try:
    _validate_blog({**blog, "draft": ("word " * 700).strip()})
    fail("rejects long draft")
except ValidationError as e:
    ok(f"rejects long draft: {e}")

try:
    _validate_blog({**blog, "sections": blog["sections"][:-1]})  # one section missing
    fail("rejects sections/outline count mismatch")
except ValidationError as e:
    ok(f"rejects sections/outline mismatch: {e}")

try:
    bad_sections = [dict(s) for s in blog["sections"]]
    bad_sections[0] = {k: v for k, v in bad_sections[0].items() if k != "content"}
    _validate_blog({**blog, "sections": bad_sections})
    fail("rejects section missing 'content'")
except ValidationError as e:
    ok(f"rejects section missing key: {e}")

try:
    _validate_blog(blog)
    ok("valid blog passes cleanly")
except ValidationError as e:
    fail("valid blog passes cleanly", str(e))

try:
    _validate_blog({**blog, "draft": "This draft does not match the sections."})
    fail("rejects draft/sections mismatch")
except ValidationError as e:
    ok(f"rejects draft/sections mismatch: {e}")

try:
    bad_sections = [dict(s) for s in blog["sections"]]
    bad_sections[1] = {**bad_sections[1], "goal": "cta"}  # outline[1].goal is "pain_point"
    bad_draft = "\n\n".join(s["content"] for s in bad_sections)  # keep Check A from firing
    _validate_blog({**blog, "sections": bad_sections, "draft": bad_draft})
    fail("rejects section goal mismatch with outline")
except ValidationError as e:
    ok(f"rejects section goal mismatch: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# 8. Newsletter validation layer
# ══════════════════════════════════════════════════════════════════════════════
console.print(Rule("[bold cyan]8 · Newsletter validation layer[/]"))

try:
    _validate_newsletters({})
    fail("rejects missing 'newsletters' key")
except ValidationError as e:
    ok(f"rejects missing key: {e}")

try:
    _validate_newsletters({"newsletters": [
        {"persona": "Marketing Manager",    "subject": "s", "body": "word " * 150},
        {"persona": "Creative Professional","subject": "s", "body": "word " * 150},
    ]})
    fail("rejects missing persona")
except ValidationError as e:
    ok(f"rejects missing persona: {e}")

try:
    bad = [dict(nl) for nl in nl_result["newsletters"]]
    bad[0] = {**bad[0], "body": bad[0]["body"] + " bad \u2014 dash"}
    _validate_newsletters({"newsletters": bad})
    fail("rejects em dash in body")
except ValidationError as e:
    ok(f"rejects em dash: {e}")

try:
    bad = [dict(nl) for nl in nl_result["newsletters"]]
    bad[1] = {**bad[1], "body": "Too short."}
    _validate_newsletters({"newsletters": bad})
    fail("rejects short body")
except ValidationError as e:
    ok(f"rejects short body: {e}")

try:
    bad = [dict(nl) for nl in nl_result["newsletters"]]
    bad[2] = {**bad[2], "body": ("word " * 200).strip()}
    _validate_newsletters({"newsletters": bad})
    fail("rejects long body")
except ValidationError as e:
    ok(f"rejects long body: {e}")

try:
    _validate_newsletters(nl_result)
    ok("valid newsletters pass cleanly")
except ValidationError as e:
    fail("valid newsletters pass cleanly", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 9. generate_performance_summary
# ══════════════════════════════════════════════════════════════════════════════
console.print(Rule("[bold cyan]9 · generate_performance_summary()[/]"))
mock_metrics = [
    {"persona": "marketing_manager",    "open_rate": 0.24, "click_rate": 0.03, "unsubscribe_rate": 0.005, "total_sent": 6},
    {"persona": "creative_professional", "open_rate": 0.40, "click_rate": 0.08, "unsubscribe_rate": 0.002, "total_sent": 6},
    {"persona": "agency_founder",        "open_rate": 0.31, "click_rate": 0.05, "unsubscribe_rate": 0.004, "total_sent": 6},
]
result = gen.generate_performance_summary(mock_metrics, blog["title"])

check(isinstance(result, dict) and "status" in result and "text" in result,
      f"returns dict with status and text keys (status={result.get('status')})")
check(result["status"] == "valid",
      f"mock_metrics with 6 sends each → status=valid (got {result.get('status')})")
check(len(result["text"]) > 50,
      f"returns non-empty text ({len(result['text'])} chars)")
check("## Insights" in result["text"],
      "text contains ## Insights section")
check("\u2014" not in result["text"] and "—" not in result["text"],
      "no em dashes in summary")

console.print(Panel(result["text"], title=f"Performance Summary · {result['status']}", border_style="cyan", padding=(1, 2)))

# ══════════════════════════════════════════════════════════════════════════════
# Result
# ══════════════════════════════════════════════════════════════════════════════
console.print(Rule())
total  = PASSED + FAILED
colour = "green" if FAILED == 0 else "red"
console.print(f"[bold {colour}]{PASSED}/{total} tests passed[/]", justify="center")
if FAILED:
    sys.exit(1)
