"""
Central security policy — the one place pre-verification access is governed.

The manifest's `requires_verification` field is NOT trusted blindly. A domain may
only be reachable BEFORE verification if it is explicitly listed here. This makes
opening a domain to unverified callers a deliberate, auditable, central decision —
never an accident of adding a domain.

To allow a new pre-verification domain (e.g. a public knowledge base), add its
name here AND set requires_verification=False in its manifest. Both are required.
"""

from __future__ import annotations

from typing import Set

# Domains permitted to be reachable BEFORE the caller is verified.
# verification: the gate domain itself (must be reachable to verify).
# (knowledge would be added here when a public KB domain is introduced.)
PRE_VERIFICATION_ALLOWLIST: Set[str] = {"verification", "kb"}


def may_skip_verification(domain_name: str, manifest_requires_verification: bool) -> bool:
    """
    True only if a domain is reachable pre-verification. Requires BOTH:
      - the manifest declares requires_verification=False, AND
      - the domain is on the central allowlist.
    A domain claiming False but not on the allowlist is treated as verified-only.
    """
    declares_open = not manifest_requires_verification
    centrally_allowed = domain_name in PRE_VERIFICATION_ALLOWLIST
    return declares_open and centrally_allowed
