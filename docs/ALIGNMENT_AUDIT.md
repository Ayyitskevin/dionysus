# Dionysus → "Mise Solo Studio OS" Alignment Audit

Dionysus must become a **stateless, contract-true content worker** for Mise:
campaign packs, gallery descriptions, captions/alt text, print-pitch enrichment,
and blog/email/social drafts. Mise owns all business state and the human
review/accept workflow. Dionysus must never auto-publish, never clobber a
human-edited body, and write nothing on failure. Generation should call a
configurable **local** model endpoint (no hard cloud dependency).

## What Dionysus is today

A FastAPI + HTMX + SQLite app with two modes:

- `DIONYSUS_STUDIO_MODE=true` (default) — `app/studio_gate.py` 404s the SaaS UI
  and exposes only bearer-gated Mise APIs (`print-pitch`, `argus-pack`, `packs`,
  `latest-pack`, `jobs/{id}`).
- `DIONYSUS_STUDIO_MODE=false` — the legacy "Platekit" SaaS: signup, login,
  Stripe billing, workspaces, invites, member roles, audit log.

The "AI engine" currently contains **no model**: `app/generator.py` and
`app/print_pitch.py` are deterministic string templates. Dionysus also owns
authoritative business state that the contract says belongs to Mise
(`organizations`, `users`, `organization_members`, `workspace_invites`,
subscriptions/Stripe, `campaigns`, `menu_items`, `content_packs`,
`audit_events`).

## Gap analysis vs the 7-point Worker Contract

| # | Contract point | Status | Notes |
|---|---|---|---|
| 1 | Structured JSON `{drafts[],model,cost_usd}` | Missing → **addressed by this PR** | Output was bespoke per endpoint; no `drafts[]`, no `alt_text`, no top-level `model`/`cost_usd`. |
| 2 | Provenance + cost (`model`, `latency_ms`, `cost_usd`) | Partial → **addressed by this PR** | `provenance.engine` + `content_packs.ai_model` existed; no latency/cost reported. |
| 3 | Callback/API (token, echo `correlation_id`, unknown subject = no-op) | Partial | Token auth solid (`security.require_mise_token`); `correlation_id` never echoed; unknown org → 404 not a no-op. |
| 4 | Idempotency (retry ≠ duplicate) | Partial | Strong job-level idempotency while queued/running; a retry **after** a job completes re-creates a campaign/job/pack. |
| 5 | Stateless / retire-ready (run cache only; strip SaaS; `RETIRE.md`) | Largest gap | Full identity/billing/business state; STUDIO_MODE only hides routes. No `RETIRE.md`. |
| 6 | Draft-only + human-edit guard | Partial (behaviorally OK, not enforced) | Never auto-publishes; failure rolls back. But no `human_edited` flag / explicit clobber guard — protection is incidental (always-insert). |
| 7 | Resilience & CI (`/healthz`, mock-only CI, no live calls) | Partial | `/healthz` exists; Argus failures fall back. **No CI** enforcing "no live model calls". |

## Decision: keep as a thin stateless worker

The unique value in Dionysus is (a) the domain **recipes / prompt scaffolding**
for these draft kinds and Argus-keyword enrichment, and (b) the Mise **contract
surface** (bearer auth, idempotent jobs, draft posture). Everything else
duplicates Mise's responsibilities. The chosen direction is to **converge
Dionysus to a thin stateless content worker** — local model + recipes as prompt
scaffolding, run-cache only — and strip the SaaS/business-state layer over
chunked PRs, with a `RETIRE.md` keeping full absorption into Mise reversible.
("Consolidate the chassis, not the engines.")

## Ranked plan (small, independently-green, backward-compatible draft PRs)

1. **Structured draft contract + provenance/cost** *(P1, P2)* — **this PR**.
2. **Configurable LOCAL model client** *(P1)* — OpenAI-compatible
   `/v1/chat/completions`; deterministic templates remain the fallback + CI path.
3. **Enforce draft-only + explicit human-edit guard** *(P2)* — `human_edited`
   flag + a clobber guard on any body-write path; tests for no-publish /
   no-clobber / failure-writes-nothing.
4. **Idempotency stability + `correlation_id` echo + unknown-subject no-op** *(P3)*.
5. **Mock-only CI + `/healthz` contract test** *(P5, P7)*.
6. **Statelessness / retire path** *(P4)* — `RETIRE.md` + stateless flag, then
   strip SaaS subsystems one PR at a time.

## Proposed model/endpoint config (local-first, provider-neutral)

```bash
DIONYSUS_MODEL_ENDPOINT=        # e.g. http://localhost:11434/v1 (Ollama/llama.cpp/vLLM)
DIONYSUS_MODEL_NAME=            # the local model Mise uses for captions
DIONYSUS_MODEL_API_KEY=        # optional bearer for the local endpoint
DIONYSUS_MODEL_TIMEOUT=30
# enabled := bool(DIONYSUS_MODEL_ENDPOINT and DIONYSUS_MODEL_NAME)
```

Empty endpoint → deterministic fallback (current behaviour, `cost_usd=0.0`,
no live calls in CI).

## What this PR (P1) ships

A canonical Worker-Contract envelope, exposed **additively** (existing response
fields are unchanged):

```json
{"drafts": [{"kind": "caption|gallery_description|campaign_pack|email|social",
             "title": "optional", "body": "draft text", "alt_text": "optional"}],
 "model": "dionysus-local-draft", "latency_ms": 0, "cost_usd": 0.0}
```

- `app/contract.py` — envelope builder, pack/print-pitch → drafts mappers, and a
  dependency-free validator (`validate_envelope` / `validate_draft`).
- `app/main.py` — `print-pitch` (measured `latency_ms`) and the pack read
  payload (`packs` / `latest-pack`) now include a `contract` envelope. Local
  drafting reports `cost_usd=0.0` and `model="dionysus-local-draft"` (or the
  pack's stored engine).
- `tests/test_contract.py` — mapping + schema-validation + endpoint tests.

`latency_ms`/`cost_usd` become meaningful once the local model client lands
(PR2); cached pack reads report `latency_ms=0`.
