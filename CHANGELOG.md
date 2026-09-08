# Changelog

All notable changes to this project are documented here. The project follows
Semantic Versioning.

## Unreleased

- Require bearer authentication for Streamable HTTP and protect dashboard
  state with a private fragment-delivered token.
- Authenticate the managed Java bridge with an installation-scoped token and
  HMAC identity challenge, support safe local bridge reuse, and honor the
  configured bridge host and port.
- Add a real AR System login/logout preflight probe, strict SQL function
  allowlisting, shared form-read rate limiting, qualification/sort field
  enforcement, bounded HTTP streaming, and private credential-file checks.
- Preserve `outcome_unknown` when Helix succeeds but local result persistence
  fails, and tighten terminal plan-state invariants.
- Parse quoted AR qualification tokens, remove `unsafe-inline` from the
  dashboard CSP, enforce an 80% CI coverage floor, pin CI actions by commit,
  and publish and verify GitHub build provenance for managed updates.

## 0.7.2 - 2026-09-07

- Preserve the resolved GitHub CLI path in persistent dashboard supervisors so
  release checks and transactional updates continue to work when systemd or
  current-user startup uses a restricted process environment.

## 0.7.1 - 2026-09-07

- Keep the local dashboard available across supported user-session and WSL
  restarts with a no-admin systemd user service on Linux/WSL, current-user
  startup on Windows, and a detached supervisor fallback.
- Add a stable dashboard launcher, managed port metadata, crash recovery,
  runtime health identity, and visible dashboard-service status.
- Switch and verify both MCP and dashboard launchers during transactional
  updates, restoring the previous managed dashboard when an update fails.
- Migrate schema-v1 managed installations without interrupting an already
  running dashboard.

## 0.7.0 - 2026-09-07

- Add a stable client-facing launcher and managed installation metadata.
- Install verified GitHub releases into isolated versioned runtimes while
  retaining the previous runtime and a configuration/state backup for rollback.
- Rebuild the Java AR API bridge and run package, dependency, setup, and
  readiness checks before activating a new runtime.
- Add dashboard controls to check for and explicitly confirm server updates,
  with a detached worker that relaunches the dashboard afterward.
- Preserve and reload compatible OpenClaw MCP definitions during updates;
  generic MCP clients use the new runtime on their next server session.
- Detect and register OpenClaw automatically during guided setup while keeping
  an explicit standalone mode.
- Reload an OpenClaw-managed MCP runtime after dashboard configuration saves;
  standalone clients continue to receive an explicit restart-required result.
- Refresh OpenClaw's complete public tool filter during managed updates.

## 0.6.8 - 2026-08-28

- Add a complete release-wheel installation path with checksum verification,
  credential setup, expected timings, and MCP client startup.
- Add installation troubleshooting and an evidence-based compatibility
  matrix.
- Expand `helix-mcp-setup --help` without changing setup behavior.

## 0.6.7 - 2026-08-28

- Prevent the opt-in live E2E from issuing an unqualified record query when
  only an approved entry ID is configured.
- Require a live record selector and verify that queried and directly read
  entry IDs match.

## 0.6.6 - 2026-08-28

- Convert public documentation and user-facing metadata to English.
- Remove environment-specific evidence and internal implementation history
  from the public documentation surface.
- Replace environment-specific sample configuration and fixtures with
  fictional examples.
- Correct MCP metadata for reviewable write planning and execution.
- Keep strict type checking compatible across supported `types-PyYAML` stub
  revisions.
- Add a loopback-only administrative dashboard for validated configuration,
  write-only credential replacement, guided per-environment policy editing,
  and sanitized readiness checks. DEV, QA, and PROD own fixed policies that
  start read-only and can independently enable controlled writes. Guided setup
  launches or safely reuses the dashboard as a detached process so
  configuration does not depend on the MCP process lifecycle.
- Keep form reads enabled in dashboard-managed policies, place optional SQL
  activation with its object permissions, and enforce human approval plus a
  non-empty reason for every controlled write.

