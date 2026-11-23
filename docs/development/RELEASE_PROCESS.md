# Release Process

This document defines the release lifecycle, versioning strategy, and publishing procedures for **openagent**.

---

## 1. Versioning Strategy

openagent adheres to [Semantic Versioning (SemVer)](https://semver.org/):
- **MAJOR** version increments indicate breaking architectural changes or major platform upgrades (e.g. `v57.0.0`).
- **MINOR** version increments indicate new backwards-compatible features, actions, or providers.
- **PATCH** version increments indicate backwards-compatible bug fixes and security patches.

The canonical package version is declared in `pyproject.toml` under `[project].version` (using PEP 440 specification, e.g. `57.0` for Mark LVII). Release tags and changelogs align with this version (e.g. `v57.0` or `v57.0.0`).

---

## 2. Release Preparation Checklist

Before creating a release:

1. **Verify Test Suite**:
   ```bash
   python -m pytest tests/ -v
   python -m providers._tests
   ```
2. **Run Linting & Static Typing**:
   ```bash
   ruff check tests/ setup.py
   ruff format --check tests/ setup.py
   mypy tests/
   ```
3. **Execute Security Audits**:
   ```bash
   bandit -ll -r actions core gui memory plugins main.py ui.py setup.py tests
   pip-audit -r requirements.txt
   ```
4. **Update Documentation**:
   - Update `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/) standards.
   - Synchronize version string in `pyproject.toml`.

---

## 3. Build & Packaging Validation

Validate package build locally:
```bash
python -m pip install --upgrade build
python -m build
```
Verify generated `.tar.gz` and `.whl` in `dist/`.

---

## 4. Tagging & Automated GitHub Release

When all verification steps pass:

1. **Commit Release Changes on Branch**:
   ```bash
   git add CHANGELOG.md pyproject.toml
   git commit -m "chore(release): prepare release vX.Y.Z"
   ```

2. **Merge and Push to `main`**:
   The release commit must be merged onto `main` and successfully pushed before creating or publishing any release tag. This prevents tags from triggering automated releases while `main` lacks the updated version and changelog:
   ```bash
   git checkout main
   git merge --ff-only <release-branch>
   git push origin main
   ```
   *Verify that `origin/main` contains the release commit before proceeding.*

3. **Tag and Publish**:
   Only after `main` has been successfully updated on the remote, create the annotated tag and push it:
   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin vX.Y.Z
   ```

4. **Automated CI/CD**:
   The `.github/workflows/release.yml` workflow triggers automatically on tag pushes matching `v*`:
   - Checks out the repository.
   - Sets up Python 3.12.
   - Builds distribution archives (`dist/*`).
   - Generates release notes and publishes the GitHub Release via `softprops/action-gh-release`.

---

## 5. Post-Release Verification

- Check the GitHub Actions run logs in the Actions tab.
- Verify release artifacts are attached to the new release on GitHub.
- Perform a fresh clone in a clean environment to smoke test installation:
   ```bash
   git clone --branch vX.Y.Z https://github.com/Sanj1th-M/openagent.git
   cd openagent
   python3 -m venv .venv
   source .venv/bin/activate       # Windows: .\.venv\Scripts\Activate.ps1
   python setup.py
   ```
