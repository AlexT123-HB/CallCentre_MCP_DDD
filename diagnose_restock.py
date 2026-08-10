#!/usr/bin/env python3
"""
diagnose_restock.py — pinpoint exactly where the clinic-restock flow breaks.

Run from the project root (same place you run the servers), with your venv active:
    python diagnose_restock.py

It tests each stage in isolation and prints PASS/FAIL so you can see the exact
break point without reading through server logs.
"""
import asyncio
import sys

CHECK = "\u2713"
CROSS = "\u2717"

def ok(msg):   print(f"  {CHECK} {msg}")
def bad(msg):  print(f"  {CROSS} {msg}")
def head(msg): print(f"\n=== {msg} ===")


def stage_1_static_files():
    """Are the source files on disk correct? (imports read fresh from disk.)"""
    head("STAGE 1 — source files on disk")
    all_ok = True

    from src.shared.security import AccessContext
    import inspect
    sig = inspect.signature(AccessContext.grant).parameters
    if "clinic_id" in sig:
        ok("AccessContext.grant accepts clinic_id")
    else:
        bad("AccessContext.grant MISSING clinic_id  → src/shared/security.py stale"); all_ok = False

    ctx = AccessContext(call_id="t")
    if hasattr(ctx, "clinic_id"):
        ok("AccessContext has clinic_id field")
    else:
        bad("AccessContext MISSING clinic_id field  → src/shared/security.py stale"); all_ok = False

    from src.client.mcp_client import MCPClient
    ctx.grant("D002", role="doctor", clinic_id="C02")
    ip = MCPClient._identity_params(ctx)
    if ip.get("clinic_id") == "C02":
        ok("MCPClient._identity_params includes clinic_id")
    else:
        bad(f"_identity_params clinic_id={ip.get('clinic_id')!r}  → src/client/mcp_client.py stale"); all_ok = False

    # the null-drop fix
    src = inspect.getsource(MCPClient.call)
    if "is not None" in src:
        ok("MCPClient.call drops None-valued LLM args (the approved:null fix)")
    else:
        bad("MCPClient.call does NOT drop None args  → src/client/mcp_client.py stale"); all_ok = False

    from src.servers.order.run import _context
    csig = inspect.signature(_context).parameters
    if "clinic_id" in csig:
        ok("order _context accepts clinic_id")
    else:
        bad("order _context MISSING clinic_id  → src/servers/order/run.py stale"); all_ok = False

    from src.servers.manifests import all_manifests
    om = next((m for m in all_manifests() if m.name == "order"), None)
    if om and "restock_needs_approval" in om.events:
        ok("order manifest has restock_needs_approval event")
    else:
        bad("order manifest MISSING restock event  → src/servers/order/manifest.py stale"); all_ok = False

    return all_ok


def stage_2_service_direct():
    """Does the order SERVICE work in-process (no MCP, no validation)?"""
    head("STAGE 2 — order service in-process")
    from src.servers.order.infrastructure.application import build_order_service
    from src.shared.security import AccessContext
    svc = build_order_service()
    ctx = AccessContext(call_id="t"); ctx.grant("D002", role="doctor", clinic_id="C02")
    try:
        r = svc.place_restock(ctx, "C02", "sticker packs", 50)
        if r.needs_human and not r.accepted:
            ok(f"place_restock gated correctly (needs_human, ref pending). reason={r.reason!r}")
            return True
        bad(f"unexpected result: accepted={r.accepted} needs_human={r.needs_human}")
    except Exception as e:
        import traceback; bad(f"EXCEPTION: {e}"); traceback.print_exc()
    return False


