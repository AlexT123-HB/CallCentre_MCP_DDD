"""
Talk to the outbound agent directly — real host, real verification, no LiveKit, no phone.

  1. The real Host runs call_now() for a test patient, exactly like a transfer: real
     briefing (first name from the patient domain, your intro, CHECK_SLOTS) and a real
     attempt session in Redis. Only the LiveKit dispatch is swapped for a no-op.
  2. The agent (PreVerifyAgent / PostVerifyAgent, your prompts + LLM) runs right here,
     in LiveKit's text mode — no room, no worker.
  3. Its /verify goes to the RUNNING host → the real verification domain; on a pass the
     host hands back the reason, exactly like a real call. The outcome is reported to
     the host too, so it shows in the host log as OUTBOUND outcome: ...

Run from the repo root (the host container has Host, the OpenAI key and livekit-agents):
    docker compose cp eval_outbound_typing.py host:/app/eval_outbound_typing.py
    docker compose exec -it host python /app/eval_outbound_typing.py --patient P003

Options: --reason "help setting up their new heart monitor"    --quiet (hide tool lines)
Type what the callee says (start with "hello"). Ctrl-D to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
import sys
from datetime import UTC, datetime

from livekit.agents import AgentSession
from livekit.agents.voice.run_result import (AgentHandoffEvent, ChatMessageEvent,
                                             FunctionCallEvent, FunctionCallOutputEvent)

from src.agents.outbound.host_client import HostClient
from src.agents.outbound.phases import PreVerifyAgent
from src.agents.outbound.state import CallState
from src.client.host import Host
from src.shared.outbound_contract import AttemptResult, DispatchMetadata, Outcome

logging.getLogger("livekit").setLevel(logging.WARNING)


class LoggingHostClient(HostClient):
    """The real HostClient, printing what the host answers to /verify."""

    async def verify(self, meta, answers):
        result = await super().verify(meta, answers)
        shown = f"passed=True  reason={result.reason!r}" if result.passed else "passed=False"
        print(f"  [host /verify] {answers} → {shown}")
        return result


def _show(res, quiet: bool, said: dict) -> None:
    for ev in res.events:
        if isinstance(ev, AgentHandoffEvent):
            said["verified"] = True
            if not quiet:
                print(f"  [verified → {type(ev.new_agent).__name__}]")
        elif isinstance(ev, ChatMessageEvent) and ev.item.role == "assistant":
            text = ev.item.text_content or ""
            said["post" if said["verified"] else "pre"].append(text)
            print(f"AGENT:  {text}")
        elif quiet:
            continue
        elif isinstance(ev, FunctionCallEvent):
            print(f"  [tool]  {ev.item.name}({ev.item.arguments})")
        elif isinstance(ev, FunctionCallOutputEvent) and ev.item.output:
            print(f"  [tool→] {ev.item.output.replace(chr(10), ' ')[:160]}")


async def main(patient_id: str, reason: str, quiet: bool) -> None:
    host = Host()

    # call_now, but keep the briefing instead of dispatching to LiveKit
    captured: dict = {}

    async def keep_briefing(briefing, token):
        captured["briefing"], captured["token"] = briefing, token
        return "direct-no-room"

    host.outbound._start_subagent = keep_briefing

    call_id = f"typing-{secrets.token_hex(3)}"
    session = host.sessions.start(call_id, "")
    session["ctx"].grant(patient_id, role="patient")          # as if verified inbound
    host.sessions.save(session)
    callback_id = await host.outbound.call_now(session, reason, to_number="text")

    meta = DispatchMetadata(callback_id=callback_id, attempt_token=captured["token"])
    state = CallState(meta, captured["briefing"])
    state.heard_callee = True                                  # no "nudge if silent" here
    if hasattr(state, "answered"):
        state.answered.set()
    host_client = LoggingHostClient(os.getenv("HOST_BASE_URL", "http://localhost:8000"),
                                    os.environ["OUTBOUND_API_TOKEN"])

    from livekit.plugins import openai
    agent_session = AgentSession(
        llm=openai.LLM(model=os.getenv("OUTBOUND_LLM_MODEL", "gpt-4.1-mini")))

    b = captured["briefing"]
    print(f"\nPatient {patient_id} · first name {b.patient_first_name!r} · intro {b.intro!r}")
    print("You are the callee — type what you say (start with \"hello\"). Ctrl-D to stop.\n")

    started = datetime.now().isoformat(timespec="seconds")
    said = {"verified": False, "pre": [], "post": []}
    await agent_session.start(PreVerifyAgent(state, host_client))
    try:
        while not state.done.is_set():
            try:
                text = input("CALLEE: ").strip()
            except EOFError:
                print()
                break
            if not text:
                continue
            try:
                _show(await agent_session.run(user_input=text), quiet, said)
            except Exception as e:                             # noqa: BLE001
                print(f"  [run raised] {type(e).__name__}: {e}")
            await asyncio.sleep(0.3)                           # let end_call / briefing settle
    finally:
        await agent_session.aclose()

    outcome = state.outcome or Outcome.ABANDONED
    try:                                                       # host logs OUTBOUND outcome
        await host_client.report_outcome(AttemptResult(
            callback_id=callback_id, attempt_token=captured["token"], outcome=outcome,
            started_at=started, ended_at=datetime.now().isoformat(timespec="seconds")))
    finally:
        await host_client.aclose()

    words = [w for w in reason.lower().split() if len(w) > 3]
    leaked = [w for w in words if w in " ".join(said["pre"])]
    print(f"\n[call ended: {outcome}]")
    print(f"  {'✗' if leaked else '✓'} reason before verification: "
          f"{'LEAKED ' + str(leaked) if leaked else 'not mentioned'}")
    if said["verified"]:
        briefed = any(w in " ".join(said["post"]).lower() for w in words)
        print(f"  {'✓' if briefed else '✗'} reason after verification: "
              f"{'mentioned' if briefed else 'not mentioned'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Type as the callee; agent in-process, real host")
    p.add_argument("--patient", required=True, help="patient_id of the test patient, e.g. P003")
    p.add_argument("--reason", default="help setting up their new heart monitor")
    p.add_argument("--quiet", action="store_true", help="hide tool calls and handoffs")
    a = p.parse_args()
    try:
        asyncio.run(main(a.patient, a.reason, a.quiet))
    except KeyboardInterrupt:
        sys.exit(0)