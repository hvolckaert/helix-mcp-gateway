# Python package

Package responsibilities:

- `config`: safe configuration loading and validation;
- `secrets`: credential resolution through opaque references;
- `targeting`: immutable DEV, QA, and PROD resolution;
- `clients`: AR API adapter and local Java bridge management;
- `services`: MCP-independent application use cases;
- `security`: policy enforcement, SQL validation, masking, and approvals;
- `tools`: MCP tool adapters;
- `resources`: read-only MCP context;
- `models`: shared internal and public contracts;
- `observability`: logging, audit, metrics, and traces.

The intended dependency direction is documented in
[`docs/module-boundaries.md`](../../docs/module-boundaries.md). No module may
introduce mutable global target configuration.
