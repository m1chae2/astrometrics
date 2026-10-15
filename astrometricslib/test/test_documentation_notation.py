r"""Purpose: Keep the mathematical notation of the review guides consistent.

Description: The architecture paper's Appendix B is the one list of symbols
for the documentation set (``documentation/Documentation_Conventions.md``,
Section 6.4). Each pipeline review guide restates some of the paper's
equations and adds symbols of its own, which it lists in a "Notation"
subsection generated from ``GUIDE_NOTATION`` in
``build/documentation/generate_pipeline_review_docs.py``. These tests check:

- every symbol in a guide's math is in Appendix B or in that guide's table;
- every row of a guide's table is used by the guide;
- no guide's table redefines a symbol Appendix B already defines;
- a symbol two guides both define means the same thing in both;
- a one-letter lowercase subscript on a guide's own symbol is one of the
  paper's index letters.

A symbol is a base (a letter, a Greek letter, or a decorated one such as
``\hat{F}``) with its subscript; superscripts such as powers are ignored.
These tests cannot tell whether a guide gives a paper symbol a different
meaning while writing it the same way. That still needs a reader.
"""

import re
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_DIR = REPO_ROOT / "documentation" / "library_design"
ARCHITECTURE = DESIGN_DIR / "Astrometrics_Library_Architecture.md"
GUIDES = sorted((DESIGN_DIR / "pipelines").glob("*.md"))

GREEK = frozenset({
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta",
    "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "rho", "sigma", "tau",
    "upsilon", "phi", "varphi", "chi", "psi", "omega", "Gamma", "Theta", "Lambda", "Xi",
    "Phi", "Psi", "Omega", "ell", "star",
})  # fmt: skip
# Commands that put a mark on one symbol: \hat{F} is a symbol of its own.
DECORATIONS = frozenset({"hat", "bar", "tilde", "dot", "ddot", "mathbf", "vec", "overline", "widehat"})
# Commands whose argument is a word or a label, not a symbol.
WORD_COMMANDS = frozenset({
    "text",
    "mathrm",
    "operatorname",
    "textrm",
    "mathit",
    "tag",
    "begin",
    "end",
    "label",
})
NOTATION_HEADING = re.compile(r"^### [\d.]+ Notation$", re.MULTILINE)

Symbol = tuple[str, str]


def math_spans(text: str) -> list[tuple[int, str]]:
    """Find every piece of LaTeX math in a Markdown text.

    Parameters
    ----------
    text : `str`
        A Markdown document.

    Returns
    -------
    spans : `list` [`tuple` [`int`, `str`]]
        The 1-based line and the LaTeX of each ``$$...$$`` block and each
        ``$...$`` span. Inline code is skipped, so a ``$`` inside backticks
        is not read as math.
    """
    display = re.compile(r"\$\$(.+?)\$\$", flags=re.DOTALL)
    spans = [(text.count("\n", 0, match.start()) + 1, match.group(1)) for match in display.finditer(text)]
    inline_text = display.sub(lambda match: "\n" * match.group(0).count("\n"), text)
    inline_text = re.sub(r"`[^`\n]*`", "", inline_text)
    for match in re.finditer(r"(?<![\\$])\$([^$\n]+?)\$", inline_text):
        spans.append((inline_text.count("\n", 0, match.start()) + 1, match.group(1)))
    return spans


def _read_group(latex: str, index: int) -> tuple[str, int]:
    """Read one argument: a braced group, a command, or a single character.

    Parameters
    ----------
    latex : `str`
        The LaTeX being read.
    index : `int`
        Where the argument starts. Spaces before it are skipped.

    Returns
    -------
    content : `str`
        The argument without its outer braces.
    end : `int`
        The index just after the argument.
    """
    while index < len(latex) and latex[index] == " ":
        index += 1
    if index >= len(latex):
        return "", index
    if latex[index] == "{":
        depth = 0
        for end in range(index, len(latex)):
            if latex[end] == "{":
                depth += 1
            elif latex[end] == "}":
                depth -= 1
                if depth == 0:
                    return latex[index + 1 : end], end + 1
        return latex[index + 1 :], len(latex)
    if latex[index] == "\\":
        command = re.match(r"\\([A-Za-z]+|.)", latex[index:])
        assert command is not None
        return command.group(0), index + len(command.group(0))
    return latex[index], index + 1


