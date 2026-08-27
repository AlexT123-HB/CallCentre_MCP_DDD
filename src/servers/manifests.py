"""
Manifest registry — the single registration point for domains.

To add a domain: import its MANIFEST and add it to MANIFESTS. That's the ONLY
host-side edit. Everything else (URL map, router prompt, narration, gate, tool
map) is derived from these manifests automatically.

Helpers here are the host's single source of truth about domains.
"""

from __future__ import annotations

from typing import Dict, List

from src.shared.manifest import DomainManifest

# ── the one registration list (add new domains here) ──
from src.servers.verification.manifest import MANIFEST as VERIFICATION
from src.servers.patient.manifest import MANIFEST as PATIENT
from src.servers.order.manifest import MANIFEST as ORDER     # ← future: one line
from src.servers.kb.manifest import MANIFEST as KB          
from src.servers.doctor.manifest import MANIFEST as DOCTOR 
from src.servers.booking.manifest import MANIFEST as BOOKING

MANIFESTS: List[DomainManifest] = [
    VERIFICATION,
    PATIENT,
    ORDER,
    KB,
    DOCTOR,
    BOOKING,
]

# ── derived lookups the host uses ──
BY_NAME: Dict[str, DomainManifest] = {m.name: m for m in MANIFESTS}


def all_manifests() -> List[DomainManifest]:
    return list(MANIFESTS)


def get(name: str) -> DomainManifest | None:
    return BY_NAME.get(name)


def names() -> List[str]:
    return [m.name for m in MANIFESTS]


def server_urls() -> Dict[str, str]:
    """domain → URL (replaces the hardcoded DOMAIN_SERVERS map)."""
    return {m.name: m.url for m in MANIFESTS}

def identity_sources() -> List[Dict[str, str]]:
    """Return the list of identity sources (for verification)."""
    return [m.identity_source for m in MANIFESTS if m.identity_source]
