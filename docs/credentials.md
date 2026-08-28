# Credential management

## Principles

- Configuration stores opaque references, never secret values.
- Each reference selects exactly one provider; there is no provider fallback.
- Secrets are resolved as late as possible and are not cached by this layer.
- Values and errors have redacted representations.
- Secret values cannot be copied or serialized with `pickle`.
- Consumers close values through a context manager.
- Each environment has one AR API credential reference.

Python cannot guarantee physical erasure of a string already allocated in
memory. `SecretValue.close()` removes application references as early as
possible, but process isolation, least privilege, and rotation remain
necessary.

## `environment` provider

This provider reads only explicitly registered `HELIX_CREDENTIAL_*` keys from
the parsed `.env` data; it does not copy them into `os.environ`.

A value may be a scalar token or a JSON object containing credential fields.
Local files must use private permissions and must never be committed.

## `keyring` provider

The optional `keyring` provider is intended for Windows Credential Manager,
macOS Keychain, or a Linux desktop secret service. References use
`<service>/<account>`.

The adapter is available internally, but local composition currently selects
the `environment` provider explicitly. It is not enabled automatically because
some desktop backends may require interactive startup.

## `vault` provider

The Vault adapter reads KV v2 records through an authenticated reader supplied
by application composition. Vault tokens never belong in the Helix
configuration. Workload identity and path permissions are deployment concerns.

## Rotation and expected fields

References should remain stable while their stored values rotate. Derived
sessions must be invalidated after rotation.

AR API credentials require `username` and `password`, plus a domain when the
installation requires one. SQL reuses the selected AR API credential and
requires AR System administrator permissions. Resolution fails closed when a
required field is absent.
