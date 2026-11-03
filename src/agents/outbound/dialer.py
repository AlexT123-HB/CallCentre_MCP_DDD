"""Place the SIP call and turn LiveKit/Twilio errors into a plain result."""

from __future__ import annotations

from dataclasses import dataclass

from google.protobuf.duration_pb2 import Duration
from livekit import api


@dataclass(frozen=True)
class DialResult:
    answered: bool
    sip_status_code: int | None = None
    sip_status: str | None = None


async def dial(
    lk: api.LiveKitAPI,
    *,
    room: str,
    trunk_id: str,
    to: str,
    identity: str,
    ringing_timeout_s: int = 30,
    max_call_s: int = 900,
) -> DialResult:
    try:
        await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=to,
                room_name=room,
                participant_identity=identity,
                hide_phone_number=True,  # keep the number out of participant attributes
                wait_until_answered=True,
                ringing_timeout=Duration(seconds=ringing_timeout_s),
                max_call_duration=Duration(seconds=max_call_s),
            )
        )
        return DialResult(answered=True)
    except api.TwirpError as e:
        md = e.metadata or {}
        code = md.get("sip_status_code")
        return DialResult(
            answered=False,
            sip_status_code=int(code) if code and code.isdigit() else None,
            sip_status=md.get("sip_status") or e.message,
        )
