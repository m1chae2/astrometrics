"""Purpose: Tests for the limits placed on AI-written code.

Description: Code from the ``electron_run_python`` MCP tool may use only the
public astrometricslib and wayfindinglib names and analysis packages. These
tests check what is allowed and the main ways around that which are refused.
"""

import pytest

from backend.services.infrastructure.agent_code_policy import check_agent_code


@pytest.mark.parametrize(
    "code",
    [
        "import numpy as np\nresult = np.mean([1, 2, 3])",
        "from astrometricslib import Astrometrics, Target",
        "import astrometricslib\nastrometricslib.Astrometrics",
        "targets = astrometrics.targets.list()\nresult = len(targets)",
        "import pandas as pd\ndf = pd.DataFrame({'a': [1]})\nresult = df.a.sum()",
        "import matplotlib.pyplot as plt\nplt.plot([1, 2])",
        "from scipy import stats",
        "_ = 1 + 1",
    ],
)
def test_analysis_and_public_api_use_is_allowed(code: str) -> None:
    """Library calls and analysis of their results pass."""
    assert check_agent_code(code) is None


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "import subprocess",
        "from astrometricslib.api.stars import StarsAPI",
        "import astrometricslib.drivers.catalog_access",
        "from astrometricslib import _private_thing",
        "from astrometricslib import NotAPublicName",
        "from backend.container import container",
        "astrometrics._targets",
        "astrometrics.targets.__dict__",
        "getattr(astrometrics, 'targets')",
        "eval('1+1')",
        "open('/etc/passwd')",
        "targets.delete_target('x')",
        "np.save('a.npy', [1])",
        "import pandas as pd\npd.read_csv('a.csv')",
        "import pandas as pd\npd.DataFrame().to_csv('a.csv')",
        "plt.savefig('a.png')",
        "container.target_service",
        "astrometrics.targets.delete('M 52')",
    ],
)
def test_private_system_and_destructive_use_is_refused(code: str) -> None:
    """Private, system, file and delete use is refused."""
    assert check_agent_code(code) is not None


def test_code_with_a_syntax_error_is_passed_on() -> None:
    """The normal syntax error reaches the client."""
    assert check_agent_code("def broken(:") is None
