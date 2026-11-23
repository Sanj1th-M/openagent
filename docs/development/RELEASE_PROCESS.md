# Release Process

This document defines the release lifecycle, versioning strategy, and publishing procedures for **openagent**.

---

## 1. Versioning Strategy

openagent adheres to [Semantic Versioning (SemVer)](https://semver.org/):
- **MAJOR** version increments indicate breaking architectural changes or major platform upgrades (e.g. `v57.0.0`).
- **MINOR** version increments indicate new backwards-compatible features, actions, or providers.
- **PATCH** version increments indicate backwards-compatible bug fixes and security patches.

The canonical version is declared in `pyproject.toml` under `[project].version`.

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
1. Commit the release preparation changes:
   ```bash
   git commit -m "chore(release): prepare release vX.Y.Z"
   ```
2. Create an annotated Git tag:
   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin main --tags
   ```
3. The `.github/workflows/release.yml` workflow triggers automatically on tag pushes matching `v*`:
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
  python setup.py
  ```
