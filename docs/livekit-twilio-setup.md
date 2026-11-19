# LiveKit + Twilio Setup — HeartBug Call Centre

Nov 18, 2026 

## Overview

HeartBug places outbound callbacks through Twilio: Twilio carries the phone leg, LiveKit runs the outbound voice agent, and the HeartBug host checks the callee's identity and holds the reason for the call.

- **Outbound (callbacks):** when a caller chooses to talk to someone **now**, the host dispatches a LiveKit job → the `outbound-worker` dials through the LiveKit outbound SIP trunk → Twilio Elastic SIP Trunk → the callee's phone. The agent asks for name and date of birth, the host checks them (`/outbound/callbacks/{id}/verify`) and only then returns the reason, which the agent tells the callee. The worker reports the result to `/outbound/callbacks/{id}/outcome`.
- **Inbound (clinics calling in):** the Twilio number's voice webhook hits the host (`/twilio/voice`, `/twilio/turn`). Twilio does STT (`<Gather>`) and TTS (`<Say>`); no LiveKit, no agent (!!! **Not implemented yet**)

```mermaid
flowchart LR
    host["HeartBug host<br/>checks name + DOB<br/>holds the reason<br/>logs the outcome"]
    worker["LiveKit worker<br/>STT, LLM, TTS"]
    lk["LiveKit SIP<br/>outbound trunk"]
    tw["Twilio trunk<br/>Elastic SIP Trunk"]
    callee["Callee phone<br/>patient or clinic"]
    num["Twilio number<br/>+61 2 5300 5369"]
    clinic["Clinic phone"]
    host <-- "dispatch, /verify" --> worker
    worker -- dial --> lk
    lk -- "SIP + auth" --> tw
    tw <-- "phone call" --> callee
    clinic -- "phone call" --> num
    num <-. "webhooks /twilio/voice, /turn<br/>(not implemented yet)" .-> host
```

LiveKit carries the audio and runs the agent; the reason for the call never leaves the host until the callee is verified.

Current values (no secrets):

| Item | Value |
| --- | --- |
| LiveKit Cloud project | `hboutbound`, region Australia |
| LiveKit URL | `wss://hboutbound-7gwyybl5.livekit.cloud` |
| LiveKit outbound trunk ID | `ST_eEUzK3phBRWD` |
| Agent name (dispatch) | `heartbug-outbound` |
| Caller ID / Twilio number | `+61 2 5300 5369` |
| Twilio trunk type | Elastic SIP Trunking |

## Accounts and prerequisites

Outbound calling runs on a Twilio trial account, a LiveKit Cloud project, an OpenAI key and the HeartBug Docker stack.

| Need | Used for | Ours |
| --- | --- | --- |
| Twilio account (trial) | The caller ID number and the Elastic SIP Trunk | Trial number `+61 2 5300 5369` |
| LiveKit Cloud project | Rooms, the SIP bridge and agent dispatch | `hboutbound`, Australia region |
| OpenAI API key | Worker STT, LLM and TTS | Same key as the host |
| Docker + the HeartBug repo | Runs `host`, `redis` and `outbound-worker` | `docker compose up -d` |

*The following setup is for outbound calls only. Inbound calls setup can be a bit different.*

## Step 1 — Twilio number

The callbacks use a Twilio **trial** account. Its number, `+61 2 5300 5369` (Sydney local), is the caller ID on every outbound call.

## Step 2 — Twilio Elastic SIP Trunk

LiveKit reaches the phone network through a Twilio Elastic SIP Trunk. Two settings on it matter:

1. **Termination SIP URI** (Termination tab), `<trunk-name>.pstn.twilio.com`. It goes into the LiveKit trunk as `address`.
2. **Credential list** (Termination → Authentication), a username and password. The same pair goes into the LiveKit trunk.

> The credential list's username and password must match the LiveKit trunk's `auth_username` / `auth_password` **exactly**. Our first trunk returned SIP 403 for a wrong username and had to be recreated.

## Step 3 — LiveKit project and outbound trunk

LiveKit Cloud project `hboutbound` (Australia) holds the API key the host and worker use, and one outbound SIP trunk pointing at the Twilio trunk.

