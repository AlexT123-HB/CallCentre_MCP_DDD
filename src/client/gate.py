"""
Host GATE — available_domains(session).

Decides WHICH domains the router may reach, by verification state. Now driven by
domain MANIFESTS + the central security policy, not hardcoded:

  - unverified → only domains allowed pre-verification (manifest declares
    requires_verification=False AND domain is on the central allowlist)
  - verified   → all domains EXCEPT pure pre-verification ones (e.g. verification)

A rule, not a layer. Adding a domain needs NO change here — its manifest's
requires_verification (validated centrally) determines its reachability.
"""

from __future__ import annotations

from typing import List

from src.servers.manifests import all_manifests
from src.shared.policy import may_skip_verification


def available_domains(session: dict, registry=None) -> List[str]:
    verified = session["ctx"].verified
    out: List[str] = []
    for m in all_manifests():
        pre_verify_ok = may_skip_verification(m.name, m.requires_verification)
        if not verified:
            if pre_verify_ok:
                out.append(m.name)            # reachable before verification
        else:
            # verified: the real data domains, PLUS any public domain that is also
            # useful after verifying (e.g. the knowledge base). Verification itself
            # is excluded — it's purely a pre-verification gate, not a data domain.
            if not pre_verify_ok:
                out.append(m.name)
            elif getattr(m, "public", False):
                out.append(m.name)            # public KB stays available post-verify
    return out

def verification_gated_domains(session: dict) -> dict:
    """Domains that EXIST but require verification the caller doesn't yet have.
    Used to steer an unverified caller who wants one of these (e.g. placing an
    order) to VERIFY FIRST, instead of mis-routing the intent to a public domain
    like the KB. Returns {name: description}; empty once the caller is verified."""
    if session["ctx"].verified:
        return {}
    gated = {}
    for m in all_manifests():
        if not may_skip_verification(m.name, m.requires_verification):
            gated[m.name] = m.description
    return gated

