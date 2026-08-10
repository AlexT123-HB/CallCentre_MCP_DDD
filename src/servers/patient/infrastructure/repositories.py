"""
Patient infrastructure — concrete repository.

Implements the domain's PatientRepository interface using the adaptor, with the
shared cache in front: get_by_id checks the cache first, falls back to the data
source on a miss, and backfills. The domain service never knows caching exists —
it lives entirely behind this interface.
"""

from __future__ import annotations

from typing import Optional

from src.servers.patient.domain.models import Patient
from src.servers.patient.domain.repository.interfaces import PatientRepository
from src.servers.patient.infrastructure.adaptors import PatientDataAdaptor
from src.shared.cache import get_cache, cache_key
import logging

# patient records change rarely within a call; 1h TTL is safe for a cache entry
_PATIENT_TTL = 3600

log = logging.getLogger("heartbug")

class PatientRepositoryImpl(PatientRepository):
    def __init__(self, adaptor: Optional[PatientDataAdaptor] = None) -> None:
        self._adaptor = adaptor or PatientDataAdaptor()
        self._cache = get_cache()

    def get_by_id(self, patient_id: str) -> Optional[Patient]:
        key = cache_key("patient", patient_id)
        cached = self._cache.get(key)              # 1. check cache
        if cached is not None:
            log.info(f"CACHE HIT {key}")
            return cached
        log.info(f"CACHE MISS {key}, query DB")
        record = self._adaptor.fetch_by_id(patient_id)   # 2. miss → data source
        if record is not None:
            self._cache.set(key, record, ttl_seconds=_PATIENT_TTL)   # 3. backfill
        return record

    def get_by_phone(self, phone: str) -> Optional[Patient]:
        return self._adaptor.fetch_by_phone(phone)
    
    def has_care_relationship(self, doctor_id: str, patient_id: str) -> bool:
        return self._adaptor.care_relationship(doctor_id, patient_id)

    def find_ids_by_name(self, full_name: str) -> list:
        return self._adaptor.ids_by_name(full_name)

    def get_monitoring(self, patient_id: str):
        key = cache_key("monitoring", patient_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        record = self._adaptor.fetch_monitoring(patient_id)
        if record is not None:
            self._cache.set(key, record, ttl_seconds=_PATIENT_TTL)
        return record

    def get_alerts(self, patient_id: str) -> list:
        return self._adaptor.fetch_alerts(patient_id)   # alerts are time-sensitive, no cache
