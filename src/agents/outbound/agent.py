"""
Outbound WORKER — one LiveKit job per call attempt (its own process).

The LLM is the brain within each phase (see phases.py); this file is plumbing:
load the briefing, dial, run the session until an outcome, hang up, report the
outcome to the host.

Run:
    python -m src.agents.outbound.agent dev
"""


from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import AgentServer, AgentSession, JobContext, cli
from livekit.plugins import openai

from src.shared.outbound_contract import (
    AGENT_NAME,
    AttemptResult,
    ContractError,
    DispatchMetadata,
    Outcome,
)

from .dialer import dial
from .dispatch import load_briefing, release_briefing
from .host_client import HostClient
from .phases import PreVerifyAgent
from .state import CallState

load_dotenv()
log = logging.getLogger("heartbug")

MAX_CALL_S = 600  # hard stop for a call that never resolves
server = AgentServer()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

def _watch_room(ctx: JobContext, state: CallState, callee: str) -> None:
    @ctx.room.on("participant_disconnected")
    def _left(p: rtc.RemoteParticipant) -> None:
        if p.identity == callee:
            state.finish(Outcome.ABANDONED)   # no-op if an outcome is already set

    # LATER (handoff to a human): staff join the room with role=staff
    # @ctx.room.on("participant_connected")
    # def _joined(p: rtc.RemoteParticipant) -> None:
    #     if p.attributes.get("role") == "staff":
    #         state.staff_joined.set()

async def _hang_up(ctx: JobContext, callee: str) -> None:
    if callee in ctx.room.remote_participants:
        try:
            await ctx.api.room.remove_participant(
                api.RoomParticipantIdentity(room=ctx.room.name, identity=callee))
        except api.TwirpError:
            pass   # already gone

async def _run(ctx: JobContext, state: CallState, host: HostClient, to_number: str) -> None:
    callee = f"callee-{state.meta.callback_id}"
    await ctx.connect()
    _watch_room(ctx, state, callee)

    session = AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model=os.getenv("OUTBOUND_LLM_MODEL", "gpt-4.1-mini")),
        tts=openai.TTS(),
    )

    @session.on("user_input_transcribed")
    def _heard(_ev) -> None:
        state.heard_callee = True

    # start before dialling so nothing is missed on pickup
    await session.start(agent=PreVerifyAgent(state, host), room=ctx.room)

    result = await dial(ctx.api, room=ctx.room.name,
                        trunk_id=os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"],
                        to=to_number, identity=callee)
    if not result.answered:
        state.sip_status_code = result.sip_status_code
        state.finish(Outcome.FAILED, result.sip_status)
        return
    await ctx.wait_for_participant(identity=callee)

    try:
        await asyncio.wait_for(state.done.wait(), MAX_CALL_S)
    except TimeoutError:
        state.finish(Outcome.FAILED, "max call duration")

    if state.outcome is Outcome.VOICEMAIL and state.voicemail_message:
        # spoken here, outside the tool, so it can't block on the tool's own turn
        await session.say(state.voicemail_message, allow_interruptions=False).wait_for_playout()
    await _hang_up(ctx, callee)
    # LATER (handoff to a human): skip the hang-up when HANDED_OFF, so the callee
    # stays in the room with staff after the agent leaves.
    await session.aclose()

@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    started_at = _now()
    try:
        meta = DispatchMetadata.from_json(ctx.job.metadata)
    except ContractError as e:
        log.error(f"outbound: bad dispatch metadata: {e}")   # no token → nothing to report
        ctx.shutdown("bad metadata")
        return

    briefing = load_briefing(meta.callback_id, meta.attempt_token)
    if briefing is None:
        # expired or stale token: never dial without a briefing
        log.error(f"[{meta.callback_id}] outbound: no briefing")
        ctx.shutdown("no briefing")
        return

    host = HostClient.from_env()
    state = CallState(meta, briefing)
    try:
        await _run(ctx, state, host, briefing.to_number)
    except Exception as e:
        log.exception(f"[{meta.callback_id}] outbound call failed")
        state.finish(Outcome.FAILED, type(e).__name__)   # no message text: may hold PII

    try:
        await host.report_outcome(AttemptResult(
            callback_id=meta.callback_id, attempt_token=meta.attempt_token,
            outcome=state.outcome or Outcome.FAILED, started_at=started_at, ended_at=_now(),
            sip_status_code=state.sip_status_code, detail=state.detail))
    except Exception:
        log.exception(f"[{meta.callback_id}] could not report outcome {state.outcome}")
    finally:
        release_briefing(meta.callback_id, meta.attempt_token)
        await host.aclose()
        ctx.shutdown(f"call ended: {state.outcome}")


if __name__ == "__main__":
    cli.run_app(server)