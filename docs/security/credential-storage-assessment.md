# Credential Storage Implementation Assessment

## Executive assessment

Credentials are currently stored as ordinary JSON strings in
`config/api_keys.json`. The current write path narrows access on POSIX systems
and uses atomic replacement, but these measures do not encrypt the credentials.
The config file also remains the supported backup and compatibility format.
Moving credentials to operating-system vaults is feasible, but a safe change
requires a staged compatibility layer and updates to existing direct readers.
Do not migrate or remove existing values automatically.

## Current storage and retrieval flow

`memory/config_manager.py::_write_config` serializes the complete settings
dictionary with `json.dump` and atomically replaces `config/api_keys.json`.
`save_api_keys` stores `gemini_api_key`; `save_provider_config` stores provider
settings, including provider `api_key` fields under `providers.creds`; and
`save_plugin_config` stores arbitrary plugin settings under
`plugin_config[namespace]`. These APIs currently persist values as plaintext
JSON strings.

Retrieval is split across the project. `get_gemini_key` calls `load_api_keys`,
but `core/gemini.py::api_key` and several actions read the JSON file directly.
Provider and plugin settings are read through `get_provider_config` and
`get_plugin_config`, and some service code accesses the raw config dictionary.
The public settings APIs return values in the existing settings shape. A
versioned secret reference cannot be introduced into the file until every
consumer either resolves it or is moved behind a common accessor; otherwise
some consumers would treat the reference object as the credential.

The config manager sets the config directory to owner-only mode `0700` and the
file to `0600` on POSIX. On Windows it relies on the parent directory's
inherited ACL. Atomic writes prevent partial JSON replacement; neither control
encrypts the file. `.gitignore` reduces accidental repository commits but does
not protect local copies, backups, filesystem snapshots, or synced folders.

## Threat model and scenarios

Assets include Gemini, hosted provider, plugin/API, and remote pairing
credentials. Relevant attackers include another local account, malware or a
compromised process running as the signed-in user, someone with access to
backups or synchronized copies, and a user who accidentally packages or shares
the configuration directory.

File permissions can reduce access by other local accounts when correctly
configured. They do not prevent the signed-in user or same-user malware from
reading the file, and they do not encrypt backups or snapshots. A vault
integration reduces exposure from copying the settings file and from other
local accounts under the OS vault's access model. It cannot protect credentials
from malware running as the same user, a compromised application process, or
code that receives the resolved credential in memory.

## OS-backed options

- **Windows:** Windows Credential Manager through a supported keyring backend.
  Verify that the selected backend is the Windows vault backend and that reads
  and writes are scoped to the current user. Do not accept an insecure fallback
  backend.
- **macOS:** Keychain Services through a supported keyring backend. Use an
  application-scoped service name and stable account identifiers, and handle
  locked-keychain or access-prompt failures explicitly.
- **Linux:** Secret Service (for example, a desktop keyring) through a supported
  backend. A headless host may have no D-Bus session or Secret Service. Treat
  that as unavailable; do not silently choose a plaintext file backend.

An adapter should expose small `get`, `set`, `delete`, and `availability`
operations and allow a fake implementation in tests. Backend selection and
availability must be explicit and fail closed for new writes. The library's
presence alone does not prove that a secure backend is active.

## Compatible migration design

1. Keep `config/api_keys.json` for non-secret settings. Add a versioned secret
   reference format and a manager-level resolver that returns the existing
   runtime settings shape to consumers.
2. Move direct JSON credential reads behind the manager before writing
   references. This includes `core/gemini.py` and action/service readers, not
   only provider and plugin settings.
3. Continue reading legacy plaintext strings during a published transition
   window. Do not convert on startup or as a side effect of an ordinary save.
4. Add a user-initiated migration preview listing credential field names and
   counts only; never print credential values. Require an explicit user action
   before migration.
5. For each secret, write it to the verified vault and read it back to compare
   exact bytes. Only then atomically replace that JSON value with its reference.
   If a vault or JSON operation fails, leave the original plaintext value
   intact and report the field name and failure class without the secret.
6. Treat old backups as still containing plaintext. Offer cleanup only as a
   separate explicit operation with a warning that backups, snapshots, and
   storage media may retain copies. Do not claim that ordinary deletion
   securely erases them.
7. For a restored JSON file with unresolved references, clearly show which
   credential fields need re-entry. Do not substitute an empty value or create
   a plaintext fallback.

The migration's rollback path must preserve the original JSON until every
vault write and reference update has been verified. If a user chooses to
restore plaintext operation, make it an explicit export/rollback operation and
explain the at-rest exposure; never silently downgrade because a vault is
temporarily locked or unavailable.

## Validation plan

Use an injected in-memory vault fake to test legacy reads, reference resolution,
successful migration, partial vault failures, failed JSON replacement,
corrupted references, vault unavailability, and rollback. Assert no credential
values appear in logs or migration summaries. Add OS integration tests on
Windows, macOS, and Linux runners where a real user vault is available. If a
runner lacks its system vault, report that platform integration as unavailable;
continue running deterministic adapter contract tests on every platform.

## Recommendation for this PR

Keep plaintext credential storage as a documented residual risk and track the
vault adapter and explicit migration as a separate implementation task. This
PR's security remediation does not yet route all credential reads through one
resolvable interface, and adding a partial vault path would create inconsistent
behavior and risk losing access to existing settings. The existing POSIX
permissions and atomic replacement remain defense in depth, not encryption.