async def stage_3_over_mcp(order_url):
    """Does the live order SERVER accept the exact args the host sends?"""
    head("STAGE 3 — live order server over MCP")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    # (a) with approved=False explicitly (what the direct test sends)
    async def call(args, label):
        try:
            async with streamablehttp_client(order_url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.call_tool("request_clinic_restock", args)
                    txt = res.content[0].text if res.content else "(empty)"
                    err = getattr(res, "isError", None)
                    print(f"  [{label}] isError={err}")
                    print(f"    → {txt[:200]}")
                    return not err and "restock_needs_approval" in txt or "restock_placed" in txt
        except Exception as e:
            bad(f"[{label}] transport error: {e}"); return False

    base = {"call_id":"t","verified":True,"subject_id":"D002","role":"doctor",
            "clinic_id":"C02","product":"sticker packs","quantity":50}

    r1 = await call({**base, "approved": False}, "approved=False (bool)")
    r2 = await call({**base, "approved": None},  "approved=None (LLM null) — THIS is the bug case")
    # what the host actually sends AFTER the null-drop fix (approved omitted):
    r3 = await call(base, "approved OMITTED (host after null-drop)")

    if r1 and not r2 and r3:
        ok("Confirmed: approved=None FAILS validation; omitting it (host null-drop) WORKS")
    elif r1 and r2 and r3:
        ok("All variants pass — server tolerates null too")
    return r3


async def stage_4_full_host_path(order_url):
    """Does the REAL MCPClient (gate + identity inject + null-drop) succeed?"""
    head("STAGE 4 — full host MCPClient path (the real thing)")
    from src.client.mcp_client import MCPClient, DOMAIN_SERVERS
    from src.shared.security import AccessContext
    DOMAIN_SERVERS["order"] = order_url   # point at our test server

    client = MCPClient()
    ctx = AccessContext(call_id="S15"); ctx.grant("D002", role="doctor", clinic_id="C02")
    session = {"call_id":"S15","ctx":ctx}
    # exactly what the host passes: LLM business args incl. the nulls
    res = await client.call(session, "order", "request_clinic_restock",
                            clinic_id=None, product="sticker packs",
                            quantity=50)
    print(f"  host result: {res}")
    if isinstance(res, dict) and res.get("event") in ("restock_needs_approval","restock_placed"):
        ok("FULL PATH WORKS — host injects clinic, drops nulls, tool returns restock event")
        return True
    bad("full path failed — see result above")
    return False


async def main():
    import os, subprocess, time
    order_url = "http://localhost:3003/mcp"

    # stage 1 + 2 don't need a server
    s1 = stage_1_static_files()
    s2 = stage_2_service_direct()

    # stages 3 + 4 need a live order server — start a throwaway one
    head("starting a throwaway order server on :3003 for live tests")
    env = {**os.environ, "PYTHONPATH": os.getcwd(), "ORDER_PORT": "3003",
           "HEARTBUG_SERVICE_TOKEN": os.getenv("HEARTBUG_SERVICE_TOKEN","internal-service-secret")}
    proc = subprocess.Popen([sys.executable, "-m", "src.servers.order.run"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)
    try:
        s3 = await stage_3_over_mcp(order_url)
        s4 = await stage_4_full_host_path(order_url)
    finally:
        proc.terminate()

    head("SUMMARY")
    print(f"  Stage 1 (files on disk):      {'PASS' if s1 else 'FAIL — sync stale files'}")
    print(f"  Stage 2 (service in-process): {'PASS' if s2 else 'FAIL — service logic bug'}")
    print(f"  Stage 3 (server validation):  {'PASS' if s3 else 'FAIL — server rejects the args'}")
    print(f"  Stage 4 (full host path):     {'PASS' if s4 else 'FAIL — host injection/null-drop'}")
    print()
    if s1 and s2 and s3 and s4:
        print("  All stages pass on disk. If your RUNNING system still fails,")
        print("  your host/order PROCESS is stale — restart it:  ./run_all.sh kill && ./run_all.sh")
    else:
        print("  The first FAIL above is the exact break point — fix that file, restart, re-run.")


if __name__ == "__main__":
    asyncio.run(main())
