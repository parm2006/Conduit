# Two-Client Multi-Monitor Conduit

**Source roadmap item:** N/A — direct product request
**Source improvement plan:** N/A
**Planned at:** 2026-08-24, `main` at `e2c191cb6591ec8cc666058f393ca95932edb279`
**Status:** Outlining
**Current gate:** Review the seven implementation slices in the structure outline before executor plans are written.

## Purpose

Expand Conduit from one Server/one Client/one configured edge into a bounded three-PC cluster with automatic physical-monitor discovery, a compact Server-only topology editor, Server-owned graph input routing, one global newest clipboard item, and Server-relayed Explorer paste.

## What Better Means

The Server can keep two Clients connected through the same secured three-port architecture; every physical display is represented accurately; only validated, acknowledged topology becomes active; input is never left logically held during transitions; copy remains non-blocking; file bytes move only on paste; and a one-Client setup, firewall scope, trust model, and current clipboard behavior do not regress.

## Artifact Index

| Artifact | Status | Purpose | Notes |
|---|---|---|---|
| [Accepted design](../../superpowers/specs/2026-08-24-multi-client-topology-design.md) | Accepted | Product behavior and architecture | Includes review clarifications through 2026-08-24 |
| [001 structure outline](001-structure-outline.md) | In review | Define independently landable vertical slices | Review phase boundaries and validation |

## Current Shape

- One fixed Server hub and at most two ready Client sessions.
- Physical displays are immutable per-machine groups; machine groups form a validated graph.
- Old active behavior continues during editing; Apply uses an acknowledged cluster barrier and rollback.
- Server-owned cursor routing, global latest clipboard state, and file jobs are separate services over session-owned lanes.
- Seven landable slices take the feature from single-Client topology through physical three-PC acceptance.

## Accepted Decisions

- Keep the existing three TCP ports and firewall scope; retain multiple session-owned sockets per listener.
- Require a successful Apply for every new, returning, or replacement Client before routing.
- Preserve one shared password plus per-device fingerprint pairing.
- Give a third authenticated candidate one purple 15-second replacement window, never a third active slot.
- Pause clipboard delivery during valid Apply but keep local capture and latest-wins submission non-blocking.

## Open Gates

- Accept or revise the seven phase boundaries in `001-structure-outline.md`.

## Implementation Routing

After outline acceptance, write one self-contained, independently landable executor plan per phase. Each plan will carry exact drift checks, source excerpts, focused tests, the full landing gate, and phase-specific STOP conditions.

## Rejected or Deferred

| Item | Reason | Revisit if |
|---|---|---|
| More than two Clients | Explicit release bound keeps state, UI, and resource limits understandable | A later product cycle requests a larger cluster |
| Direct Client-to-Client sockets | The Server is the trust, routing, and clipboard authority | The hub becomes a measured throughput bottleneck |
| Per-Client passwords | Rejected setup complexity; pairing remains per device | The trust model changes |
| Clipboard history or disk persistence | Only the newest in-memory item is required | A separate history feature is designed |
