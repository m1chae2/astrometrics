"""Purpose: The pinned pieces of the GPU setup for the Flatpak Siril on Linux.

Description: ``siril_gpu_setup.py`` makes Siril's GPU tools (GraXpert,
StarNet2 and the Python scripts Siril runs) use the NVIDIA graphics card.
This module lists everything it installs, so a change of version is a change
to this file alone:

- the Flatpak permission overrides for ``org.siril.Siril``;
- three folders of CUDA libraries (CUDA is NVIDIA's toolkit for running
  programs on the graphics card), each file taken from one pinned Python
  wheel on PyPI and checked against its SHA-256 checksum;
- the Python packages for Siril's own Python environment;
- the two wrapper scripts and the settings that point at them.

Each wheel also names its license. The NVIDIA wheels are under NVIDIA's own
license agreements, which are not open source, so the setup asks the user to
accept them before it downloads one (see `License.needs_acceptance`).

Each file checksum is the one the wheel's own ``RECORD`` lists for that file,
so a downloaded wheel that differs in any byte is refused. Some files on the
machine these pins were taken from were copied by hand from other sources
(the Ollama CUDA 12 bundle, the CUDA 13 toolkit, a different build of the same
ONNX Runtime wheel). Their checksums are listed as ``hand_installed_sha256``:
the check accepts them as working copies, and the setup leaves them in place
unless ``--force`` is given.

The pins were taken on 2026-11-27 from the working setup on the project
machine (NVIDIA GeForce GTX 1650, driver 615.71, Siril 1.4 from Flathub with
Python 3.13).
"""

from dataclasses import dataclass

SIRIL_APP_ID = "org.siril.Siril"
"""The Flathub application ID of Siril."""


@dataclass(frozen=True)
class License:
    """The license a pinned wheel is under.

    Attributes
    ----------
    name : `str`
        The license's name.
    url : `str`
        Where its full text is published.
    open_source : `bool`
        Whether it is an open-source license. A license that is not must be
        accepted by the user before the setup downloads a wheel under it.
    """

    name: str
    url: str
    open_source: bool

    @property
    def needs_acceptance(self) -> bool:
        """Whether the user must accept this license before a download.

        Returns
        -------
        needs_acceptance : `bool`
            `True` for a license that is not open source.
        """
        return not self.open_source


CUDA_EULA = License(
    name="NVIDIA CUDA Toolkit End User License Agreement",
    url="https://docs.nvidia.com/cuda/eula/index.html",
    open_source=False,
)
"""The license of the CUDA runtime, cuBLAS, cuFFT, cuRAND and NVRTC wheels.
Their PyPI metadata says ``LicenseRef-NVIDIA-Proprietary``."""

CUDNN_LICENSE = License(
    name="NVIDIA cuDNN Software License Agreement",
    url="https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html",
    open_source=False,
)
"""The license of the cuDNN wheels (the NVIDIA SDK license agreement with its
cuDNN supplement). Their PyPI metadata leaves the license field empty."""

ONNXRUNTIME_LICENSE = License(
    name="MIT License",
    url="https://github.com/microsoft/onnxruntime/blob/main/LICENSE",
    open_source=True,
)
"""The license of ONNX Runtime."""


@dataclass(frozen=True)
class PinnedFile:
    """One file taken out of a pinned wheel.

    Attributes
    ----------
    member : `str`
        The file's path inside the wheel (a zip file).
    name : `str`
        The file's name in the target folder.
    sha256 : `str`
        The file's SHA-256 checksum, as the wheel's ``RECORD`` lists it.
    size_bytes : `int`
        The file's exact size.
    hand_installed_sha256 : `tuple` [`str`, ...]
        Checksums of other builds that are known to work and may already be
        in place. The check accepts them; the setup replaces them only with
        ``--force``.
    """

    member: str
    name: str
    sha256: str
    size_bytes: int
    hand_installed_sha256: tuple[str, ...] = ()


