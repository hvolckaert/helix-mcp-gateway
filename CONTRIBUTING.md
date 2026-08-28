# Contributing

Thank you for contributing to Helix MCP Gateway. For a significant change,
open an issue first and describe the problem or use case.

## Repository rules

- Do not include credentials, private endpoints, organization names, client
  names, or data obtained from a Helix environment.
- Do not add BMC JARs or other proprietary binaries.
- Keep PROD strictly read-only.
- Preserve planning, human review, and explicit approval for every Helix
  write.
- Do not add tool arguments or business values to logs, audit records, or
  metrics.
- Keep configuration changes backward compatible or document the migration.
- Use fictional examples that can be published safely.

Report vulnerabilities through the private process in
[SECURITY.md](SECURITY.md), never through a public issue.

## Development checks

```text
.venv/bin/python -m ruff format --check src tests hatch_build.py
.venv/bin/python -m ruff check src tests hatch_build.py
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
sh arapi-bridge/test.sh
```

The Java contract and local end-to-end tests are described in
[docs/development.md](docs/development.md). They use fictional credentials and
test doubles. Live tests are opt-in and are never part of CI.

## Pull requests

Keep each pull request focused, explain its risk, and add tests for new
behavior. Update documentation and the changelog when a change is visible to
operators or users.

By submitting a contribution, you agree that it may be distributed under the
repository's MIT License.
