# Configuration

This directory must contain only non-sensitive configuration:

- `helix.yaml`: a safe, read-only public default;
- `helix.example.yaml`: a minimal publishable schema example;
- `*.local.yaml`: ignored machine- or organization-specific runtime policy.

Credentials, private endpoints, organization names, and connection strings do
not belong here. Credentials are resolved through opaque per-environment
references.

The `arapi` section configures the local loopback bridge. Policies control
form reads, SQL, row and timeout limits, and controlled write scopes. Explicit
write allowlists are the default; opt-in broad write modes still enforce the
general form boundary, sensitive-field protections, human approval, and a
write reason. Every environment starts with `access_mode: read_only` and may
enable controlled writes explicitly.

Schema version 2 separates `creatable_fields_by_form` from
`updatable_fields_by_form`. The loader accepts one safe YAML document, rejects
duplicate keys, and does not interpolate environment variables.

Before sharing a configuration file, verify that every form, field, policy,
and example value is fictional or part of public product documentation.