@dataclass(frozen=True)
class PinnedWheel:
    """One wheel on PyPI, with the files the setup takes from it.

    Attributes
    ----------
    project : `str`
        The project name on PyPI.
    version : `str`
        The pinned version.
    files : `tuple` [`PinnedFile`, ...]
        The files taken from the wheel.
    python_version : `str` or `None`
        For a wheel built for one Python version (``cp313``), that version,
        such as ``"3.13"``; `None` for a wheel that fits any Python.
    platform : `str` or `None`
        The wheel's platform tag when ``python_version`` is set.
    license : `License` or `None`
        The license the wheel is under; `None` only for stand-in wheels in
        the tests.
    """

    project: str
    version: str
    files: tuple[PinnedFile, ...]
    python_version: str | None = None
    platform: str | None = None
    license: License | None = None

    @property
    def needs_license_acceptance(self) -> bool:
        """Whether the user must accept the wheel's license before a download.

        Returns
        -------
        needs_acceptance : `bool`
            `True` when the wheel's license is not open source.
        """
        return self.license is not None and self.license.needs_acceptance


@dataclass(frozen=True)
class LibraryFolder:
    """A folder of libraries that the setup fills from pinned wheels.

    Attributes
    ----------
    part : `str`
        The part name used on the command line (``--parts``).
    title : `str`
        What the folder is, for the report.
    relative_path : `str`
        The folder, relative to the home folder.
    wheels : `tuple` [`PinnedWheel`, ...]
        Where its files come from.
    links : `tuple` [`tuple` [`str`, `str`], ...]
        Symbolic links to make in the folder, as (link name, target name).
    """

    part: str
    title: str
    relative_path: str
    wheels: tuple[PinnedWheel, ...]
    links: tuple[tuple[str, str], ...] = ()


def _cudnn_files(lib_folder: str, checksums: dict[str, tuple[str, int]]) -> tuple[PinnedFile, ...]:
    """Build the pinned files of a cuDNN wheel from its checksum table.

    Parameters
    ----------
    lib_folder : `str`
        The folder of the libraries inside the wheel.
    checksums : `dict` [`str`, `tuple` [`str`, `int`]]
        For each library name, its SHA-256 checksum and size.

    Returns
    -------
    files : `tuple` [`PinnedFile`, ...]
        One pinned file per library.
    """
    return tuple(
        PinnedFile(member=f"{lib_folder}/{name}", name=name, sha256=sha256, size_bytes=size)
        for name, (sha256, size) in checksums.items()
    )


# ---------------------------------------------------------------------------
# ~/cuda_libs: CUDA 12 libraries for the Siril Flatpak and for StarNet2
# ---------------------------------------------------------------------------

CUBLAS_CU12 = PinnedWheel(
    project="nvidia-cublas-cu12",
    license=CUDA_EULA,
    version="12.9.2.10",
    files=(
        PinnedFile(
            member="nvidia/cublas/lib/libcublas.so.12",
            name="libcublas.so.12",
            sha256="5757ab5839fb4f203ca47ecb336110d10f4a5606b1e097f195fbca89774569e2",
            size_bytes=105_140_976,
            # cuBLAS 12.8.5.5 from the Ollama CUDA 12 bundle.
            hand_installed_sha256=("18a5df540d156e9aff0363776fefab7884b614f122847b51c3eb1da772048e5c",),
        ),
        PinnedFile(
            member="nvidia/cublas/lib/libcublasLt.so.12",
            name="libcublasLt.so.12",
            sha256="2c9006a75c74b3bea2dc7ae2ec38ab038b0e45ea02cb4b717a915e8a5796acb1",
            size_bytes=749_210_000,
            # cuBLASLt 12.8.5.5 from the Ollama CUDA 12 bundle.
            hand_installed_sha256=("974927b9af78cadc0412ea3ab296da791af02c2dd0de5a35b5f0fbb8dc5d0a4d",),
        ),
    ),
)

CUDA_RUNTIME_CU12 = PinnedWheel(
    project="nvidia-cuda-runtime-cu12",
    license=CUDA_EULA,
    version="12.9.79",
    files=(
        PinnedFile(
            member="nvidia/cuda_runtime/lib/libcudart.so.12",
            name="libcudart.so.12",
            sha256="256e6409e4f06f618e1fb53d4844a6b81cdded1013afa8ade40c22f99eb133b7",
            size_bytes=741_088,
        ),
    ),
)

