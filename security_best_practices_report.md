# Security investigation: CodeQL alert 24

## Executive summary

**CONFIRMED:** CodeQL alert 24 correctly identified a route parameter flowing
into a filesystem path in `dashboard/server.py`.  On `main`, removing slashes
from that parameter was not a sound containment control: it changed attacker
input instead of proving that the file served belonged to the uploads
directory.  The target branch's existing name allowlist and resolved-path
containment check prevent the demonstrated traversal and symlink cases.

The branch is not yet fully review-ready because it has no regression tests
for this security boundary.  The remediation below keeps the allowlist and
containment check, and separates the user-provided name from filesystem path
selection by choosing only an existing directory entry.

## A. Original vulnerability

On `main`, `download_file` accepted the user-controlled `{filename}` route
parameter, removed only `/` and `\\`, and then joined the resulting text to
`self._uploads_dir`.  The CodeQL alert (rule `py/path-injection`, CWE-22,
severity `high`) identifies that data flow.  Textual sanitization alone is not
an authorization boundary for filesystem objects; it neither establishes a
canonical base-directory containment invariant nor handles every
platform-specific path interpretation.

## B. Evidence

- GitHub alert 24 is open at
  `https://github.com/Sanj1th-M/openagent/security/code-scanning/24`.  Its
  most recent instance is on `main` commit `c9e2df5`, workflow
  `.github/workflows/security.yml:codeql`, location
  `dashboard/server.py:1162`, and says the path depends on the route value at
  line 1153.
- The original vulnerable construction was `path = self._uploads_dir / safe`
  in `download_file` on `main`.
- The route is `GET /uploads/{filename}` in `dashboard/server.py` (current
  branch lines 1146-1167).  FastAPI supplies `filename` from the URL path.
- Authentication is checked first with a bearer token supplied as the `token`
  query parameter and validated by the local `_valid` function.  An attacker
  must possess a live dashboard token; this limits exposure but does not make
  unsafe filesystem-path handling correct.
- Uploads are stored in the process-selected `UPLOADS_DIR`; the upload route
  sanitizes upload names, and the list route only enumerates `is_file()`
  entries.  There is no deletion route in this module.
- The target branch's commits `2619621` and `e1aa8fb` add a restrictive
  filename allowlist, `Path.resolve()`, and `relative_to(base)` containment.
  The branch contains no dashboard security regression tests.

## C. Attack scenario

Before the branch fix, a token holder could request a crafted path-like value
at the download endpoint.  Whether a particular traversal form escaped the
directory depended on FastAPI routing, URL decoding, and the host OS, so this
investigation does not claim a specific payload was proven to read an external
file on every deployment.  The CodeQL source-to-sink finding and absence of a
canonical containment check are sufficient to require remediation.

On the target branch, `../`, backslashes, absolute paths, URL-decoded path
separators, control characters, and names outside `[A-Za-z0-9._ -]` are
rejected.  A symlink inside the uploads directory resolving outside it fails
the containment check.

## D. Current implementation assessment

**FIXED in principle:** the target branch no longer strips separators and then
blindly joins the result.  Its allowlist and canonical containment check stop
ordinary traversal and pre-existing symlink escape.

**Still to improve:** the request text is still used to construct a candidate
path, which keeps the CodeQL data-flow shape and gives less explicit evidence
that the served name was selected from the upload directory.  The remediation
will select an existing entry from the resolved directory by exact name, then
resolve and contain that selected entry.  It preserves the visible behavior
for valid names.

## E. Risk assessment

The original issue is **High** in the CodeQL classification because arbitrary
file disclosure can expose local credentials or application data.  Actual
remote exploitability is reduced by the bearer-token check and local-LAN
dashboard deployment, but cannot be dismissed from code alone.

`Path.resolve()` plus a check is not a complete race-free filesystem access
primitive.  A separate local actor with write access to the uploads directory
could replace an entry after validation and before `FileResponse` opens it.
The dashboard upload API itself only writes regular files and does not create
symlinks, so this review did not establish a remote API-only exploit for that
race.  Fully eliminating it would require descriptor-based serving with
platform-specific no-follow semantics and is deliberately outside this
targeted alert fix.

## F. Missing information

- GitHub alert metadata was available, but no alert-specific proof-of-concept
  or detailed CodeQL trace beyond the API response was available.
- Deployment permissions and whether other local processes can write to the
  selected Downloads/Documents uploads directory are not represented in the
  repository.
- The live GitHub CodeQL run for the updated branch has not yet occurred.

## Remediation plan

1. Modify only `dashboard/server.py`: retain the existing auth, allowlist, and
   containment logic; enumerate the resolved uploads directory and select an
   exact existing entry rather than joining request text into a path.
2. Add `tests/test_dashboard_download_security.py` with focused checks for a
   valid authenticated download, unauthorized access, traversal/absolute and
   malformed names, encoded traversal at the ASGI boundary, and an escaping
   symlink where supported.
3. Run the focused test, all pytest tests, the configured Ruff and mypy checks,
   Bandit, the repository diff check, and review the final diff.  No dependency
   changes are required.

Compatibility: filenames produced by the existing upload sanitizer already
fit the allowed character set, and valid downloads keep the same URL and
response type.  The change intentionally continues returning `404` for an
invalid or absent filename to avoid revealing filesystem details.

## Follow-up hardening decision

### Confirmed issue

The prior implementation accepted a symbolic link to a regular file **inside**
the uploads directory: `candidate.resolve()` produces an in-directory regular
path, then `is_file()` succeeds.  The upload API only creates ordinary files,
so there is no legitimate dashboard-upload use case for serving symlinks.  In
addition, `Path.resolve()` can raise `OSError` for filesystem failures and
`RuntimeError` for a symlink loop; the previous handler only handled errors
from `iterdir()`.

The handler will therefore reject any selected symlink before resolving it and
will return the existing opaque 404 for those expected resolution errors.
Regular files retain their current behavior.  Directories and special files
continue to fail `is_file()`.

### Symlink evidence and behavior

- An outside-target symlink is already rejected when its resolved target fails
  `relative_to(base)`.
- An in-directory target symlink was accepted; this is the confirmed gap being
  closed.
- Broken links, symlink loops, and directory symlinks have no valid upload
  semantics and will all be rejected before any target resolution.
- This Windows environment cannot create symlinks (`winerror 1314`), so these
  tests skip here; they are suitable for the repository's Linux CI runner,
  where symlink creation is normally available.

### TOCTOU assessment

Installed Starlette 0.52.1's `FileResponse` performs `os.stat(self.path)` and
then opens `self.path` later via `anyio.open_file`; it does not retain an
already-open descriptor.  A local actor able to alter the uploads directory
could therefore replace a regular file after the handler validates it.

The inspected local uploads ACL gives the application owner full control and
the Codex sandbox users read/execute only.  The remote dashboard API creates
regular files, not links.  Thus the race is not confirmed as a remote,
API-only vulnerability under the repository's deployment model.  Replacing
`FileResponse` with custom descriptor-based range handling would be a broader,
platform-sensitive architectural change and is not justified by the available
evidence.  It remains a deployment limitation: do not grant untrusted local
principals write access to the uploads directory.