def _subscript_label(raw: str) -> str:
    r"""Write a subscript in one standard form.

    Parameters
    ----------
    raw : `str`
        The subscript as written, such as ``\text{ens}`` or ``j+1``.

    Returns
    -------
    label : `str`
        The subscript without ``\text`` wrappers or spaces. Index arithmetic
        (``j+1``, ``i-1``) is read as the index itself.
    """
    label = re.sub(r"\\(?:text|mathrm)\s*\{([^}]*)\}", r"\1", raw.strip())
    label = re.sub(r"\s+", "", label)
    return re.sub(r"^([a-z])[+-]\d+$", r"\1", label)


def _read_scripts(latex: str, index: int) -> tuple[str, int]:
    """Read the sub- and superscripts after a symbol.

    Parameters
    ----------
    latex : `str`
        The LaTeX being read.
    index : `int`
        The index just after the symbol's base.

    Returns
    -------
    subscript : `str`
        The subscript in standard form, or ``""``. Superscripts are dropped.
    end : `int`
        The index just after the last script.
    """
    subscript = ""
    while True:
        lookahead = index
        while lookahead < len(latex) and latex[lookahead] == " ":
            lookahead += 1
        if lookahead >= len(latex) or latex[lookahead] not in "_^":
            return subscript, index
        marker = latex[lookahead]
        content, index = _read_group(latex, lookahead + 1)
        if content in ("\\text", "\\mathrm"):
            word, index = _read_group(latex, index)
            content = f"\\text{{{word}}}"
        if marker == "_":
            subscript = _subscript_label(content)


def symbols(latex: str) -> list[Symbol]:
    r"""List the symbols written in one piece of LaTeX.

    Parameters
    ----------
    latex : `str`
        A math span, without its dollar signs.

    Returns
    -------
    found : `list` [`tuple` [`str`, `str`]]
        Each symbol as (base, subscript), in order. A word in ``\text`` (RMS,
        median) is not a symbol, nor is a function such as ``\sin``. A
        ``\Delta`` stays on the symbol it applies to, so ``\Delta t`` is one
        symbol.
    """
    found: list[Symbol] = []
    index = 0
    while index < len(latex):
        character = latex[index]
        if character == "\\":
            command = re.match(r"\\([A-Za-z]+)", latex[index:])
            if command is None:
                index += 2
                continue
            name = command.group(1)
            index += len(command.group(0))
            if name in DECORATIONS:
                inner, index = _read_group(latex, index)
                base = f"\\{name}{{{inner.strip()}}}"
            elif name in WORD_COMMANDS:
                _, index = _read_group(latex, index)
                while index < len(latex) and latex[index] in "_^":
                    content, index = _read_group(latex, index + 1)
                    if content in ("\\text", "\\mathrm"):
                        _, index = _read_group(latex, index)
                continue
            elif name == "Delta" and re.match(r"\s*\\?[A-Za-z]", latex[index:]):
                rest = symbols(latex[index:].lstrip())
                first_base, first_subscript = rest[0]
                return [*found, ("\\Delta " + first_base, first_subscript), *rest[1:]]
            elif name in GREEK or name == "Delta":
                base = f"\\{name}"
            else:
                continue
        elif character.isalpha():
            base = character
            index += 1
        else:
            index += 1
            continue
        subscript, index = _read_scripts(latex, index)
        found.append((base, subscript))
    return found


def _show(symbol: Symbol) -> str:
    """Write a symbol back as LaTeX for a failure message.

    Parameters
    ----------
    symbol : `tuple` [`str`, `str`]
        The (base, subscript) pair.

    Returns
    -------
    latex : `str`
        The symbol as ``base_{subscript}``, or the base alone.
    """
    base, subscript = symbol
    return f"${base}_{{{subscript}}}$" if subscript else f"${base}$"


def _appendix_b() -> str:
    """Return the text of the architecture paper's Appendix B.

    Returns
    -------
    text : `str`
        From the Appendix B heading to the next heading of the same level.
    """
    text = ARCHITECTURE.read_text(encoding="utf-8")
    start = text.index("## Appendix B")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def paper_symbols() -> dict[Symbol, str]:
    """Read the symbols the architecture paper defines.

    Returns
    -------
    defined : `dict` [`tuple` [`str`, `str`], `str`]
        Each symbol in the first column of Appendix B, with its meaning.
    """
    defined: dict[Symbol, str] = {}
    for row in _appendix_b().splitlines():
        cells = [cell.strip() for cell in row.split("|")]
        if len(cells) < 4 or not cells[1].startswith("$"):
            continue
        for _, latex in math_spans(cells[1]):
            for symbol in symbols(latex):
                defined.setdefault(symbol, cells[2])
    return defined