CUDA_NVRTC_CU12 = PinnedWheel(
    project="nvidia-cuda-nvrtc-cu12",
    license=CUDA_EULA,
    version="12.9.86",
    files=(
        PinnedFile(
            member="nvidia/cuda_nvrtc/lib/libnvrtc.so.12",
            name="libnvrtc.so.12",
            sha256="7c67c6b51ea0e0279634cebd676ff7efda1674806444520c84430ad5c35fe625",
            size_bytes=106_244_480,
        ),
    ),
)

CUFFT_CU12 = PinnedWheel(
    project="nvidia-cufft-cu12",
    license=CUDA_EULA,
    version="11.4.1.4",
    files=(
        PinnedFile(
            member="nvidia/cufft/lib/libcufft.so.11",
            name="libcufft.so.11",
            sha256="e1d65ebd08895f9d9883f848f3974f89e0130416252477b18835ba7f15d159bc",
            size_bytes=291_507_928,
        ),
    ),
)

CURAND_CU12 = PinnedWheel(
    project="nvidia-curand-cu12",
    license=CUDA_EULA,
    version="10.3.7.77",
    files=(
        PinnedFile(
            member="nvidia/curand/lib/libcurand.so.10",
            name="libcurand.so.10",
            sha256="12411c1e921cd3cac4e0d2f58024a3e6bbb7cea584d5cd768ad908ec762c5545",
            size_bytes=96_472_496,
            # cuRAND 10.4.3.29 copied from the CUDA 13.3 toolkit.
            hand_installed_sha256=("44f629e55711dc90bbb26616e09e92d7648e81e96d60d605cb360d9a8be29ca2",),
        ),
    ),
)

CUDNN_CU12 = PinnedWheel(
    project="nvidia-cudnn-cu12",
    license=CUDNN_LICENSE,
    version="9.24.0.43",
    files=_cudnn_files(
        "nvidia/cudnn/lib",
        {
            "libcudnn.so.9": ("0f43113ec6b171f93236560541f3b95104fed2516b19000a0e7047a5cc12f8ee", 129_240),
            "libcudnn_adv.so.9": (
                "6007f67989b1ac8d5bbd7129fa6200ba4ae4b5c0558a4dfe4d50c19ee59569cb",
                272_749_720,
            ),
            "libcudnn_cnn.so.9": (
                "2b78023b42c8c82018f01dc9f94fe29ec9ba350580dd53b1750501dcb2a34ecf",
                4_203_840,
            ),
            "libcudnn_engines_precompiled.so.9": (
                "c70e63247cdc14311b8b2efad0eb79674dd7297c27f9f23466ae4214cd415145",
                583_033_744,
            ),
            "libcudnn_engines_runtime_compiled.so.9": (
                "35ec33cfd4f42c6240d18d141d52af4cf9cafdae2bc9ba292390bbbfded7a172",
                39_878_128,
            ),
            "libcudnn_engines_tensor_ir.so.9": (
                "cca873cbd32645e7011a5272d9032827ebdb632166a063a1a1a96ea6831fbeb0",
                2_099_032,
            ),
            "libcudnn_ext.so.9": (
                "920c0fe6b74fe6a8eed85faa446230e1a555b8afc02e03638a0dfa264b4ae7be",
                115_361_960,
            ),
            "libcudnn_graph.so.9": (
                "dac0584e8f279e910bff4241e28b56efd399898038a5c94a0b6338bc9bc41d13",
                115_565_592,
            ),
            "libcudnn_heuristic.so.9": (
                "b4e69e7b97da5bd825f169f6d108fe71dc0352e99e9a0ffc7200401830c297cf",
                60_822_200,
            ),
            "libcudnn_ops.so.9": (
                "8b51427d9807335c5873e74a40425989a20dd74af0332c90449cf19853cf8013",
                106_964_632,
            ),
        },
    ),
)

