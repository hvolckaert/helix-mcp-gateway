# Python module boundaries

The package separates MCP transport, configuration, credentials, policy, and
BMC integration so each layer can be tested with controlled substitutes.

## Composition modules

- `helix_mcp.bootstrap` builds the dependency graph and is the only module
  allowed to know every concrete implementation.
- `helix_mcp.lifecycle` opens and closes process resources, managed bridge
  clients, caches, and observability sinks.
- `helix_mcp.server` configures MCP, registers tools and resources, and selects
  the transport. It contains no Helix business rules.

## Functional modules

| Module | Responsibility |
| --- | --- |
| `config` | Load and validate non-sensitive configuration. |
| `secrets` | Resolve credentials through opaque references. |
| `targeting` | Resolve immutable DEV, QA, and PROD targets. |
| `clients` | Communicate with the local AR API bridge. |
| `services` | Implement form, SQL, metadata, and health use cases. |
| `security` | Enforce policy, SQL rules, masking, and approval. |
| `tools` | Adapt MCP schemas to application services. |
| `resources` | Publish side-effect-free MCP context. |
| `models` | Define shared input, output, and error contracts. |
| `observability` | Produce logs, audit, metrics, and traces. |

## Dependency direction

```text
tools -> services -> clients -> BMC Helix
```

Additional rules:

1. `server` may depend on `bootstrap`, `tools`, and `resources`.
2. `tools` and `resources` may depend on `services` and `models`.
3. `services` may depend on `clients`, `security`, `targeting`, and `models`.
4. `clients` may depend on `secrets`, `targeting`, and `models`.
5. `security` may depend on `targeting` and `models`, but not on `tools`.
6. `config`, `secrets`, `targeting`, and `models` do not depend on MCP.
7. Clients never import `server`, `tools`, `resources`, or `services`.

Targets are immutable, secrets remain references until needed, clients return
project models rather than external-library objects, and audit receives only
sanitized events.
