"""Purpose: OS-level diagnostic and management tasks for the MCP tools.

Handles subprocess execution for tests and linting.
"""

import os
import subprocess


def run_frontend_diagnostics(repo_root: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Run the frontend type-check and lint commands.

    Parameters
    ----------
    repo_root : `str`
        Absolute path to the repository root containing the frontend
        `package.json`.

    Returns
    -------
    result : `str`
        Combined, human-readable summary of the type-check and lint
        results, with long command output truncated.
    """
    results = []

    # TypeScript Type Check
    try:
        tc_res = subprocess.run(
            ["npm", "run", "type-check"], cwd=repo_root, capture_output=True, text=True, timeout=120
        )
        if tc_res.returncode == 0:
            results.append("✅ TypeScript Type Check: Passed")
        else:
            output = tc_res.stdout + tc_res.stderr
            if len(output) > 5000:
                output = output[:2500] + "\n... [TRUNCATED] ...\n" + output[-2500:]
            results.append(f"❌ TypeScript Type Check: Failed\n{output}")
    except Exception as e:
        results.append(f"⚠️ Error running type-check: {e!s}")

    # Linting
    try:
        lint_res = subprocess.run(
            ["npm", "run", "lint"], cwd=repo_root, capture_output=True, text=True, timeout=120
        )
        if lint_res.returncode == 0:
            results.append("✅ Linting: Passed")
        else:
            output = lint_res.stdout + lint_res.stderr
            if len(output) > 2000:
                output = output[:1000] + "\n... [TRUNCATED] ...\n" + output[-1000:]
            results.append(f"❌ Linting: Failed\n{output}")
    except Exception as e:
        results.append(f"⚠️ Error running lint: {e!s}")

    return "\n\n".join(results)


def run_frontend_tests(repo_root: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Run the frontend unit test suite.

    Parameters
    ----------
    repo_root : `str`
        Absolute path to the repository root containing the frontend
        `package.json`.

    Returns
    -------
    result : `str`
        Human-readable summary of the test run, with long command
        output truncated.
    """
    try:
        res = subprocess.run(
            ["npm", "run", "test:unit"], cwd=repo_root, capture_output=True, text=True, timeout=300
        )
        output = res.stdout + res.stderr
        if len(output) > 5000:
            output = output[:2500] + "\n... [TRUNCATED] ...\n" + output[-2500:]

        if res.returncode == 0:
            return f"✅ Tests Passed!\n{output}"
        else:
            return f"❌ Tests Failed!\n{output}"
    except Exception as e:
        return f"⚠️ Error running tests: {e!s}"


def run_backend_tests(repo_root: str, test_path: str | None = None):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Run the backend pytest suite.

    Parameters
    ----------
    repo_root : `str`
        Absolute path to the repository root containing the `.venv`
        virtualenv.
    test_path : `str`, optional
        Path to a specific test file or directory, relative to
        `repo_root`. If `None` (default), runs `backend/tests`.

    Returns
    -------
    result : `str`
        Human-readable summary of the pytest run, with long command
        output truncated.
    """
    if os.name == "nt":
        python_bin = os.path.join(repo_root, ".venv", "Scripts", "python.exe")
    else:
        python_bin = os.path.join(repo_root, ".venv", "bin", "python")

    if not test_path:
        test_path = "backend/tests"

    try:
        res = subprocess.run(
            [python_bin, "-m", "pytest", test_path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = res.stdout + res.stderr

        if len(output) > 8000:
            output = output[:4000] + "\n... [TRUNCATED] ...\n" + output[-4000:]

        if res.returncode == 0:
            return f"✅ Backend Tests Passed!\n{output}"
        else:
            return f"❌ Backend Tests Failed!\n{output}"
    except Exception as e:
        return f"⚠️ Error running backend tests: {e!s}"
