# RETIRE.md — Dionysus retire-readiness

Dionysus is being converged to a **stateless, contract-true content worker** for
the Mise Solo Studio OS: it drafts campaign packs, gallery descriptions,
captions/alt text, print-pitch enrichment, and email/social copy, and returns
them as **reversible drafts a human accepts in Mise**. Mise owns all identity,
billing, and business state, plus the review/accept workflow.

This document is the map for getting there — and for fully retiring Dionysus if
its drafts can instead be produced by the local content endpoint Mise already
calls. Keeping it current keeps the decision **reversible**.

> Decision (2026-06): **keep Dionysus as a thin stateless worker** (the recipes
> + contract surface + local-model call are the engine worth preserving), strip
> the SaaS/business-state layer in phases, and keep this file so full absorption
> into Mise stays a one-step option.

## Where the worker contract already stands

Shipped: structured-JSON draft envelope + provenance/cost (`app/contract.py`),
configurable **local** model endpoint with deterministic fallback
(`app/model_client.py`), human-edit guard (`content_packs.human_edited`,
surfaced to Mise), idempotent `argus-pack` + `correlation_id` echo, `/healthz`,
and mock-only CI. What remains for "retire-ready" is **statelessness**: removing
the authoritative business/identity state below.

## State inventory (13 tables)

**Authoritative business / identity — Mise owns; strip from Dionysus**
- `users` — accounts / passwords (SaaS auth).
- `organizations` — tenant identity, plan, `access_token`, brand profile. The
  worker needs only a lightweight **subject** reference (slug + the few
  generation inputs: audience, brand_voice), ideally supplied per request.
- `organization_members` — membership / roles.
- `workspace_invites` — invite flow.
- `subscriptions` — billing state (mirrors Stripe).
- `audit_events` — business audit log (Mise owns the accept/audit trail).

**Business inputs — should arrive in the request, not be owned here**
- `campaigns` — in the worker model this is just a correlation/unit-of-work
  handle (already carries `argus_run_id` + `correlation_id`).
- `menu_items` — restaurant menu inputs; belong in the request payload.

**Engine config — keep**
- `content_recipes` — draft kinds + channel/deliverable scaffolding (the IP).

**Run cache / operational — keep, but treat as cache, not source of truth**
- `content_packs` — the drafts. Per the contract these are a **run cache** keyed
  for idempotency, not authoritative content (Mise persists what a human
  accepts). Candidate for TTL/pruning.
- `jobs` — async draft queue.
- `rate_limit_events` — only the Mise-bearer-token limiter survives once SaaS
  auth is gone.
- `schema_migrations` — bookkeeping.

## Module / route inventory

**Worker core — keep**
`config`, `db`, `jobs`, `recipes`, `generator`, `model_client`, `contract`,
`mise_hook`, `print_pitch`, `packs` (read/render), `argus`, `readiness` (trim to
studio checks), `security` (keep `require_mise_token` only), `rate_limit` (keep
the mise-token limiter), `cli`/`backups` (trim).
Routes: `/healthz`, `/readiness`, and the bearer-gated
`/api/mise/organizations/{slug}/…` surface (`print-pitch`, `argus-pack`,
`packs`, `latest-pack`, `jobs/{id}`).

**SaaS surfaces — strip (already dark in `DIONYSUS_STUDIO_MODE=true`)**
`billing`, `plans`, `audit`, `seed` (demo workspace), `studio_gate` (unneeded
once the SaaS routes are gone); session/CSRF/password parts of `security`; the
`/`, `/login`, `/logout`, `/signup`, `/invite/*`, `/w/*`, `/share/*`,
`/pricing`, `/stripe/webhook` routes in `main`; templates `login`, `pricing`,
`billing`, `workspace`, `settings`, `support`, `accept_invite`, `shared_pack`,
`audit_event`; the Stripe config + price IDs.

## Phased strip plan (each phase = one independently-green draft PR)

1. ✅ **Billing / Stripe** *(done)* — removed the Stripe payment integration
   (`/stripe/webhook`, checkout), Stripe config, the `stripe` dep, billing/pricing
   templates and readiness checks. `billing.py` kept only `checkout_state` +
   `sync_trial_subscription` (local plan); `subscriptions` table retained for now.
2. ✅ **SaaS UI + auth** *(done)* — removed the signup/login/logout/invite/
   workspace/settings/support/share/pricing routes and templates, `studio_gate`,
   session/CSRF/password code (`security` trimmed to the bearer token), and
   dropped the `users` / `organization_members` / `workspace_invites` tables.
   `seed.py` is now CLI subject-provisioning. The human-edit setter moved to a
   bearer-gated Mise endpoint (`POST …/packs/{id}/human-edited`).
3. **Audit log** — remove `audit_events` + `audit.py` (Mise owns the audit
   trail); keep lightweight structured run logs. (`audit_events` lost its `users`
   FK in phase 2; the only remaining writer is the unreachable regenerate path.)
4. **De-own business inputs** — accept menu/brand inputs in the request payload;
   reduce `organizations` to a minimal subject record; treat `campaigns` purely
   as a correlation handle. Removing `STUDIO_MODE` + `billing.py`/`plans.py`/
   `subscriptions` belongs here (now only used by `mise_hook` recipe selection).
5. **Run-cache hygiene** — document/prune `content_packs` as a TTL'd cache, not a
   store of record.

Each phase keeps backward-compatible defaults and stays behind the existing
studio-mode posture so nothing in Mise's path changes unexpectedly.

## Full-retirement alternative (absorb into Mise)

Because the real surface is small, full retirement is viable: Mise would take
over by (a) calling its **local content endpoint** with a prompt built from the
**recipes** (port `content_recipes` + `generator._model_messages`), and (b)
emitting the **contract envelope** (`app/contract.py`) into its own `ai_runs`
ledger. The print-pitch and argus-keyword enrichment helpers
(`print_pitch.py`, `argus.summarize_export`) port directly. Choose this over
keeping a thin worker only if running a second process isn't worth the engine
boundary; this file is the checklist for that move.

## Reversibility

Every phase is a separate PR a human merges; revert is `git revert`. The SaaS
layer is already inert under `DIONYSUS_STUDIO_MODE=true` (the default), so the
strip removes dead-in-production code rather than changing live behavior.
