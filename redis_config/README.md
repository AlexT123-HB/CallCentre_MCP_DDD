# Redis ACL — per-domain isolation

`users.acl` gives each container its own Redis user, restricted to its own key
namespace. This makes the domain boundary an **infrastructure guarantee**, not a
code convention: a bug in the doctor server physically cannot read `patient:*` —
Redis returns `NOPERM`.

**The aclfile format is strict: `user` lines only, no comments or blank lines.**
That's why this documentation lives here and not in the file itself.

| User | Namespace | Used by |
|---|---|---|
| `hostsvc` | `session:*` | host (session store) |
| `patientsvc` | `patient:*` | patient server |
| `ordersvc` | `order:*` `order_by_*:*` `pending:*` | order server |
| `doctorsvc` | `doctor:*` `clinic:*` | doctor server |
| `verifysvc` | (none — verification is never cached) | verification server |
| `bookingsvc` | `callback:*` | booking server |
| `outboundsvc` | `outbound_briefing:*` | outbound-worker server |

Verified: doctor user reading `patient:P001` → `NOPERM`; reading `doctor:D002` → OK.

## Production notes

- Passwords here are dev placeholders in plain text. In production they come from
  secrets (Docker secrets / env from a vault), not a file in the repo.
- The `default` user is left enabled with no password **only** so `redis-cli` works
  from your Mac during the demo. Disable it or set a password before production.
- `verifysvc` gets `+ping` only — it connects (so the client's `ping()` health check
  passes) but can touch no keys, matching "verification is never cached".
