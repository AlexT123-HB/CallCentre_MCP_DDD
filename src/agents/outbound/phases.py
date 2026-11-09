"""The LLM brain, split into two phase agents.

PreVerifyAgent  — knows only the first name, the intro and what to ask. Collects
                  name + date of birth and sends them to the host to verify. It
                  cannot say why we're calling: it was never told.
PostVerifyAgent — created ONLY by a passing host verification, which is also what
                  delivers the reason for the call. Briefly explains it, then
                  closes the call.

The LLM converses and slot-fills freely; the code decides what each phase can do.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import ChatContext

from src.shared.outbound_contract import Outcome
from src.shared.config import _BASE_STYLE

from .host_client import HostClient
from .state import CallState

log = logging.getLogger("heartbug")

FIRST_WORDS_WAIT_S = 4.0  # let the callee say "hello?" first; nudge if they don't

def _pre_instructions(s: CallState) -> str:
    slots = "\n".join(f"   - {k}: {v}" for k, v in s.slots.items())
    return f"""{_BASE_STYLE}. You are from HeartBug, placing an outbound phone call to someone who asked to speak with a person.
You do NOT yet know who answered, and you have not been told why you're calling.
Introduce the call only with: "{s.intro}"

1. If you hear a voicemail greeting, answering machine or automated message rather than
   a live person, call mark_voicemail straight away and say nothing.
2. Otherwise introduce yourself and your company, greet them, and ask if you're speaking with {s.first_name}.
3. Wrong person: apologise briefly, say goodbye, then call end_call("wrong_person").
4. Bad time to talk: say someone will try again later, say goodbye, then call
   end_call("bad_time").
5. Once they confirm they are {s.first_name}, say you need to confirm a couple of details
   first, then collect these one at a time, clarifying if unclear:
{slots}
   Ask in plain words; never ask the caller to use a particular format. You convert
   their answers to the stated format when submitting. When you have all of them, call submit_check_answer.
6. If asked why you're calling, say you can share that once their details are confirmed.
7. If they won't confirm their details, say goodbye and call end_call("refused").
"""


def _post_instructions(s: CallState) -> str:
    return f"""{_BASE_STYLE}. You are on a phone call with {s.first_name}, whose identity has just been confirmed.
The reason for the call: {s.reason}.
- Thank them, then briefly explain why you're calling, in one or two sentences.
- Tell them a member of our team will follow up with them, and say goodbye.
- Don't give clinical, account or order advice.
"""


class PreVerifyAgent(Agent):
    def __init__(self, state: CallState, host: HostClient) -> None:
        self._s, self._host = state, host
        super().__init__(instructions=_pre_instructions(state),
                         tools=[self._build_submit_tool()])

    async def on_enter(self) -> None:
        asyncio.create_task(self._nudge_if_silent())

    async def _nudge_if_silent(self) -> None:
        await asyncio.sleep(FIRST_WORDS_WAIT_S)
        if not self._s.heard_callee and not self._s.done.is_set():
            self.session.generate_reply(
                instructions="Nobody has spoken yet. Greet them and give the introduction.")

    def _build_submit_tool(self):
        """Built per call so its schema has exactly the slots the host asked for."""
        slots = self._s.slots
        schema = {
            "name": "submit_check_answer",
            "description": "Submit the identity details the person gave, once you have all of them.",
            "parameters": {
                "type": "object",
                "properties": {k: {"type": "string", "description": v} for k, v in slots.items()},
                "required": list(slots),
                "additionalProperties": False,
            },
        }

        @function_tool(raw_schema=schema)
        async def submit_check_answer(raw_arguments: dict[str, object], context: RunContext):
            answers = {k: str(raw_arguments.get(k) or "").strip() for k in slots}
            missing = [k for k, v in answers.items() if not v]
            if missing:
                return f"Still missing: {', '.join(missing)}. Ask for those."
            self._s.check_attempts += 1
            try:
                result = await self._host.verify(self._s.meta, answers)
            except Exception as e:
                log.error(f"[{self._s.meta.callback_id}] Error occurred while verifying callee: {e}")
                raise
            if result.passed:
                self._s.mark_verified(result.reason)
                log.info(f"[{self._s.meta.callback_id}] outbound callee verified")
                return PostVerifyAgent(self._s, self._host, chat_ctx=self.chat_ctx)
            if self._s.checks_exhausted:
                return ("The details didn't match and there are no attempts left. Say you "
                        "couldn't confirm their details and the team will follow up another "
                        "way, say goodbye, then call end_call(\"unverified\").")
            return "Those details didn't match. Apologise and ask for them once more."

        return submit_check_answer

    @function_tool
    async def mark_voicemail(self, context: RunContext) -> None:
        """Call when a voicemail greeting, answering machine or automated message answered
        instead of a live person."""
        self._s.finish(Outcome.VOICEMAIL)

    @function_tool
    async def end_call(
        self, context: RunContext,
        outcome: Literal["wrong_person", "bad_time", "refused", "unverified"],
    ) -> str | None:
        """End the call, after you have said goodbye."""
        if outcome == "unverified" and not self._s.checks_exhausted:
            return "Not allowed yet: they still have a verification attempt left."
        await context.wait_for_playout()
        self._s.finish(Outcome(outcome))
        return None


class PostVerifyAgent(Agent):
    def __init__(self, state: CallState, host: HostClient, chat_ctx: ChatContext | None) -> None:
        self._s, self._host = state, host
        super().__init__(instructions=_post_instructions(state), chat_ctx=chat_ctx)

    async def on_enter(self) -> None:
        handle = self.session.generate_reply(
            instructions="Thank them for confirming. In one or two sentences say why you're "
                         "calling, then say a member of our team will follow up, and say goodbye.")
        asyncio.create_task(self._close_after(handle))

    async def _close_after(self, handle) -> None:
        """Hang up once the briefing has been spoken."""
        await handle.wait_for_playout()
        self._s.finish(Outcome.BRIEFED)

    # LATER (handoff to a human): instead of closing after the briefing, add staff
    # to the call and hold the line until they join:
    #
    #   async def _hand_off(self) -> None:
    #       try:
    #           await self._host.request_handoff(self._s.meta)
    #           await asyncio.wait_for(self._s.staff_joined.wait(), HANDOFF_TIMEOUT_S)
    #           self._s.finish(Outcome.HANDED_OFF)
    #       except TimeoutError:
    #           handle = self.session.generate_reply(
    #               instructions="Apologise: the team isn't free right now. Say someone "
    #                            "will call them back soon, and say goodbye.")
    #           await handle.wait_for_playout()
    #           self._s.finish(Outcome.HANDOFF_TIMEOUT)

    @function_tool
    async def end_call(self, context: RunContext) -> None:
        """End the call early if they want to hang up, after you have said goodbye."""
        await context.wait_for_playout()
        self._s.finish(Outcome.ABANDONED)