CUDA_LIBS = LibraryFolder(
    part="cuda-libs",
    title="CUDA 12 libraries (~/cuda_libs)",
    relative_path="cuda_libs",
    wheels=(CUBLAS_CU12, CUDA_RUNTIME_CU12, CUDA_NVRTC_CU12, CUFFT_CU12, CURAND_CU12, CUDNN_CU12),
)
"""The CUDA 12 runtime, cuBLAS, cuFFT, cuRAND, NVRTC and cuDNN 9.

The Siril Flatpak finds them through ``LD_LIBRARY_PATH`` (set by the
override), which the GPU build of ONNX Runtime in Siril's Python needs. The
StarNet2 wrapper uses the same folder on the host.
"""


# ---------------------------------------------------------------------------
# StarNet2: the GPU build of ONNX Runtime 1.26
# ---------------------------------------------------------------------------

ONNXRUNTIME_GPU = PinnedWheel(
    project="onnxruntime-gpu",
    license=ONNXRUNTIME_LICENSE,
    version="1.26.0",
    python_version="3.13",
    platform="manylinux_2_28_x86_64",
    files=(
        PinnedFile(
            member="onnxruntime/capi/libonnxruntime.so.1.26.0",
            name="libonnxruntime.so.1.26.0",
            sha256="7fe6f3306ea455b3d1493ea8b95bf442b48a216e459c47564152e5f7d5ded2c6",
            size_bytes=23_101_400,
        ),
        PinnedFile(
            member="onnxruntime/capi/libonnxruntime_providers_cuda.so",
            name="libonnxruntime_providers_cuda.so",
            sha256="2e22b3a2cf96b00d22db581265ff73fa46b009c2bdb26455d4c118896410487f",
            size_bytes=400_827_896,
            # The same release taken from a wheel for another Python version.
            hand_installed_sha256=("b3d419caf0b6ecc3883d0f6d97190a325199fb72815e13c3d0668e590e2cc53f",),
        ),
        PinnedFile(
            member="onnxruntime/capi/libonnxruntime_providers_shared.so",
            name="libonnxruntime_providers_shared.so",
            sha256="086ec1d5388f64153d9c63470d126693db9a182c8ce236d3a1119068471b8a0d",
            size_bytes=14_632,
        ),
        PinnedFile(
            member="onnxruntime/LICENSE",
            name="LICENSE",
            sha256="2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c",
            size_bytes=1_073,
        ),
        PinnedFile(
            member="onnxruntime/ThirdPartyNotices.txt",
            name="ThirdPartyNotices.txt",
            sha256="0e07b95f3a8d6230037707c5c4a2b554d12c4cb67369669ac255635528ffcee2",
            size_bytes=325_054,
        ),
    ),
)
"""The GPU build of ONNX Runtime, a library that runs AI models.

StarNet2 2.6.2 needs exactly ONNX Runtime 1.26 (symbol version
``VERS_1.26.0``). The same wheel is installed into Siril's Python.
"""

STARNET_ORT = LibraryFolder(
    part="starnet-ort",
    title="ONNX Runtime GPU libraries for StarNet2",
    relative_path=".local/share/StarNetGPU/ort-gpu-1.26",
    wheels=(ONNXRUNTIME_GPU,),
    links=(("libonnxruntime.so.1", "libonnxruntime.so.1.26.0"),),
)


# ---------------------------------------------------------------------------
# GraXpert 4: cuDNN built for CUDA 13
# ---------------------------------------------------------------------------

