# Release checklist

Follow these steps in order to publish a new Filedge release.

## 1. Pre-release checks

```bash
# Lint
uv run ruff check .

# Full test suite with coverage
uv run pytest --cov=filedge --cov-report=term-missing
```

Both must pass cleanly before continuing.

## 2. Version bump

Update `version` in `pyproject.toml`:

```toml
[project]
version = "0.x.y"
```

Commit the bump:

```bash
git add pyproject.toml
git commit -m "chore: bump version to 0.x.y"
git tag v0.x.y
```

## 3. Build the package

```bash
uv build
```

Verify the `dist/` directory contains both a `.tar.gz` and a `.whl`.

### Smoke-test the wheel in a clean environment

```bash
uv run --isolated --with dist/filedge-0.x.y-py3-none-any.whl -- filedge --help
```

The output should display the top-level help text without errors.

### Verify optional extras resolve

```bash
uv run --isolated \
  --with "dist/filedge-0.x.y-py3-none-any.whl[postgres,bigquery,duckdb]" \
  -- filedge --help
```

## 4. Build the docs

```bash
uv run mkdocs build --strict
```

The `--strict` flag promotes warnings to errors. Fix any broken links or missing pages before proceeding.

## 5. Publish to PyPI

```bash
uv publish
```

Confirm the release appears at <https://pypi.org/project/filedge/>.

## 6. Post-publish: update the one-command install path

After the package is live on PyPI, update the install command in `docs/getting-started.md` (and anywhere else the git-URL form appears) from:

```bash
uvx --from git+https://github.com/tongqqiu/filedge.git filedge --help
```

to:

```bash
uvx filedge --help
```

Commit this change and push to `main` so the published docs reflect the PyPI install path.

```bash
git add docs/
git commit -m "docs: update install command to PyPI uvx form for v0.x.y"
git push origin main
```

## 7. Redeploy docs (if using GitHub Pages)

```bash
uv run mkdocs gh-deploy --force
```

Confirm the live docs site reflects the updated install command.
