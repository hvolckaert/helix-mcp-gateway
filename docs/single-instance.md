# Single logical installation

The gateway represents one physical BMC Helix installation at a time. Its
internal logical identity is `helix`, with fixed DEV, QA, and PROD environments.

## Local AR API routing

The AR API bridge connects only to configured loopback listeners. The external
connectivity layer determines which authorized remote environment each local
listener reaches. The gateway does not discover organization-specific groups,
aliases, or topology files.

Exact local ports and remote destinations are deployment configuration and
must not be published in examples or support reports.

## Changing the physical installation

A physical target change is never hot-swapped:

1. stop the MCP server;
2. stop or reload the local connectivity layer and AR API bridge;
3. update the authorized remote destinations;
4. update credentials if required;
5. restart the local components and MCP server;
6. run `list_targets` and `health_check` before reading Helix.

Restarting clears clients and caches. Pending plans from the previous physical
installation must be cancelled or isolated in a new encrypted plan store so a
reviewed operation cannot cross installation boundaries.

SQL uses the same AR API listeners and credentials as form operations. Every
tool call still selects DEV, QA, or PROD explicitly.
