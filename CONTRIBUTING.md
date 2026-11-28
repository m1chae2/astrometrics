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

### Tests on Windows

Ubuntu is the main system, and every test runs there. The Python tests also run on Windows, in the `windows CI` workflow (`.github/workflows/windows.yml`) and on a Windows checkout set up with `build\windows\setup_windows.ps1`, which makes `.venv-win`:

```powershell
.venv-win\Scripts\python -m pytest -n auto -o addopts="" -m "not trio"
```

Windows leaves out the tests of parts that exist only on Linux. The root `conftest.py` holds the list, `LINUX_ONLY_TEST_PATHS`, with the reason for each folder or file: the simulator, the TPM harness and INDI, which Windows does not have, and Siril's Linux pipes and process cleanup, whose Windows versions have their own tests. A run on Windows names each left-out path at the top of its output.

A single test of Linux-only behavior carries the `linux_only` marker with its reason, and Windows skips it:

```python
@pytest.mark.linux_only("the test uses Linux mount points such as /mnt/nas")
def test_a_mounted_drive_passes(...):
```

Linux runs every test, marked or not. Before you mark a test, check whether it can run on both systems. Most tests that fail on Windows assume something about Linux:

- **A path written with `/`.** Compare with `Path(...).as_posix()`, or build the expected path with `os.path.join`.
- **A symbolic link.** Windows makes these only in Developer Mode. Stage a file with `file_staging.stage_file` (`astrometricslib/_internal/drivers/file_staging.py`), as the app does: a symbolic link on Linux, a hard link on Windows.
- **A fake program written as a script.** Windows does not run a script by its `#!` line. Put a `.cmd` file in front of it that starts the script with `sys.executable`.
- **`os.kill(pid, 0)` to see whether a process runs.** On Windows this ends the process. Use `psutil.pid_exists` there.
- **A Windows path in TOML.** Put it in single quotes (`path = 'C:\...'`). In double quotes, `\U` is read as an escape.

The library keeps the two systems alike. It uses libraries that already handle both (`platformdirs` for folders, `filelock` for locks, `psutil` for processes), and it tries an action and falls back rather than checking the system. The few rules that really differ have one module per system, chosen once at import: `astrometricslib/foundation/system.py` (with `_system_posix.py` and `_system_windows.py`) and the Siril pipes in `siril/core/pipes.py`. Code outside those modules does not check `sys.platform`. Test each module on its own system: skip a Windows-only test off Windows instead of patching `sys.platform`.

Frontend unit tests never reach a backend: `ui/setupTests.ts` mocks `fetch` and refuses `fetch`, `XMLHttpRequest` and `WebSocket` calls to the default backend port (5000). A few files need a real backend and carry the marker `@requires-test-backend`. They are skipped unless you set `ASTROMETRICS_LIVE_BACKEND_TESTS=1`. With it set, `ui/globalSetup.ts` starts a throwaway backend of its own (its own library, a random port, the INDI simulator), and the marked tests use only that one. Never point these tests at a backend that is connected to real hardware or the real library, and keep device commands, policy changes, library writes and ingestion starts out of them.

## UI controls

Build every screen from the components in `ui/common/components/` (`Button`, `TextInput`, `Checkbox`, `PopoverSelect`, `Tabs` and the others there), and never restyle them for one screen. If a control needs a new look or size, add a prop or variant to the component. ESLint rejects a raw `<button>`, `<input>`, `<select>` or `<textarea>`, a hand-built `btn` class and an inline color. Stylelint rejects a stylesheet that targets a shared control's classes (such as `.btn` or `.panel .btn`) outside the control's own stylesheet. New colors, sizes and radii are tokens in `ui/common/styles/theme.css`.

## Performance measures (TPMs)

`build/tpm/run_tpm.sh` measures how fast the app is and how many resources it uses (display switch times, blocking time, memory, CPU and backend calls) and compares the result with `build/tpm/tpm_baseline.json`. It runs only against a throwaway library and a throwaway backend on a random port, never your real backend or hardware. Run it before and after a change that could make the app slower or heavier. A **breached** TPM needs a fix or a stated reason in the pull request. Update the baseline only on purpose, with `--update-baseline`. [`build/tpm/README.md`](build/tpm/README.md) explains each measure.

## Documentation

[About these docs](documentation/About_These_Docs.md) maps every document.

- **Where content lives.** Theory goes in the architecture paper, code names and tuned values in the implementation paper, and evidence in the validation register under `documentation/library_design/validation/`.
- **Generated files.** The pipeline review guides are generated. Edit the generators in `build/documentation/` and run them again. Never edit the output by hand.
- **Register workflow.** Changing a tuned value or its method means updating its register entry. See the [how-to guides](documentation/how_to/change_a_tuned_value.md).
- **Checks.** Run the documentation tests:

  ```bash
  .venv/bin/pytest astrometricslib/test/test_documentation_rules.py astrometricslib/test/test_documentation_style.py astrometricslib/test/test_documentation_notation.py astrometricslib/test/test_documentation_site.py wayfindinglib/test/test_documentation_papers.py astrometricslib/test/test_architecture_doc_claims.py astrometricslib/test/test_validation_register.py astrometricslib/pipelines/test/test_pipeline_doc_parity.py -q
  ```

  Then lint the prose and Markdown with `build/documentation/lint_docs.sh` (Vale and markdownlint; Vale comes with the dev extra, `.venv/bin/pip install -e ".[dev]"`, and markdownlint runs through `npx`). Use `--fix` to let markdownlint repair list and spacing issues. Finally build the site with warnings as errors: `bash build/linux/build_docs.sh`. The pre-commit hooks `documentation-rules` and `documentation-lint` run the tests and the linters on each commit.

## Commit messages

The first line starts with one of `doc:`, `refactor:`, `new:`, `fix:` or `maintenance:`, then a space and a short description, for example `fix: reject frames with no pointing data`. The `commit-msg` hook (`build/hooks/check_commit_message.sh`) rejects other messages. Git's own merge messages pass.

## Issues and pull requests

Open an issue before starting tracked work. Plans and design decisions live in the issue, not as files in the repository. Give it a subsystem, component and type label, and a display label (for example `Planetarium` or `Status Header`) for each part of the screen it changes. Before a pull request that closes an issue can merge, the issue must contain an `## Analysis and Plan` section: what you found investigating the problem and what the change will do. The `Issue Progress Required` workflow (`.github/workflows/issue-progress-required.yml`) checks for it. Close the issue from the pull request description with a keyword such as `Closes #123`. Fill in the checklist in the pull request template.
