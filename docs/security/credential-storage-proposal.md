# OS-Backed Credential Storage Proposal

## Goal

Replace plaintext API and plugin credentials in `config/api_keys.json` with an operating-system credential vault while preserving the current settings UI, provider configuration, and portable non-secret settings. This is a proposal only; the CI remediation does not change credential storage behavior.

## Recommended design

Use a small storage adapter in `memory/` with operations to read, write, delete, and report availability for named secrets. Use the OS vault selected by the maintained Python `keyring` package: Windows Credential Manager, macOS Keychain, and Secret Service on Linux. Treat a missing or unsuitable vault as an explicit unavailable state. Never silently fall back to writing credentials in plaintext after migration.

Keep `config/api_keys.json` as the single source for non-secret settings. Replace secret values with versioned references such as `{"$secret_ref": "gemini_api_key"}` and store each credential under an application-scoped service name with a stable account/key identifier. Preserve the existing configuration API so plugins and UI consumers continue to receive the same settings shape from `memory/config_manager.py`; the manager resolves references when constructing the in-memory view.

## Backward-compatible migration

1. On read, recognize both legacy string values and versioned secret references. Legacy plaintext values remain readable during the migration window.
2. Offer an explicit migration action in settings with a clear list of credential fields to move and the target OS vault. Do not migrate on ordinary app startup or silently alter a user's config.
3. For each secret, write it to the vault and read it back to verify exact equality. Only after verification, atomically replace that field in JSON with its versioned reference. Preserve other fields and existing atomic-write/permission protections.
4. If any vault write or JSON update fails, keep the corresponding legacy value intact and report which entries were not migrated. Do not delete the only working copy.
5. After a successful migration, offer removal of plaintext from known backup files as a separate, explicit cleanup step. Explain that old copies on backups, snapshots, or disk media cannot be reliably erased by the app.
6. During the transition release, continue resolving legacy strings and references. Add a migration-status report that distinguishes migrated, legacy, missing-vault, and inaccessible-vault entries. A future release may stop accepting legacy strings only after a published deprecation period and a user-visible export/recovery path.

## Backup, restore, and portability

Export JSON with non-secret settings and secret-reference metadata only. Provide a separate encrypted credential export flow if cross-device migration is required; protect it with a user-chosen passphrase and a modern password-based key derivation plus authenticated encryption. Import should write and verify vault entries before committing JSON references, mirroring the migration transaction. A JSON backup restored on another account or machine must report unresolved references and provide a clear re-entry path rather than creating empty credentials.

Document that OS-backed storage protects secrets from other local users subject to OS account and vault permissions. It does not protect secrets from malware running as the signed-in user, a compromised process, or code that can read the in-memory settings view.

## Rollout and tests

- Add the vault adapter behind an injectable interface, then unit-test the adapter contract with an in-memory fake on all CI platforms.
- Add platform integration tests for Windows Credential Manager, macOS Keychain, and Linux Secret Service in suitable runners; skip only when the runner explicitly lacks an interactive/system vault, and report that limitation.
- Test legacy reads, successful migration, partial failures, corrupted references, vault unavailability, backup/import, and atomic rollback.
- Add a dry-run migration report before enabling writes, then ship the explicit migration UI and documentation.
- Keep current POSIX file modes and atomic JSON replacement as defense in depth for non-secret settings and unmigrated legacy entries.

## Decisions needed before implementation

- Whether to add `keyring` and its platform backends to the default install or make secure credential storage a required capability with a guided install path.
- Whether encrypted portable credential export is in scope for the first release.
- The length of the legacy plaintext compatibility window and the user experience when no OS vault is available.
