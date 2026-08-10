"""
Doctor infrastructure — the doctor's OWN record source, with the shared cache.

Unlike patient data (reached across a context boundary via the ACL), the doctor's
own details — including the email a report is sent to — belong to the DOCTOR
context itself. This adaptor reads them from the shared store now; it swaps to the
real clinic-user API later (only this file changes).

Caching: the doctor's own record and their clinic are cached as ENTITIES keyed by
their own id ('doctor:D002', 'clinic:C02') — the same recipe as patient/order. Both
change rarely, so the TTL is long. NOTE: patient data is deliberately NOT cached
here — it lives in the patient context and is reached over MCP, so PHI stays in one
place with one guard rather than being copied into this server.
"""

from __future__ import annotations

from typing import Optional

from src.shared.cache import get_cache, cache_key
from src.shared import database as db

# doctor + clinic records barely change; a long TTL is safe
_DIRECTORY_TTL = 6 * 3600


class DoctorDirectory:
    """Lookup of the calling doctor's own record fields (cache → store)."""

    def __init__(self) -> None:
        self._cache = get_cache()

    def record_of(self, doctor_id: str) -> dict:
        """The doctor's own record: cache → store → backfill."""
        if not doctor_id:
            return {}
        key = cache_key("doctor", doctor_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        rec = db.DOCTORS.get(doctor_id) or {}
        if rec:
            self._cache.set(key, rec, ttl_seconds=_DIRECTORY_TTL)
        return rec

    def email_of(self, doctor_id: str) -> Optional[str]:
        return (self.record_of(doctor_id) or {}).get("email")

    def clinic_of(self, clinic_id: str) -> dict:
        """The clinic record (name/address) — cached; used for restock shipping."""
        if not clinic_id:
            return {}
        key = cache_key("clinic", clinic_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        rec = db.CLINICS.get(clinic_id) or {}
        if rec:
            self._cache.set(key, rec, ttl_seconds=_DIRECTORY_TTL)
        return rec
