# Release checklist

Follow these steps in order to prepare a new Filedge release. Publishing is handled
by the GitHub Actions release workflow when a `v*` tag is pushed.

## 1. Pre-release checks

```bash
# Lint
uv run ruff check .

# Full test suite with coverage
uv run pytest --cov=filedge --cov-report=term-missing

# Docs
uv run mkdocs build --strict
```

All checks must pass cleanly before continuing. The `--strict` docs build promotes
warnings to errors; fix broken links or missing pages before opening the release
PR.

## 2. Version bump

Update `version` in `pyproject.toml` and the editable package entry in
`uv.lock`:

```toml
[project]
version = "0.x.y"
```

If you prefer to let `uv` refresh the lockfile instead of editing it directly,
run:

```bash
uv lock
```

## 3. Open the release-prep PR

The release-prep PR should include:

- The version bump.
- Documentation updates for user-facing changes since the last tag.
- Any small release-readiness fixes found while checking the docs.

Open the PR and let CI run on the branch:

```bash
git checkout -b codex/release-0.x.y
git add pyproject.toml uv.lock README.md docs/ filedge/ tests/
git commit -m "chore: prepare v0.x.y release"
git push origin codex/release-0.x.y
gh pr create --fill
```

Merge only after CI is green and the release notes/docs are complete.

## 4. Create and push the release tag

After the PR is merged and `main` is up to date, create the tag and push it:

```bash
git checkout main
git pull
git tag v0.x.y
git push origin v0.x.y
```

Pushing the tag triggers `.github/workflows/release.yml`. The workflow runs lint,
tests, builds the source distribution and wheel, uploads the `dist/` artifact,
and publishes to PyPI via trusted publishing.

## 5. Watch the GitHub Actions release workflow

Open the release workflow run for the pushed tag and confirm both jobs pass:

```bash
gh run list --workflow release.yml --limit 5
gh run watch <run-id>
```

When the workflow succeeds, confirm the release appears at
<https://pypi.org/project/filedge/>.

## 6. Optional local artifact smoke test

The GitHub Action owns publishing, but you can build and smoke-test artifacts
locally before tagging if you want extra confidence:

```bash
uv build

uv run --isolated --with dist/filedge-0.x.y-py3-none-any.whl -- filedge --help

uv run --isolated \
  --with "dist/filedge-0.x.y-py3-none-any.whl[postgres,bigquery,duckdb]" \
  -- filedge --help
```

The output should display the top-level help text without errors.

## 7. Post-publish: update the one-command install path

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

## 8. Redeploy docs (if using GitHub Pages)

```bash
uv run mkdocs gh-deploy --force
```

Confirm the live docs site reflects the updated install command.