CUDNN_CU13 = PinnedWheel(
    project="nvidia-cudnn-cu13",
    license=CUDNN_LICENSE,
    version="9.28.1.4",
    files=_cudnn_files(
        "nvidia/cudnn/lib",
        {
            "libcudnn.so.9": ("f756cabeed9b481134769fdbc0c2ae022cf52ac39aab08be26ea990a56da2efc", 137_432),
            "libcudnn_adv.so.9": (
                "d69ed7d8adc4524ddb6cbf5e10211e35f580d14e40924692f0cdf1b78cf23ab2",
                92_392_360,
            ),
            "libcudnn_cnn.so.9": (
                "2512f47e6e33000e12be5b2e59ac71eb8f5805a548561e7637e4413d1b7cdeb7",
                4_204_144,
            ),
            "libcudnn_engines_precompiled.so.9": (
                "51bd1d25591ae2994120aac5c5be172e31b973e0fdeba43a50647949096ee40a",
                260_072_128,
            ),
            "libcudnn_engines_runtime_compiled.so.9": (
                "738655d1b9441a752fde9b9911780635e60fe96113f2b06c1af6d70458ef5174",
                41_978_752,
            ),
            "libcudnn_engines_tensor_ir.so.9": (
                "ff088c803f896fd9a92c0ab861026ff3b7ee5a4222713c3dd8221e1c843e74ec",
                249_581_552,
            ),
            "libcudnn_ext.so.9": (
                "46c273e178b7733ffa5d56bd9719f378a20b94e07d2ccf23c4078e22bced0652",
                10_492_592,
            ),
            "libcudnn_graph.so.9": (
                "06c28f04f028466cf33461a7d645210146015d425a0dae6c7dc7e60c07c4d973",
                149_118_552,
            ),
            "libcudnn_heuristic.so.9": (
                "7738da4dd6fee9597680ff595863743b2bc4bd5b0ec3edb4e8e9d28a2b5e629d",
                96_473_968,
            ),
            "libcudnn_ops.so.9": (
                "e849b0a7353eec5bb5c9f75d841d075d786e205d84f8fd07b092770554674e48",
                35_661_648,
            ),
        },
    ),
)

GRAXPERT_CUDNN = LibraryFolder(
    part="graxpert-cudnn",
    title="cuDNN for CUDA 13, for GraXpert",
    relative_path=".local/share/GraXpert/cudnn13/nvidia/cudnn/lib",
    wheels=(CUDNN_CU13,),
)
"""cuDNN 9 built for CUDA 13.

GraXpert 4 is built for CUDA 13 and aborts with a cuDNN built for CUDA 12,
which is what the system package may supply. The CUDA 13 runtime and cuBLAS
themselves come from the system's CUDA 13 toolkit (``HOST_CUDA13_LIBRARIES``).
"""

HOST_CUDA13_LIBRARIES = (
    "/usr/local/cuda/lib64/libcudart.so.13",
    "/usr/local/cuda/lib64/libcublas.so.13",
    "/usr/local/cuda/lib64/libcublasLt.so.13",
)
"""System CUDA 13 libraries that GraXpert 4 needs on the host.

They come from NVIDIA's CUDA 13 package repository (``cuda-libraries-13-x``),
which needs administrator rights, so the setup only checks for them.
"""

LIBRARY_FOLDERS = (CUDA_LIBS, STARNET_ORT, GRAXPERT_CUDNN)

PINNED_WHEELS = tuple(dict.fromkeys(wheel for folder in LIBRARY_FOLDERS for wheel in folder.wheels))
"""Every pinned wheel, each once, in the order the folders list them."""

LICENSES_TO_ACCEPT = tuple(
    dict.fromkeys(
        wheel.license for wheel in PINNED_WHEELS if wheel.license and wheel.needs_license_acceptance
    )
)
"""The licenses that are not open source, which the user must accept before
the setup downloads a wheel under them: NVIDIA's CUDA and cuDNN agreements."""


# ---------------------------------------------------------------------------
# Flatpak permission overrides for org.siril.Siril
# ---------------------------------------------------------------------------

OVERRIDE_FILESYSTEMS = ("~/cuda_libs:ro", "!~/.astropy")
"""Folders shared with (or hidden from) the Siril sandbox.

``~/cuda_libs`` is shared read-only so Siril's Python finds the CUDA
libraries. ``~/.astropy`` is hidden, so the Astropy in Siril's Python keeps
its own cache and settings instead of the host's.
"""

OVERRIDE_DEVICES = ("all", "dri")
"""Devices for the sandbox: ``all`` includes the NVIDIA device files."""


