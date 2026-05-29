# Release checklist

Follow these steps in order to prepare a new Filedge release. Package publishing
and versioned docs deployment are handled by GitHub Actions when a `v*` tag is
pushed.

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

## 3. Update the changelog

Add a new `## [0.x.y] - YYYY-MM-DD` section to [`CHANGELOG.md`](../CHANGELOG.md)
with curated **Highlights** plus `Added` / `Changed` / `Fixed` / `Documentation`
subsections as needed. This section is the release notes: the release workflow
extracts it verbatim as the GitHub Release body and appends the auto-generated
list of merged pull requests beneath it.

Keep it tight — write for someone deciding whether to upgrade, not an exhaustive
diff. Also update the link-reference lines at the bottom of the file:

```markdown
[Unreleased]: https://github.com/tongqqiu/filedge/compare/v0.x.y...HEAD
[0.x.y]: https://github.com/tongqqiu/filedge/compare/v<prev>...v0.x.y
```

The heading must read exactly `## [0.x.y]` (matching the tag minus the `v`), or
the release job fails fast because it can't find the section.

## 4. Open the release-prep PR

The release-prep PR should include:

- The version bump (`pyproject.toml` + `uv.lock`).
- The new `CHANGELOG.md` section.
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

## 5. Create and push the release tag

After the PR is merged and `main` is up to date, create the tag and push it.
The tag name must match the `CHANGELOG.md` heading (`v` + the `## [0.x.y]`
version):

```bash
git checkout main
git pull
git tag v0.x.y
git push origin v0.x.y
```

Pushing the tag triggers:

- `.github/workflows/release.yml`, which runs lint and tests, builds the source
  distribution and wheel, publishes to PyPI via trusted publishing, and creates
  a **GitHub Release** whose body is the curated `CHANGELOG.md` section followed
  by the auto-generated list of merged PRs, with the build artifacts attached.
- `.github/workflows/docs.yml`, which deploys the docs for the tag with `mike`
  under the tag's version number and updates the `latest` alias.

## 6. Watch the GitHub Actions workflows

Open the release and docs workflow runs for the pushed tag and confirm they pass:

```bash
gh run list --workflow release.yml --limit 5
gh run watch <run-id>

gh run list --workflow docs.yml --limit 5
gh run watch <run-id>
```

When the workflow succeeds, confirm the release appears at
<https://pypi.org/project/filedge/>.

Confirm the docs appear at:

- `https://tongqqiu.github.io/filedge/0.x.y/`
- `https://tongqqiu.github.io/filedge/latest/`

## 7. Optional local artifact smoke test

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

## 8. Post-publish: update the one-command install path

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

## 9. Redeploy docs after post-publish docs edits

```bash
uv run mike deploy --push --update-aliases 0.x.y latest
uv run mike set-default --push latest
```

Confirm the live docs site reflects the updated install command.

## One-time GitHub Pages setup

Versioned docs are published to the `gh-pages` branch by `mike`. In the GitHub
repository settings, configure Pages to deploy from the `gh-pages` branch,
root directory. This replaces the previous Pages artifact deployment flow.
