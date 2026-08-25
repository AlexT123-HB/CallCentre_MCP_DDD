# HeartBug MCP Call Center

HeartBug is a healthcare call-center assistant built as a FastAPI host that routes caller requests to a set of MCP-backed domain servers.

The host is in `src/client`, and the domain services live under `src/servers`. Each domain exposes a manifest describing its tools and behavior, so the host can discover capabilities instead of hardcoding them.

## What this project does

- handles inbound call starts via `/webhook`
- routes turns to the right domain using the host + domain manifests
- verifies callers through a verification domain
- supports patient, order, doctor, and knowledge-base operations
- persists session/cache state in Redis when configured
- runs the different services together with Docker Compose

## Current architecture

```text
src/
├── client/      # FastAPI host + routing, planning, orchestration, narration
├── servers/     # independent MCP domains: verification, patient, order, kb, doctor
├── shared/      # common domain manifests, security, cache, database, logging, tracing
├── __init__.py
└── ...
```

### Host

The host is the API entry point and the orchestration layer.

- `src/client/app.py` exposes the HTTP endpoints
- `src/client/host.py` performs the turn flow and tool orchestration
- `src/client/mcp_client.py` connects to domain MCP servers
- `src/client/gate.py` checks what domains are allowed for the current caller state
- `src/client/router.py` and provider code decide routing/tool selection
- `src/client/narration.py` turns structured results into spoken output

### Domains

The main domain servers in this repo are:

- `verification` — verifies callers using identity checks
- `patient` — returns the caller's patient record
- `order` — lists/lookups order status and handles order flows
- `kb` — knowledge-base answers for general questions
- `doctor` — doctor-facing patient status and report workflows

Each domain has a `manifest.py` file that declares its endpoint, tools, events, and required arguments.

## HTTP API

The app starts with:

```bash
uvicorn src.client.app:app --host 0.0.0.0 --port 8000
```

Endpoints:

- `POST /webhook` with body:
  ```json
  {"call_id": "TEST-123", "ani": "+61498765432"}
  ```
  Starts a call and returns the greeting/verification setup

- `POST /{domain}` with body:
  ```json
  {"call_id": "TEST-123", "transcript": "I need my order status"}
  ```
  Sends one turn to the host; the path is a hint, and the host decides the actual domain

- `GET /` returns a health check payload:
  ```json
  {"status": "ok"}
  ```

## Docker setup

The repo includes a Docker Compose stack for the whole system.

```bash
export OPENAI_API_KEY=...
docker compose up --build -d
```

This brings up:

- Redis
- verification service on port 3001
- patient service on port 3002
- order service on port 3003
- kb service on port 3004
- doctor service on port 3005
- host on port 8000

Useful commands:

```bash
docker compose logs -f host
docker compose logs -f verification
docker compose down
```

## Local run (without Docker)

Install dependencies:

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=...
```

Then start the five domain servers and host in separate terminals:

```bash
VERIFICATION_PORT=3001 python -m src.servers.verification.run
PATIENT_PORT=3002 python -m src.servers.patient.run
ORDER_PORT=3003 python -m src.servers.order.run
KB_PORT=3004 python -m src.servers.kb.run
DOCTOR_PORT=3005 python -m src.servers.doctor.run
uvicorn src.client.app:app --port 8000
```

## Redis / session state

Redis is used for session persistence and caching when `REDIS_URL` is configured. Without it, the app falls back to in-process state.

The Compose file configures service-specific Redis users and wired URLs for each domain and host.

## Environment variables

Common values used by the project:

- `OPENAI_API_KEY` — required for the LLM-backed provider
- `REDIS_URL` — enables Redis-backed session/cache storage
- `HEARTBUG_TRACING` — toggles tracing
- `MLFLOW_TRACKING_URI` — MLflow destination for traces
- domain URLs like `VERIFICATION_URL`, `PATIENT_URL`, `ORDER_URL`, `KB_URL`, `DOCTOR_URL`

## Development notes

This project follows a self-describing domain model:

- each domain exposes a `manifest`
- the host learns about available tools and flows from those manifests
- security and identity checks are enforced at the domain layer as well as in the host
- tool writes are constrained so the model cannot directly execute unsafe actions

## Quick smoke test

After starting the host and services, a simple test call is:

```bash
curl -X POST http://127.0.0.1:8000/webhook \
  -H 'Content-Type: application/json' \
  -d '{"call_id":"TEST-123","ani":"+61498765432"}'
```

Then send a turn:

```bash
curl -X POST http://127.0.0.1:8000/verification \
  -H 'Content-Type: application/json' \
  -d '{"call_id":"TEST-123","transcript":"Hi, my name is Alex Tran and my date of birth is 1988-01-02"}'
```

## Repo status

This repository is currently a working MCP-based call-center orchestration project with multiple domain services, Docker packaging, Redis-backed state, and a FastAPI host. The README is intentionally written to reflect the actual run path and service layout used by the code in this workspace.
