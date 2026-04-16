"""
Brevo email campaign creation and (simulated) dispatch.

Why simulated?
  Brevo requires all recipient emails to be real opt-in addresses before
  an actual send.  Our mock contacts use fictional domains, so we create
  the campaign object in Brevo (giving us a real campaign ID to log) but
  skip the sendNow call and instead record status as 'simulated'.
"""

import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BREVO_BASE = "https://api.brevo.com/v3"
PT = ZoneInfo("America/Los_Angeles")


def _build_html(subject: str, body: str, persona_label: str) -> str:
    """Wrap plain-text newsletter body in a minimal, readable HTML shell."""
    paragraphs = "".join(
        f"<p style='margin:0 0 14px 0;'>{p.strip()}</p>"
        for p in body.split("\n\n")
        if p.strip()
    )
    paragraphs = paragraphs.replace(
        "[READ MORE]",
        "<a href='https://novamind.io/blog/' "
        "style='display:inline-block;background:#1a1a1a;color:#fff;"
        "padding:10px 20px;text-decoration:none;border-radius:4px;"
        "font-size:13px;margin-top:8px;'>Read More &rarr;</a>",
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{subject}</title></head>
<body style="font-family:Georgia,serif;max-width:600px;margin:40px auto;
             padding:0 24px;color:#1a1a1a;line-height:1.7;text-align:justify;">
  <p style="font-size:12px;color:#888;margin-bottom:32px;text-transform:uppercase;
             letter-spacing:.08em;">NovaMind · Weekly</p>
  <h2 style="font-size:22px;margin-bottom:20px;">{subject}</h2>
  {paragraphs}
  <hr style="border:none;border-top:1px solid #e5e5e5;margin:32px 0;">
  <p style="font-size:11px;color:#aaa;">
    You're receiving this because you're subscribed to NovaMind's newsletter.
    <a href="{{{{unsubscribe}}}}" style="color:#aaa;">Unsubscribe</a>
  </p>
</body>
</html>"""


class CampaignManager:
    REQUEST_TIMEOUT_SECONDS = 12

    def __init__(self, api_key: str, sender_email: str, sender_name: str = "NovaMind"):
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "api-key": api_key,
        }
        self.sender_email = sender_email
        self.sender_name  = sender_name
        retry = Retry(
            total=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"POST"}),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session = requests.Session()
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        kwargs.setdefault("headers", self.headers)
        kwargs.setdefault("timeout", self.REQUEST_TIMEOUT_SECONDS)
        response = self.session.request(method, f"{BREVO_BASE}{path}", **kwargs)
        response.raise_for_status()
        return response

    def create_campaign(self, name: str, subject: str, body: str,
                        list_id: int, persona_label: str) -> int:
        """
        Create a Brevo email campaign in DRAFT state.
        Returns the Brevo campaign ID.
        """
        payload = {
            "name":       name,
            "subject":    subject,
            "sender":     {"name": self.sender_name, "email": self.sender_email},
            "type":       "classic",
            "htmlContent": _build_html(subject, body, persona_label),
            "recipients": {"listIds": [list_id]},
        }
        r = self._request("POST", "/emailCampaigns", json=payload)
        return r.json()["id"]

    def get_campaign_state(self, brevo_campaign_id: int) -> dict:
        """Return a compact status snapshot for a Brevo campaign."""
        r = self._request("GET", f"/emailCampaigns/{brevo_campaign_id}")
        data = r.json()
        status = (data.get("status") or "").strip().lower() or None
        sent_at = data.get("sentDate")
        reason = None

        if status == "suspended":
            reason = "Suspended in Brevo, often due to recipient subscription or compliance state."

        return {
            "crm_status": status,
            "crm_sent_at": sent_at,
            "crm_status_reason": reason,
        }

    def send_campaign(self, brevo_campaign_id: int) -> None:
        """Send a drafted Brevo campaign immediately."""
        r = self.session.post(
            f"{BREVO_BASE}/emailCampaigns/{brevo_campaign_id}/sendNow",
            headers=self.headers,
            timeout=self.REQUEST_TIMEOUT_SECONDS,
        )
        # 204 = sent, 400 = already sent — both acceptable
        if r.status_code not in (200, 204, 400):
            r.raise_for_status()

    def create_all_campaigns(self, newsletters: dict, list_ids_by_persona: dict,
                             blog_title: str, personas_meta: dict) -> dict[str, int]:
        """
        Create one Brevo campaign per persona newsletter.
        Returns {persona_slug: brevo_campaign_id}
        """
        campaign_ids = {}
        date_str = datetime.now(PT).strftime("%Y-%m-%d")

        for persona_slug, content in newsletters.items():
            list_id       = list_ids_by_persona[persona_slug]
            persona_label = personas_meta[persona_slug]["label"]
            campaign_name = f"[{date_str}] {blog_title[:40]} – {persona_label}"

            brevo_id = self.create_campaign(
                name          = campaign_name,
                subject       = content["subject"],
                body          = content["body"],
                list_id       = list_id,
                persona_label = persona_label,
            )
            campaign_ids[persona_slug] = brevo_id

        return campaign_ids

    def send_all_campaigns(self, campaign_ids: dict[str, int]) -> None:
        """Send all drafted campaigns immediately."""
        for persona_slug, brevo_id in campaign_ids.items():
            self.send_campaign(brevo_id)
