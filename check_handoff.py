"""Check the verify → briefing handoff against THIS container's phases.py — no phone, no host.

Run:  docker compose cp check_handoff.py outbound-worker:/app/check_handoff.py
      docker compose exec outbound-worker python /app/check_handoff.py
"""
import asyncio, json, logging, sys, traceback
logging.basicConfig(level=logging.WARNING)
from livekit.agents import AgentSession, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from src.agents.outbound.phases import PreVerifyAgent, PostVerifyAgent
from src.agents.outbound.state import CallState
from src.shared.outbound_contract import (Briefing, CheckSpec, DispatchMetadata, VerifyResult, Outcome)

class ScriptedStream(llm.LLMStream):
    def __init__(self, llm_, *, chat_ctx, tools, conn_options, reply):
        super().__init__(llm_, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._reply = reply
    async def _run(self):
        kind, payload = self._reply
        if kind == "tool":
            d = llm.ChoiceDelta(role="assistant", tool_calls=[llm.FunctionToolCall(
                name=payload[0], arguments=json.dumps(payload[1]), call_id="call_1")])
        else:
            d = llm.ChoiceDelta(role="assistant", content=payload)
        self._event_ch.send_nowait(llm.ChatChunk(id="c1", delta=d))

class ScriptedLLM(llm.LLM):
    def chat(self, *, chat_ctx, tools=None, conn_options=DEFAULT_API_CONNECT_OPTIONS, **kw):
        names = {getattr(t, "id", None) for t in (tools or [])}
        items = chat_ctx.items
        last = items[-1] if items else None
        print(f"   LLM called: tools={sorted(n for n in names if n)} last={type(last).__name__}")
        if "submit_check_answer" in names and getattr(last, "type", "") == "message" and "1990" in (last.text_content or ""):
            reply = ("tool", ("submit_check_answer", {"name": "John K", "dob": "1990-07-22"}))
        elif "submit_check_answer" in names:
            reply = ("text", "Could you tell me your date of birth?")
        else:
            reply = ("text", "Thanks for confirming. I'm calling about your new device. A member of our team will follow up. Goodbye.")
        return ScriptedStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options, reply=reply)

class FakeHost:
    async def verify(self, meta, answers):
        print("   HOST verify:", answers)
        return VerifyResult(True, 'their request: "can someone help me with my new device?"')

async def main():
    meta = DispatchMetadata("cb-1", "tok")
    b = Briefing(callback_id="cb-1", to_number="+61400000000", patient_first_name="John",
                 check=CheckSpec(slots={"name": "their full name", "dob": "their date of birth, submitted as YYYY-MM-DD"}),
                 intro="I'm calling back about the request you just made.")
    state = CallState(meta, b); state.heard_callee = True
    async with AgentSession(llm=ScriptedLLM()) as session:
        await session.start(PreVerifyAgent(state, FakeHost()))
        print("1) caller gives DOB")
        try:
            res = await session.run(user_input="John K, 22 July 1990")
            for ev in res.events:
                print("   event:", type(ev).__name__, getattr(getattr(ev,'item',None),'type',''), 
                      (getattr(getattr(ev,'item',None),'output','') or '')[:200] if hasattr(getattr(ev,'item',None),'output') else '')
        except Exception as e:
            print("   RUN RAISED:", type(e).__name__, e); traceback.print_exc()
        await asyncio.sleep(1.5)
        print("2) current agent:", type(session.current_agent).__name__, "| verified:", state.verified, "| outcome:", state.outcome)

if __name__ == "__main__":
    asyncio.run(main())
