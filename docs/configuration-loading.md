# Safe configuration loading

## Source

The primary configuration is one UTF-8 YAML document. `HELIX_CONFIG_PATH`
selects it; otherwise the gateway uses `config/helix.yaml` relative to the
`.env` file.

An optional allowed root can constrain the resolved path. When configured,
the file must remain inside that root.

## Schema version 2

Version 2 requires explicit policy access modes and separate create/update
allowlists:

```yaml
access_mode: read_write
writable_forms:
  - Example:Form
creatable_fields_by_form:
  Example:Form:
    - Description
updatable_fields_by_form:
  Example:Form:
    - Status
```

`read_write` requires human approval and non-empty write allowlists.
`read_only` requires every write allowlist to be empty. Any policy assigned to
PROD must be `read_only`.

Legacy keys such as `create_mode`, `update_mode`, and
`writable_fields_by_form` are rejected.

## Parser controls

The loader enforces:

- a default 1 MiB size limit;
- exactly one YAML document;
- `SafeLoader`, without Python object construction;
- at most 50 aliases and a maximum nesting depth of 64;
- rejection of duplicate keys;
- a mapping at the document root;
- symbolic-link rejection by default;
- validation through `SingleInstanceConfig`.

Limits may be reduced but should not be disabled in production.

## Sanitized errors

Configuration errors expose only the file name, optional line and column, and
a stable category. They never reproduce rejected YAML fragments or values.

Stable codes include:

- `CONFIG_SOURCE_ERROR`;
- `CONFIG_TOO_LARGE`;
- `CONFIG_ENCODING_ERROR`;
- `CONFIG_SYNTAX_ERROR`;
- `CONFIG_STRUCTURE_ERROR`;
- `SINGLE_INSTANCE_CONFIG_ERROR`.

## Environment variables

There is no implicit `${VARIABLE}` interpolation. Process variables take
precedence over `.env` values.

Credentials are resolved only through `HELIX_CREDENTIAL_*` references. Audit,
metrics, and operational-log paths are optional and relative paths resolve
against the `.env` directory.

Encrypted plan persistence requires both:

- `HELIX_WRITE_PLAN_DB_PATH`;
- `HELIX_WRITE_PLAN_KEY_PATH`.

The key file must contain exactly 32 bytes. Startup rejects configurations
that define only one of the two paths.
