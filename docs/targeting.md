# Explicit environment targeting

## Primary rule

Every environment-specific operation receives `environment`. There is no
active target, inherited value, or default environment.

The only accepted values are `dev`, `qa`, and `prod`. The internal logical
installation name `helix` is not part of the MCP tool contract.

## Registry

`TargetRegistry` is built once from validated configuration. Each entry binds
an environment to its policy, AR API backend, operational state, and limits.

The public target view includes environment, enabled state, backends, and
capabilities. It excludes hosts, URLs, ports, secret references, and complete
policy configuration.

## Resolution

`TargetResolver` handles each call without mutable session state:

1. require the environment;
2. validate it as DEV, QA, or PROD;
3. find the exact registry entry;
4. reject disabled targets;
5. validate the requested backend;
6. return immutable configuration and policy bindings.

When no backend is requested, the result keeps `backend=None` so the service
can choose the correct backend later. It does not imply a default environment.

Stable errors include `TARGET_SELECTION_REQUIRED`, `INVALID_ENVIRONMENT`,
`INVALID_BACKEND`, `TARGET_NOT_FOUND`, `TARGET_DISABLED`, and
`BACKEND_UNAVAILABLE`.