def index_letters() -> frozenset[str]:
    """Read the index letters the architecture paper declares.

    Returns
    -------
    letters : `frozenset` [`str`]
        The letters named in Appendix B's sentence "The index symbols ...".
    """
    sentence = re.search(r"The index symbols (.+?) run over", _appendix_b())
    assert sentence is not None, "Appendix B no longer says which letters are indices"
    return frozenset(re.findall(r"\$([a-z])\$", sentence.group(1)))


def _split_notation(text: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Separate a guide's notation table from the rest of the guide.

    Parameters
    ----------
    text : `str`
        A review guide.

    Returns
    -------
    body : `str`
        The guide with the notation subsection blanked, keeping line numbers.
    rows : `list` [`tuple` [`int`, `str`, `str`]]
        The line, symbol cell and meaning cell of each table row.
    """
    heading = NOTATION_HEADING.search(text)
    if heading is None:
        return text, []
    end = text.find("\n#", heading.end())
    end = len(text) if end == -1 else end
    section = text[heading.start() : end]
    first_line = text.count("\n", 0, heading.start()) + 1
    rows = []
    for offset, line in enumerate(section.splitlines()):
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) >= 4 and cells[1].startswith("$"):
            rows.append((first_line + offset, cells[1], cells[2]))
    body = text[: heading.start()] + "\n" * section.count("\n") + text[end:]
    return body, rows


def guide_notation(path: Path) -> dict[Symbol, tuple[int, str]]:
    r"""Read the symbols a guide defines in its notation table.

    Parameters
    ----------
    path : `pathlib.Path`
        A review guide.

    Returns
    -------
    defined : `dict` [`tuple` [`str`, `str`], `tuple` [`int`, `str`]]
        The symbol each math span of the table's first column defines, with
        its line and meaning. Only the first symbol of a span counts, so the
        arguments in ``$B_\text{sky}(x, y)$`` are not defined there.
    """
    _, rows = _split_notation(path.read_text(encoding="utf-8"))
    defined: dict[Symbol, tuple[int, str]] = {}
    for line, cell, meaning in rows:
        for _, latex in math_spans(cell):
            found = symbols(latex)
            if found:
                defined.setdefault(found[0], (line, meaning))
    return defined


def guide_symbols(path: Path) -> dict[Symbol, list[int]]:
    """Read the symbols a guide uses outside its notation table.

    Parameters
    ----------
    path : `pathlib.Path`
        A review guide.

    Returns
    -------
    used : `dict` [`tuple` [`str`, `str`], `list` [`int`]]
        Each symbol, with the lines it appears on.
    """
    body, _ = _split_notation(path.read_text(encoding="utf-8"))
    used: dict[Symbol, list[int]] = defaultdict(list)
    for line, latex in math_spans(body):
        for symbol in symbols(latex):
            used[symbol].append(line)
    return used


def _is_paper_symbol(symbol: Symbol, paper: dict[Symbol, str], indices: frozenset[str]) -> bool:
    r"""Say whether a symbol is one the paper defines.

    Parameters
    ----------
    symbol : `tuple` [`str`, `str`]
        The (base, subscript) pair.
    paper : `dict`
        The symbols of Appendix B (`paper_symbols`).
    indices : `frozenset` [`str`]
        The paper's index letters.

    Returns
    -------
    defined : `bool`
        True when Appendix B has the symbol, or has its base bare or with an
        index, and the symbol's subscript is an index (``\lambda_j`` is the
        ``j``-th ``\lambda``).
    """
    if symbol in paper:
        return True
    base, subscript = symbol
    if subscript not in indices:
        return False
    return any(known == base and known_subscript in indices | {""} for known, known_subscript in paper)


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        (r"\sigma_{\text{ap}}^2 = S_\star", [("\\sigma", "ap"), ("S", "\\star")]),
        (
            r"\hat{F}_i(t) = \frac{F_i(t)}{L_\text{ens}(t)}",
            [("\\hat{F}", "i"), ("t", ""), ("F", "i"), ("t", ""), ("L", "ens"), ("t", "")],
        ),
        (r"P_j = \delta_j \, \delta_{j+1} \tag{6}", [("P", "j"), ("\\delta", "j"), ("\\delta", "j")]),
        (
            r"\text{RMS}_\text{sep} = \sum_{i=1}^N \Delta \theta_i^2",
            [("i", ""), ("N", ""), ("\\Delta \\theta", "i")],
        ),
        (r"E_\sigma / E_{\sigma,\text{thr}}", [("E", "\\sigma"), ("E", "\\sigma,thr")]),
        (r"\Delta_z", [("\\Delta", "z")]),
    ],
)  # fmt: skip
def test_symbols_are_read_in_one_standard_form(latex: str, expected: list[Symbol]) -> None:
    """LaTeX becomes the same (base, subscript) pairs however it is spelled."""
    assert symbols(latex) == expected


def test_guides_have_notation_tables_to_check() -> None:
    """The guides and Appendix B are where the tests expect them."""
    assert GUIDES, "no review guides found"
    assert len(paper_symbols()) > 50, "Appendix B was not read"
    assert {"i", "c", "j"} <= index_letters()


@pytest.mark.parametrize("path", GUIDES, ids=lambda path: path.stem)
def test_every_guide_symbol_is_defined(path: Path) -> None:
    """Each guide symbol is in Appendix B or in the guide's notation table."""
    paper, indices, local = paper_symbols(), index_letters(), guide_notation(path)
    problems = [
        f"line {lines[0]}: {_show(symbol)}"
        for symbol, lines in sorted(guide_symbols(path).items())
        if symbol not in local and not _is_paper_symbol(symbol, paper, indices)
    ]
    assert not problems, (
        f"{path.name} uses symbols that neither the architecture paper's Appendix B nor the "
        "guide's Notation table defines. Use the paper's symbol if it has one; otherwise add "
        "a row to GUIDE_NOTATION in build/documentation/generate_pipeline_review_docs.py and "
        f"regenerate: {problems}"
    )


