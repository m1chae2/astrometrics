# Third-Party Notices

Astrometrics is released under the [GNU General Public License, version 3 or later](LICENSE) (GPL-3.0-or-later). This file lists the work of others that the project uses: the programs its setup scripts install or download, the programs the app runs, the GPU libraries, the data sets and online services the app reads, and the Python and npm packages. Each item stays under its own license, which this file names and links. Each row covers an item as its own project ships it; anything that project bundles or downloads for itself (camera makers' SDKs inside INDI's drivers, Chromium inside Electron, AI models a program fetches) is covered by that project's own notices and is not listed again here.

The source code in this repository does not include these programs, libraries or data sets, except the files listed under [Files in this repository](#files-in-this-repository). The app runs each outside program as a separate program. When you run a setup script, you install each item from its own project, under its own license, and you accept that license. Read the license of each item before you install it. Some of them are not open source.

The licenses below were read on 2026-11-27 from each project's own license file, website or PyPI record, linked in each row. A license marked *not confirmed* has no published terms that could be found; the row says what was found. This file is a summary, not legal advice. Each linked license is the binding text.

## Files in this repository

These files are copies or extracts of the work of others, kept in the repository.

| Item | Version | Where | License | Open source | Notes |
| --- | --- | --- | --- | --- | --- |
| Hipparcos bright-star extract | Not recorded | `wayfindinglib/drivers/catalog/bright_star_catalog.sqlite` | [CC BY-NC 3.0 IGO](https://www.cosmos.esa.int/web/hipparcos/catalogues) (ESA) | Open data, non-commercial | Positions and magnitudes of 82,984 stars from the ESA Hipparcos catalog. ESA asks for the credit "Credit: ESA" and the reference ESA (1997), *The Hipparcos and Tycho Catalogues*, ESA SP-1200. Commercial use needs ESA's permission. |
| d3-celestial constellation lines | Not recorded | `wayfindinglib/drivers/catalog/constellation_lines.json` | [BSD-3-Clause](https://github.com/ofrohn/d3-celestial/blob/master/LICENSE) | Yes | Copyright (c) 2015, Olaf Frohn. The file carries the notice in its `_attribution` field. |
| Plotly.js | 4.3.0 | `public/js/plotly.min.js` | [MIT](https://github.com/plotly/plotly.js/blob/master/LICENSE) | Yes | Copyright 2012-2026, Plotly, Inc. A copy of the `plotly.js-dist-min` npm package. |

The packaged Linux app (`build/linux/build_installer.sh`) is different from the source checkout: it does bundle the Python packages (with PyInstaller), Electron, the npm production packages and Aladin Lite. Whoever distributes that package must ship the licenses of what it contains. See [Python packages](#python-packages) for the copyleft packages among them.

## Programs the setup installs or downloads

`build/linux/install_ubuntu_deps.sh`, `build/linux/setup_venv.sh`, `build/linux/setup_wsl.sh` and `build/windows/setup_windows.ps1` install these. Rows with a version are pinned in the code. The rest come at whatever version the package source offers.

| Item | Version | Source | License | Open source | Notes |
| --- | --- | --- | --- | --- | --- |
| Siril (Windows) | 1.4.5 | [free-astro.org](https://siril.org/download/), checked against a pinned SHA-256 | [GPL-3.0](https://gitlab.com/free-astro/siril/-/blob/master/LICENSE.md) | Yes | Pinned in `astrometricslib/_internal/drivers/program_setup.py`. Stacks the frames. |
| GraXpert (Windows) | 3.0.2 | [GitHub releases](https://github.com/Steffenhir/GraXpert/releases/tag/3.0.2), checked against a pinned SHA-256 | [GPL-3.0](https://github.com/Steffenhir/GraXpert/blob/main/License.md) | Yes | Pinned in `program_setup.py`. Removes the sky gradient from previews. |
| Siril (Linux) | Ubuntu's, or Flathub's `org.siril.Siril` | Ubuntu archive or [Flathub](https://flathub.org/apps/org.siril.Siril) | [GPL-3.0](https://gitlab.com/free-astro/siril/-/blob/master/LICENSE.md) | Yes | `install_ubuntu_deps.sh --siril-source=apt` or `--siril-source=flatpak`. |
| Flatpak and the Flathub repository | Ubuntu's | Ubuntu archive; [flathub.org](https://flathub.org) | [LGPL-2.1](https://github.com/flatpak/flatpak/blob/main/COPYING) | Yes | Only with `--siril-source=flatpak`. Each Flathub app has its own license; Flathub's terms were *not confirmed* (the page could not be read). |
| INDI library and server | 2 or newer | [ppa:mutlaqja/ppa](https://launchpad.net/~mutlaqja/+archive/ubuntu/ppa) | [LGPL-2.1](https://github.com/indilib/indi/blob/master/LICENSE) | Yes | Telescope-control protocol and drivers. |
| INDI third-party drivers | PPA's `indi-3rdparty-drivers` | ppa:mutlaqja/ppa | [LGPL-2.1](https://github.com/indilib/indi-3rdparty) for the repository | Yes | |
| pyindi-client | Newest on PyPI | [PyPI](https://pypi.org/project/pyindi-client/) | [GPL-3.0-or-later](https://github.com/indilib/pyindi-client/blob/master/LICENSE) | Yes | Python binding to INDI that `wayfindinglib` imports. See [Python packages](#python-packages). |
| astrometry.net (`solve-field`) | Ubuntu's | Ubuntu archive | [BSD-3-Clause, GPL-3.0-or-later as a whole](https://github.com/dstndstn/astrometry.net/blob/main/LICENSE) | Yes | Only with `--with-local-solver`. The team's own code is BSD-3-Clause; because it uses GPL libraries, the combined program is under the GPL. |
| Tycho-2 index files (`astrometry-data-tycho2-*`) | Ubuntu's | Ubuntu archive | [BSD-2-Clause](https://sources.debian.org/src/tycho2/2-4/debian/copyright/) | Yes | Only with `--with-local-solver`. Index files from [data.astrometry.net](https://data.astrometry.net) that you download yourself state no license (*not confirmed*); they are built from Tycho-2 and Gaia. |
| Python 3.14 | Ubuntu's, or the deadsnakes PPA | Ubuntu archive or [ppa:deadsnakes/ppa](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa) | [PSF-2.0](https://docs.python.org/3/license.html) | Yes | The PPA describes itself as having no guarantee of timely security updates. |
| Node.js 24 | Ubuntu's, or NodeSource | Ubuntu archive or [NodeSource](https://github.com/nodesource/distributions) | [MIT](https://github.com/nodejs/node/blob/main/LICENSE) | Yes | `setup_wsl.sh` adds the NodeSource repository, whose scripts are [MIT](https://github.com/nodesource/distributions/blob/master/LICENSE.md). |
| Electron | 45.4.1 | npm | [MIT](https://github.com/electron/electron/blob/main/LICENSE) | Yes | Draws the app window. |
| Vale | Newest the `vale` package fetches | GitHub releases, fetched by the PyPI `vale` package | [MIT](https://github.com/errata-ai/vale/blob/v3/LICENSE) | Yes | Prose linter for the documentation, in the `dev` extra. |
| Build tools and libraries | Ubuntu's | Ubuntu archive | Each package's own | Yes | `build-essential`, `swig`, `libcfitsio-dev`, `libnova-dev`, `libdbus-1-dev`, `libglib2.0-dev` and others. Each package's terms are in `/usr/share/doc/<package>/copyright`. |
| Aladin Lite | Newest v3 | [aladin.cds.unistra.fr](https://aladin.cds.unistra.fr/AladinLite/) | [LGPL-3.0](https://github.com/cds-astro/aladin-lite/blob/master/LICENSE) | Yes | Downloaded only by the packaging script `build/packaging/fetch_aladin.sh`. |

## Programs the app runs that you install yourself

The app runs these when you set their paths in `astrometrics.config.toml`, or reads their files. No setup script installs them.

| Item | Source | License | Open source | Notes |
| --- | --- | --- | --- | --- |
| Cosmic Clarity (Seti Astro) | [setiastro.com](https://www.setiastro.com/cosmic-clarity) | [MIT](https://github.com/setiastro/cosmicclarity/blob/main/LICENSE) | Yes | Copyright (c) 2024 Franklin Marek. The code repository is archived. |
| StarNet2 command-line program | [starnetastro.com](https://www.starnetastro.com) | *Not confirmed* | No (closed binary) | Copyright Mikita Misiura. The site publishes no license or terms, only "All rights reserved". The older StarNet v1 code is [MIT](https://github.com/nekitmm/starnet/blob/master/LICENSE.md). |
| Siril's GraXpert-AI and Cosmic Clarity scripts | [siril-scripts](https://gitlab.com/free-astro/siril-scripts) | GPL-3.0-or-later for those scripts | Yes | The repository is [MIT](https://gitlab.com/free-astro/siril-scripts/-/blob/main/LICENSE.md), but these scripts carry their own GPL-3.0-or-later notice. |
| PHD2 | [openphdguiding.org](https://openphdguiding.org) | [BSD-3-Clause](https://github.com/OpenPHDGuiding/phd2/blob/master/LICENSE.txt) | Yes | The guiding program. The app talks to it over its network interface; the simulator starts the real PHD2 when it is installed. |
| KStars and Ekos | [kstars.kde.org](https://kstars.kde.org) | [GPL-2.0-or-later](https://invent.kde.org/education/kstars) | Yes | The app reads Ekos log files. |
| NVIDIA CUDA 13 toolkit libraries | [NVIDIA](https://developer.nvidia.com/cuda-downloads) | [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) | No | GraXpert 4 needs them in `/usr/local/cuda`. `setup_siril_gpu.sh` checks for them but does not install them. |
| rclone | [rclone.org](https://rclone.org) | [MIT](https://github.com/rclone/rclone/blob/master/COPYING) | Yes | Only the optional backup scripts use it. |
| rsync, fuse-overlayfs | Ubuntu archive | GPL-3.0-or-later; GPL-2.0-or-later | Yes | Frame downloads and the simulator. |

## GPU libraries for the Flatpak Siril

`build/linux/setup_siril_gpu.sh` downloads these wheels from PyPI at the versions pinned in `build/linux/siril_gpu/siril_gpu_pins.py`, and checks each file against a pinned SHA-256 checksum. The NVIDIA wheels are NVIDIA's proprietary software. Before the script downloads the first of them, it shows NVIDIA's two license agreements and asks you to accept them; without a terminal it needs `--accept-nvidia-license`. Among other terms, the agreements limit use to NVIDIA graphics cards, forbid reverse engineering, and allow redistribution only of listed runtime files inside an application that adds substantial function. Read the full agreements before you accept.

| Wheel | Version | License | Open source | Notes |
| --- | --- | --- | --- | --- |
| `nvidia-cublas-cu12` | 12.9.2.10 | [NVIDIA CUDA Toolkit End User License Agreement](https://docs.nvidia.com/cuda/eula/index.html) | No | PyPI: `LicenseRef-NVIDIA-Proprietary`. |
| `nvidia-cuda-runtime-cu12` | 12.9.79 | [NVIDIA CUDA Toolkit End User License Agreement](https://docs.nvidia.com/cuda/eula/index.html) | No | PyPI: `LicenseRef-NVIDIA-Proprietary`. |
| `nvidia-cuda-nvrtc-cu12` | 12.9.86 | [NVIDIA CUDA Toolkit End User License Agreement](https://docs.nvidia.com/cuda/eula/index.html) | No | PyPI: `LicenseRef-NVIDIA-Proprietary`. |
| `nvidia-cufft-cu12` | 11.4.1.4 | [NVIDIA CUDA Toolkit End User License Agreement](https://docs.nvidia.com/cuda/eula/index.html) | No | PyPI: `LicenseRef-NVIDIA-Proprietary`. |
| `nvidia-curand-cu12` | 10.3.7.77 | [NVIDIA CUDA Toolkit End User License Agreement](https://docs.nvidia.com/cuda/eula/index.html) | No | PyPI: "NVIDIA Proprietary Software". |
| `nvidia-cudnn-cu12` | 9.24.0.43 | [NVIDIA cuDNN Software License Agreement](https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html) | No | The PyPI license field is empty; NVIDIA's cuDNN agreement applies. |
| `nvidia-cudnn-cu13` | 9.28.1.4 | [NVIDIA cuDNN Software License Agreement](https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html) | No | The PyPI license field is empty; NVIDIA's cuDNN agreement applies. |
| `onnxruntime-gpu` | 1.26.0 | [MIT](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) | Yes | Microsoft. The setup also copies the wheel's own `LICENSE` and `ThirdPartyNotices.txt`. |

The script also installs these packages into Siril's own Python environment, from PyPI, at these versions.

| Package | Version | License | Open source |
| --- | --- | --- | --- |
| `flatbuffers` | 25.12.19 | [Apache-2.0](https://github.com/google/flatbuffers/blob/master/LICENSE) | Yes |
| `numpy` | 2.4.2 | [BSD-3-Clause](https://github.com/numpy/numpy/blob/main/LICENSE.txt) | Yes |
| `packaging` | 26.0 | [Apache-2.0 or BSD-2-Clause](https://github.com/pypa/packaging/blob/main/LICENSE) | Yes |
| `protobuf` | 7.34.0 | [BSD-3-Clause](https://github.com/protocolbuffers/protobuf/blob/main/LICENSE) | Yes |
| `sympy` | 1.14.0 | [BSD-3-Clause](https://github.com/sympy/sympy/blob/master/LICENSE) | Yes |
| `mpmath` | 1.3.0 | [BSD-3-Clause](https://github.com/mpmath/mpmath/blob/master/LICENSE) | Yes |

## Data sets and online services

The app sends questions to these services, or reads data files that other programs install. Each service has its own terms of use.

| Item | Used for | Terms | Open | Notes |
| --- | --- | --- | --- | --- |
| Siril Astrometry Catalogue extracted from Gaia DR3 (Zenodo [14692304](https://doi.org/10.5281/zenodo.14692304)) | Siril's offline plate solving and color calibration, when installed | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Yes | Installed by Siril's catalog installer script, or by this project's `setup_siril_catalog.sh` (Linux) and Windows setup, which download the same Zenodo file and check it against a pinned checksum. Attribution below. It is derived from Gaia, so the Gaia terms also apply. |
| Siril SPCC database | Siril's color calibration | [GPL-3.0](https://gitlab.com/free-astro/siril-spcc-database) | Yes | Installed by Siril. |
| ESA Gaia archive (Gaia DR3 cone searches and XP spectra) | Star positions, magnitudes and spectra | [CC BY-NC 3.0 IGO](https://www.cosmos.esa.int/web/gaia-users/license) | Open data, non-commercial | Acknowledgement below. Commercial use needs ESA's permission ([terms](https://www.cosmos.esa.int/web/esdc/terms-and-conditions)). |
| SIMBAD (CDS, Strasbourg) | Object names, types and positions | [ODbL](https://cds.unistra.fr/legals/), free without registration | Yes | CDS asks users to cite Wenger et al. (2000), A&AS 143, 9, and the acknowledgement below. |
| VizieR (CDS, Strasbourg): Gaia DR3 (`I/355`), Gaia DR3 variability (`I/358`), AAVSO VSX (`B/vsx`) | Catalog queries | [CDS legal notice](https://cds.unistra.fr/legals/): each catalog keeps its own license | Mostly | Cite Ochsenbein et al. (2000), A&AS 143, 23. VSX states no license (*not confirmed*); AAVSO's acknowledgement is below. |
| CDS TAP service (`tapvizier.u-strasbg.fr`) | Siril's online Gaia queries | The same CDS terms | Yes | The app only checks that the service answers. |
| SkyBoT (IMCCE) | Known asteroids in a field | [Acknowledgement requested](https://ssp.imcce.fr/webservices/skybot/) | Service | Cite Berthier et al. (2006), ASP Conf. Ser. 351, 367. |
| Astrometry.net online service (`nova.astrometry.net`) | Online plate solving, with your API key | [Upload terms](https://nova.astrometry.net/upload) | Service | Images you upload can be viewed publicly. The upload page licenses anonymous uploads under CC BY 3.0; a signed-in account sets its own choice. No separate terms of use page was found. |

### Acknowledgements to include when you publish results

**Gaia.** ESA's license page asks every user to acknowledge the Gaia mission and DPAC, and each data release has its own wording. The DR3 acknowledgement page could not be read on 2026-11-27, so check this standard wording against [the DR3 acknowledgements](https://gea.esac.esa.int/archive/documentation/GDR3/Miscellaneous/sec_acknowl/) before you publish:

> This work has made use of data from the European Space Agency (ESA) mission Gaia (<https://www.cosmos.esa.int/gaia>), processed by the Gaia Data Processing and Analysis Consortium (DPAC, <https://www.cosmos.esa.int/web/gaia/dpac/consortium>). Funding for the DPAC has been provided by national institutions, in particular the institutions participating in the Gaia Multilateral Agreement.

For images and data products, ESA's archive terms ask for the credit line "Credit: ESA, Gaia DPAC".

**Siril Gaia catalog (CC BY 4.0).** Cass, I., Knagg-Baugh, A., Melis, C., and Richard, C. (2025), "Siril Astrometry Catalogue extracted from Gaia DR3", version 1.0.0, Zenodo, <https://doi.org/10.5281/zenodo.14692304>, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

**SIMBAD.**

> This research has made use of the SIMBAD database, operated at CDS, Strasbourg, France.

**VizieR.** CDS's VizieR pages could not be read on 2026-11-27; this is the wording CDS gives for VizieR, so check it on [VizieR](https://vizier.cds.unistra.fr) before you publish:

> This research has made use of the VizieR catalogue access tool, CDS, Strasbourg, France (DOI: 10.26093/cds/vizier).

**AAVSO VSX.**

> This research has made use of the International Variable Star Index (VSX) database, operated at AAVSO, Cambridge, Massachusetts, USA.

**SkyBoT.**

> This research has made use of LTE SkyBoT VO tool

**Hipparcos.** "Credit: ESA", and ESA (1997), *The Hipparcos and Tycho Catalogues*, ESA SP-1200.

## Python packages

`setup_venv.sh` installs the project's Python dependencies from PyPI; `pyproject.toml` lists them. The project's virtual environment, with the `mcp` and `dev` extras, held 224 packages on 2026-11-27, read from each package's installed metadata:

| License family | Packages |
| --- | --- |
| MIT | 108 |
| BSD (2- and 3-clause) | 83 |
| Apache-2.0 | 18 |
| Python Software Foundation | 4 |
| ISC | 3 |
| MPL-2.0 (file-level copyleft) | 3 (`certifi`, `pathspec`, `tqdm`) |
| LGPL | 1 (`sep`) |
| GPL | 4 (`pyindi-client`, `pyinstaller`, `pyinstaller-hooks-contrib`, `docutils`) |

The copyleft packages, and how the project uses each one:

| Package | Version | License | How the project uses it | Effect on a GPL-3.0-or-later project |
| --- | --- | --- | --- | --- |
| `pyindi-client` | 2.2.0 | GPL-3.0-or-later | `wayfindinglib`'s INDI drivers import it. It is optional: without it, a stand-in that never connects is used. | None now: the project is itself GPL-3.0-or-later, so a package that bundles `pyindi-client`, such as the PyInstaller build, is distributed under GPL-3.0 terms as a whole, as the project's own license already requires. (This was the reason the project moved from MIT to GPL-3.0-or-later on 2026-11-28.) |
| `sep` | 1.4.1 | LGPL-3.0-or-later | `astroalign` imports it; the project does not import it directly. | Low. LGPL allows use from non-GPL code. A bundled build must ship its license and let the user replace it. |
| `rawpy` | 0.27.0 | MIT | The project imports `rawpy` to read camera raw files. | None. |
| `certifi`, `pathspec`, `tqdm` | Installed versions | MPL-2.0 | Imported by dependencies. | None unless their files are changed. |
| `pyinstaller`, `pyinstaller-hooks-contrib` | 6.22.0, 2026.6 | GPL-2.0 with an exception for bundled programs; GPL-2.0 or Apache-2.0 | Build tool for the packaged app, in the `dev` extra. | None: PyInstaller's license exception lets it package programs under any license. |
| `docutils` | 0.22.4 | Public domain and BSD, with a few GPL files | Used by Sphinx to build the documentation. | None: a build tool, not imported by the app. |

The separate programs above (Siril, GraXpert, astrometry.net, rsync and others) are under the GPL too, but the app starts them as separate programs and exchanges files with them. That use does not make the app a combined work.

## npm packages

`npm install` installs the frontend packages listed in `package.json`; `package-lock.json` pins their versions. The lock file listed 943 packages on 2026-11-27, 237 of them needed at run time.

| License | Run-time packages |
| --- | --- |
| MIT | 221 |
| ISC | 10 |
| BSD-2-Clause and BSD-3-Clause | 4 |
| Apache-2.0 | 1 |
| Python-2.0 (`argparse`) | 1 |
| BlueOak-1.0.0 (`sax`) | 1 |

No run-time package has a copyleft license. Among the development-only packages: `axe-core` and the `lightningcss` packages are MPL-2.0; `caniuse-lite` is CC-BY-4.0 data; `jszip` is MIT or GPL-3.0-or-later (the project uses it under MIT); and `sentry` 0.45.0, which `@sentry/bundler-plugins` pulls in, is under the Functional Source License (FSL-1.1-Apache-2.0). The FSL is not an open-source license until each release converts to Apache-2.0 two years after it is published. The UI MCP server (`ui/mcp`) has its own lock file with 119 packages: 87 MIT, 22 Apache-2.0, 7 ISC and 3 BSD.

`astrometricslib/test/test_third_party_notices.py` checks that every pinned program release and every pinned GPU wheel has a row in this file.