| Trunk field | Value |
| --- | --- |
| `address` | The Twilio termination SIP URI from Step 2 |
| `numbers` | `+61253005369` (caller ID, E.164) |
| Auth username / password | The Twilio credential list pair |
| Trunk ID | `ST_eEUzK3phBRWD`, set as `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` |

## Step 4 — HeartBug configuration

All services read the one `.env` through `x-common`, so the LiveKit and outbound values go there once. Never commit `.env`.

**`.env` additions**

| Variable | Used by | Value |
| --- | --- | --- |
| `LIVEKIT_URL` | host, worker | `wss://hboutbound-7gwyybl5.livekit.cloud` |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | host, worker | from Step 3 |
| `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` | worker | `ST_eEUzK3phBRWD` |
| `OUTBOUND_API_TOKEN` | host, worker | long random string; the host mounts `/outbound/*` only when set |
| `OUTBOUND_DEMO_TO_NUMBER` | host (optional) | E.164 number every callback dials, for demos |
| `OPENAI_API_KEY` | worker | already set for the host |

**`docker-compose.yml` — add the worker**

```yaml
  outbound-worker:
    <<: *common
    command: python -m src.agents.outbound.agent start
    environment:
      <<: *common-env
      HOST_BASE_URL: http://host:8000
      REDIS_URL: redis://outboundsvc:outbound-secret@redis:6379/0
    depends_on: [host, redis]
```

**`redis_config/users.acl` — briefing keys**

```text
user hostsvc on >host-secret ~session:* ~outbound_briefing:* +@read +@write +@keyspace +@transaction +ping
user outboundsvc on >outbound-secret ~outbound_briefing:* +@read +@write +@keyspace +ping
```

Reload without a restart: `docker compose exec redis redis-cli ACL LOAD` (as the default/admin user).

**`requirements.txt`**

```text
livekit-api==1.2.2
livekit-agents[openai]==1.8.10
httpx==0.28.1
```

Rebuild after changing requirements (`docker compose build`), then `docker compose up -d`. Code under `./src` is bind-mounted, so code changes only need `docker compose restart outbound-worker host`; `.env` changes need `up -d` to be re-read.

## Step 5 — Test it

Check the worker is registered, try a typed call, then a real one.

1. **Worker registered:** `docker compose logs -f outbound-worker` shows `"registered worker"` with `agent_name: heartbug-outbound` and `region: Australia`.
2. **Typed outbound call (no phone):** run the command below and type as the callee.
3. **Real outbound call:** set `OUTBOUND_DEMO_TO_NUMBER` to your mobile, talk to HeartBug as a verified patient, ask for a person, and choose **now**. Pick up, give name and DOB, and the agent tells you why it called then hung up.

```bash
docker compose exec -it host python /app/eval_outbound_typing.py --patient P003 --reason "your order O1003 being delayed"
```

## Troubleshooting

Most failures so far were credentials, ACLs or missing `await`s, not LiveKit itself. Twilio's [trunk troubleshooting page](https://www.twilio.com/docs/sip-trunking/troubleshooting) lists the SIP codes.

| Symptom | Cause | Fix |
| --- | --- | --- |
| Dial fails with SIP **403** | LiveKit trunk's auth username didn't match the Twilio credential list | Recreated the LiveKit trunk with the matching username |
| Call "answers" instantly, then a voicemail greeting | AU mobiles divert declined or unanswered calls to voicemail, which answers at SIP level | Expected; the agent detects voicemail and leaves a message |
| Agent talks before anyone picks up | Agent timer started on dial, not on answer | Worker waits for the callee to join before speaking |
| `/verify` returns 500, log says `coroutine ... was never awaited` | `call_now` called without `await` in the host's transfer branch | `await self.outbound.call_now(...)` |
| Callee gives the right details but fails verification | DOB said as "22 July 1990" but checked as ISO; or a stale test patient in the Redis cache | Agent submits DOB as `YYYY-MM-DD` and the host normalises it; clear the cached patient |
| Host logs `NOPERM` on `outbound_briefing:*` | Redis ACL lacked the key pattern | Add `~outbound_briefing:*` to `hostsvc` and `outboundsvc`, then `ACL LOAD` |
