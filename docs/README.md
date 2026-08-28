# Documentation

This directory documents the architecture, security model, operation, and
release lifecycle of Helix MCP Gateway. Start with the project
[`README.md`](../README.md) for the value proposition and requirements.

## Installation and operation

- [`installation.md`](installation.md): wheel installation, per-user setup,
  and local bridge compilation;
- [`configuration-loading.md`](configuration-loading.md): safe YAML and
  `.env` loading;
- [`operations.md`](operations.md): preflight, startup, shutdown, and recovery.

## Architecture and contracts

- [`single-instance.md`](single-instance.md): one logical installation and
  fixed DEV, QA, and PROD environments;
- [`targeting.md`](targeting.md): explicit environment selection;
- [`module-boundaries.md`](module-boundaries.md): Python module responsibilities
  and dependency direction;
- [`mcp-tools.md`](mcp-tools.md): MCP transport, lifecycle, and tool catalog;
- [`form-query-service.md`](form-query-service.md): bounded form and metadata
  reads;
- [`form-writes.md`](form-writes.md): human-approved create and update flows;
- [`sql.md`](sql.md): read-only SQL through AR API;
- [`credentials.md`](credentials.md): secret providers and credential lifecycle;
- [`observability.md`](observability.md): logs, audit, metrics, and public errors.

## Development and release

- [`development.md`](development.md): local development, validation, and CI;
- [`stdio-e2e.md`](stdio-e2e.md): reproducible and opt-in live E2E tests;
- [`releasing.md`](releasing.md): release preparation and verification.

The `adr/` directory is reserved for future architecture decision records.