OVERRIDE_ENVIRONMENT = (
    ("LD_LIBRARY_PATH", "{home}/cuda_libs"),
    ("TMPDIR", "{home}/.var/app/org.siril.Siril/cache/tmp"),
    ("TF_FORCE_GPU_ALLOW_GROWTH", "true"),
)
"""Environment variables set inside the sandbox, as (name, value).

``{home}`` stands for the home folder, because Flatpak does not expand ``~``
in environment values. ``LD_LIBRARY_PATH`` points at ``~/cuda_libs``.
``TMPDIR`` keeps large temporary files on disk instead of the RAM-backed
``/tmp``.
``TF_FORCE_GPU_ALLOW_GROWTH`` stops TensorFlow-based tools from taking all of
the graphics card's memory at start.
"""

REQUIRED_TALK_NAMES = ("org.freedesktop.Flatpak",)
"""Session-bus names the sandbox must reach: ``flatpak-spawn --host``, which
the wrappers use, talks to ``org.freedesktop.Flatpak``. Siril's own Flathub
manifest grants it; the override adds it only when it is missing."""


# ---------------------------------------------------------------------------
# Siril's Python environment
# ---------------------------------------------------------------------------

SIRIL_VENV_RELATIVE_PATH = ".var/app/org.siril.Siril/data/siril/venv"
"""Siril's Python environment, relative to the home folder. Siril makes it
the first time it runs a Python script."""

SIRIL_PYTHON_VERSION = "3.13"
"""The Python version of Siril's environment that these pins are for."""

SIRIL_PYTHON_PACKAGES = (
    ("onnxruntime-gpu", "1.26.0"),
    ("flatbuffers", "25.12.19"),
    ("numpy", "2.4.2"),
    ("packaging", "26.0"),
    ("protobuf", "7.34.0"),
    ("sympy", "1.14.0"),
    ("mpmath", "1.3.0"),
)
"""The GPU build of ONNX Runtime and the packages it needs, with exact
versions. Siril's GraXpert-AI and Cosmic Clarity scripts run their models with
it. ``onnxruntime-gpu`` is installed from the checked wheel
(`ONNXRUNTIME_GPU`); the others are installed by version from PyPI."""

CONFLICTING_PYTHON_PACKAGES = ("onnxruntime",)
"""Packages that hide the GPU build when installed beside it: the CPU-only
``onnxruntime`` uses the same import name."""

SIRIL_MANAGED_PACKAGES = ("sirilpy",)
"""Packages that Siril installs and updates itself; reported only."""


# ---------------------------------------------------------------------------
# Wrappers and settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Wrapper:
    """A script that starts a GPU program, inside or outside the sandbox.

    Attributes
    ----------
    name : `str`
        The program's name, for the report.
    template : `str`
        The template's file name in ``build/linux/siril_gpu/templates``.
    relative_path : `str`
        Where the wrapper is installed, relative to the home folder.
    default_program : `str`
        The program the wrapper starts, relative to the home folder, unless
        the command line names another one.
    """

    name: str
    template: str
    relative_path: str
    default_program: str


GRAXPERT_WRAPPER = Wrapper(
    name="GraXpert",
    template="graxpert-gpu.sh.in",
    relative_path=".local/share/GraXpert/graxpert-gpu.sh",
    default_program="Applications/GraXpert-linux/GraXpert",
)

STARNET_WRAPPER = Wrapper(
    name="StarNet2",
    template="starnet-gpu.sh.in",
    relative_path=".local/share/StarNetGPU/starnet-gpu.sh",
    default_program="Applications/starnet2_linux_2.6.2-0242_ORT_x64_cli/starnet2",
)

APP_CONFIG_SECTION = "Processing.GraXpert"
"""The section of ``astrometrics.config.toml`` that names GraXpert."""

APP_CONFIG_KEY = "graxpert_executable"
"""The setting that names GraXpert."""

SIRIL_CONFIG_RELATIVE_FOLDER = ".var/app/org.siril.Siril/config/siril"
"""Where the Flatpak Siril keeps its settings (``config.<version>.ini``)."""

SIRIL_CONFIG_SECTION = "core"
"""The section of Siril's settings that names the outside programs."""

SIRIL_CONFIG_KEYS = (("starnet_exe", STARNET_WRAPPER), ("graxpert_path", GRAXPERT_WRAPPER))
"""Siril's settings for StarNet and GraXpert, with the wrapper each names."""