@pytest.mark.parametrize("path", GUIDES, ids=lambda path: path.stem)
def test_every_notation_row_is_used(path: Path) -> None:
    """A guide's notation table lists only symbols the guide uses."""
    used = guide_symbols(path)
    unused = [
        f"line {line}: {_show(symbol)}"
        for symbol, (line, _) in guide_notation(path).items()
        if symbol not in used
    ]
    assert not unused, (
        f"{path.name} defines symbols it no longer uses. Remove them from GUIDE_NOTATION: {unused}"
    )


@pytest.mark.parametrize("path", GUIDES, ids=lambda path: path.stem)
def test_notation_does_not_redefine_paper_symbols(path: Path) -> None:
    """A guide's table never gives a new meaning to a symbol of Appendix B."""
    paper = paper_symbols()
    clashes = [
        f"line {line}: {_show(symbol)} is already '{paper[symbol]}'"
        for symbol, (line, _) in guide_notation(path).items()
        if symbol in paper
    ]
    assert not clashes, (
        f"{path.name} redefines symbols of the architecture paper. Drop the row to use the "
        f"paper's meaning, or pick another symbol: {clashes}"
    )


def test_a_symbol_defined_by_two_guides_means_the_same() -> None:
    """A symbol in two guides' tables has one meaning in both."""
    meanings: dict[Symbol, dict[str, str]] = defaultdict(dict)
    for path in GUIDES:
        for symbol, (_, meaning) in guide_notation(path).items():
            meanings[symbol][path.name] = meaning
    conflicts = [
        f"{_show(symbol)}: {by_guide}"
        for symbol, by_guide in meanings.items()
        if len(set(by_guide.values())) > 1
    ]
    assert not conflicts, (
        "These symbols mean different things in different guides. Give them the same meaning "
        f"text in GUIDE_NOTATION, or rename one: {conflicts}"
    )


@pytest.mark.parametrize("path", GUIDES, ids=lambda path: path.stem)
def test_guide_symbols_use_the_paper_index_letters(path: Path) -> None:
    """A one-letter lowercase subscript on a guide symbol is a paper index."""
    indices = index_letters()
    problems = []
    for symbol, (line, _) in guide_notation(path).items():
        for part in symbol[1].split(","):
            if re.fullmatch(r"[a-z]", part) and part not in indices:
                problems.append(f"line {line}: {_show(symbol)} uses index '{part}'")
    assert not problems, (
        f"{path.name} indexes with letters the architecture paper does not declare "
        f"({', '.join(sorted(indices))}). Use the paper's index letter: {problems}"
    )