## 0.6.5 - 2026-08-10

- Normalize every file inside the wheel to mode `0644` without recompressing
  content, making builds reproducible across Linux, Windows, and WSL mounts.
- Verify that source trees with different file modes produce byte-identical
  wheels.

## 0.6.4 - 2026-08-09

- Support AR API metadata containing duplicate field names with distinct field
  IDs.
- Preserve the field ID as the authoritative identity and trim surrounding
  whitespace from names.
- Return `FORM_FIELD_AMBIGUOUS` when an operation attempts to resolve an
  ambiguous field by name.
- Map AR error 286 to `FORM_FIELD_NOT_QUERYABLE` without exposing free-form
  server text.

## 0.6.3 - 2026-08-03

- Define SQL exclusively through AR API and remove direct-database capability
  claims from the MCP contract.
- Make opt-in live read validation reproducible from a release wheel.
- Normalize closed boolean values returned by AR API column metadata.

## 0.6.2 - 2026-08-03

- Allow `COUNT(*) AS alias` while continuing to reject output wildcards.
- Return `DATABASE_QUERY_WILDCARD_NOT_ALLOWED` with a sanitized suggestion.

## 0.6.1 - 2026-08-03

- Distinguish missing SQL aliases from duplicate or unsafe aliases through
  `DATABASE_QUERY_ALIAS_REQUIRED` and `DATABASE_QUERY_ALIAS_INVALID`.
- Add static remediation hints without returning rejected SQL.

## 0.6.0 - 2026-08-03

- Route SQL and database metadata through `ARServerUser.getListSQL()` instead
  of a direct database connection.
- Reuse the selected environment's Helix credential for SQL.
- Return `ARAPI_ADMIN_REQUIRED` only for operations that need administrator
  access.
- Preserve planning, approval, encrypted persistence, allowlists, bounds, rate
  limiting, and explicit environment selection.
- Require explicit unique aliases and reject output wildcards, placeholders,
  comments, multiple statements, and non-read-only SQL.
- Validate positional AR API rows against the approved output aliases.

## 0.5.1 - 2026-08-03

- Add authoritative server UTC time and remaining lifetime to SQL plans.
- Require execution of the exact pending plan reviewed by the user.

## 0.5.0 - 2026-08-03

- Replace direct SQL execution with the persistent
  `plan_sql_query -> review -> execute_sql_query` workflow.
- Add SQL plan inspection and cancellation.
- Encrypt persisted SQL plans with the same AES-256-GCM store used by write
  plans.
- Prevent reuse of executed, cancelled, expired, or digest-mismatched plans.
- Recover an interrupted read plan as pending because an approved read can be
  retried safely.

## 0.4.1 - 2026-08-02

- Map AR API error 303 to `FORM_NOT_FOUND`.
- Return at most three policy-visible form suggestions to the requesting MCP
  client without writing them to logs.

## 0.4.0 - 2026-08-02

- Adopt the MIT License, security policy, contribution guide, and reproducible
  GitHub release workflow.
- Add bounded local metrics, stable error codes, rotated operational logging,
  and asynchronous `operation_id` correlation.
- Add optional AES-256-GCM persistence for write plans with restrictive file
  permissions and tamper detection.
- Add a reproducible MCP-over-stdio E2E using an in-memory Java AR API double.
- Add isolated Java bridge tests and a real Python-to-Java loopback contract.
- Enforce bounded read and approved-write policy examples for DEV, QA, and
  PROD.

## 0.3.0 - 2026-08-02

- Package the Java bridge source and generic configuration template in the
  wheel without proprietary BMC libraries.
- Add guided setup, bridge compilation, sanitized preflight, and managed bridge
  lifecycle.
- Add form discovery, field metadata, bounded queries, direct reads, health,
  and two-phase create/update workflows through AR API.

## Earlier development

- Establish the Python MCP server, explicit environment targeting, policy
  enforcement, audit, tests, and local AR API integration model.
