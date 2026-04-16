"""
Brevo CRM integration: contacts, lists, and persona segmentation.
"""

import logging
import requests
from typing import Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


BREVO_BASE = "https://api.brevo.com/v3"


class CRMManager:
    REQUEST_TIMEOUT_SECONDS = 12

    def __init__(self, api_key: str):
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "api-key": api_key,
        }
        retry = Retry(
            total=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
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

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_account(self) -> dict:
        r = self._request("GET", "/account")
        return r.json()

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    def upsert_contact(self, email: str, first_name: str, last_name: str,
                       persona: str, list_id: int) -> None:
        """Create or update a contact and add them to the persona list."""
        payload = {
            "email": email,
            "attributes": {
                "FIRSTNAME": first_name,
                "LASTNAME": last_name,
                "PERSONA": persona,
            },
            "listIds": [list_id],
            "updateEnabled": True,
        }
        r = self.session.post(
            f"{BREVO_BASE}/contacts",
            json=payload,
            headers=self.headers,
            timeout=self.REQUEST_TIMEOUT_SECONDS,
        )
        # 204 = already exists and was updated, 201 = created — both fine
        if r.status_code not in (200, 201, 204):
            r.raise_for_status()

    def upsert_contacts_bulk(self, contacts: list[dict], list_ids_by_persona: dict) -> int:
        """
        Push all mock contacts to Brevo.
        Returns count of successfully synced contacts.
        """
        count = 0
        failures: list[dict] = []
        for c in contacts:
            persona = c["persona"]
            list_id = list_ids_by_persona.get(persona)
            if list_id is None:
                continue
            try:
                self.upsert_contact(
                    email=c["email"],
                    first_name=c["firstName"],
                    last_name=c["lastName"],
                    persona=persona,
                    list_id=list_id,
                )
                count += 1
            except Exception as e:
                failures.append({"email": c["email"], "persona": persona, "error": str(e)})

        for f in failures:
            logger.warning(
                "upsert_contact failed for %s (%s): %s",
                f["email"], f["persona"], f["error"],
            )
        return count

    # ------------------------------------------------------------------
    # Lists (persona segments)
    # ------------------------------------------------------------------

    def get_all_lists(self) -> list[dict]:
        r = self._request("GET", "/contacts/lists?limit=50")
        return r.json().get("lists", [])

    def _get_or_create_folder(self, name: str = "NovaMind") -> int:
        """Return the ID of the named folder, creating it if it doesn't exist."""
        r = self._request("GET", "/contacts/folders?limit=50")
        for folder in r.json().get("folders", []):
            if folder["name"] == name:
                return folder["id"]
        r2 = self._request("POST", "/contacts/folders", json={"name": name})
        return r2.json()["id"]

    def find_or_create_list(self, name: str, folder_id: Optional[int] = None) -> int:
        """Return existing list ID or create a new one inside the NovaMind folder."""
        existing = self.get_all_lists()
        for lst in existing:
            if lst["name"] == name:
                return lst["id"]

        if folder_id is None:
            folder_id = self._get_or_create_folder()

        r = self._request(
            "POST",
            "/contacts/lists",
            json={"name": name, "folderId": folder_id},
        )
        return r.json()["id"]

    def setup_persona_lists(self, personas: dict) -> dict[str, int]:
        """
        Ensure one Brevo list exists per persona.
        Returns {persona_slug: list_id}
        """
        folder_id = self._get_or_create_folder()
        mapping = {}
        for slug, meta in personas.items():
            list_name = f"NovaMind – {meta['label']}"
            list_id = self.find_or_create_list(list_name, folder_id=folder_id)
            mapping[slug] = list_id
        return mapping

    # ------------------------------------------------------------------
    # CRM-side campaign logging
    # ------------------------------------------------------------------

    def create_note(self, text: str) -> str:
        """Create a CRM note in Brevo and return its note ID."""
        r = self._request("POST", "/crm/notes", json={"text": text})
        return r.json()["id"]

    def log_campaign_note(
        self,
        *,
        topic: str,
        blog_title: str,
        persona_slug: str,
        persona_label: str,
        brevo_campaign_id: int,
        brevo_list_id: int | None,
        crm_status: str | None,
        crm_sent_at: str | None,
        crm_status_reason: str | None = None,
    ) -> str:
        """
        Write a CRM-side audit note for a persona campaign send.

        This complements the local SQLite log without changing pipeline logic.
        """
        lines = [
            "NovaMind Campaign Log",
            f"Topic: {topic}",
            f"Blog title: {blog_title}",
            f"Persona: {persona_label} ({persona_slug})",
            f"Brevo campaign ID: {brevo_campaign_id}",
            f"Brevo list ID: {brevo_list_id if brevo_list_id is not None else 'unknown'}",
            f"CRM status: {crm_status or 'unknown'}",
            f"Send date: {crm_sent_at or 'unknown'}",
        ]
        if crm_status_reason:
            lines.append(f"Status reason: {crm_status_reason}")
        return self.create_note("\n".join(lines))
