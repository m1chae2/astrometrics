# Contributing to Astrometrics

Thank you for helping. This page covers how to set up, check and submit a change. By taking part you agree to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Set up

Astrometrics needs Python 3.14 or newer. The [installation guide](documentation/Installation.md) lists the system packages. Then create the environment:

```bash
./build/linux/setup_venv.sh
```

The script creates `.venv`, installs the package and copies the configuration template. Install the pre-commit hooks once per clone:

```bash
.venv/bin/pre-commit install
.venv/bin/pre-commit install --hook-type commit-msg
```

## Test and lint

Use the `.venv` interpreter for everything.

```bash
.venv/bin/ruff check
.venv/bin/ruff format
.venv/bin/pytest <affected test folder>
```

Every file starts with a description block, and every class, function, method and test has a docstring. For changes under `ui/`, also run `npm run type-check` and `npm test`.

## Documentation

The rules for writing and placing documentation are in [Documentation conventions](documentation/Documentation_Conventions.md), and [About these docs](documentation/About_These_Docs.md) maps every document.

- **Where content lives.** Theory goes in the architecture paper, code names and tuned values in the implementation paper, and evidence in the validation register under `documentation/library_design/validation/`.
- **Generated files.** The pipeline review guides and `Pipeline_Audit_Report.md` are generated. Edit the generators in `build/documentation/` and run them again. Never edit the output by hand.
- **Register workflow.** Changing a tuned value or its method means updating its register entry. See the [how-to guides](documentation/how_to/change_a_tuned_value.md).
- **Checks.** Run the documentation tests:

  ```bash
  .venv/bin/pytest astrometricslib/test/test_documentation_rules.py astrometricslib/test/test_documentation_style.py astrometricslib/test/test_architecture_doc_claims.py astrometricslib/test/test_validation_register.py astrometricslib/pipelines/test/test_pipeline_doc_parity.py -q
  ```

  Then lint the prose and Markdown with `build/documentation/lint_docs.sh` (Vale and markdownlint; Vale comes with the dev extra, `.venv/bin/pip install -e ".[dev]"`, and markdownlint runs through `npx`). Use `--fix` to let markdownlint repair list and spacing issues. Finally build the site with warnings as errors: `bash build/linux/build_docs.sh`. The pre-commit hooks `documentation-rules` and `documentation-lint` run the tests and the linters on each commit.

## Commit messages

The first line starts with one of `doc:`, `refactor:`, `new:`, `fix:` or `maintenance:`, then a space and a short description, for example `fix: reject frames with no pointing data`. The `commit-msg` hook (`build/hooks/check_commit_message.sh`) rejects other messages. Git's own merge messages pass.

## Issues and pull requests

Open an issue before starting tracked work. Before a pull request that closes an issue can merge, the issue must contain an `## Analysis and Plan` section: what you found investigating the problem and what the change will do. The `Issue Progress Required` workflow (`.github/workflows/issue-progress-required.yml`) checks for it. Close the issue from the pull request description with a keyword such as `Closes #123`. Fill in the checklist in the pull request template.
