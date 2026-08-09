"""The architecture rule, enforced rather than trusted.

PRD §3.1 states that `app/domain/` imports nothing but the standard library and
Pydantic, and proposed enforcing it with a ruff banned-import rule. Ruff's
`flake8-tidy-imports` ban list is global rather than per-directory, so a lint rule
could not express "banned in domain/, required in api/" without a second config
file. An AST test is stronger anyway: it names the offending module and import,
and it runs in CI beside everything else.

The rule is what makes the policy and the metrics testable in microseconds with no
fixtures. Without enforcement it would survive exactly one hurried afternoon.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DOMAIN = Path(__file__).resolve().parents[2] / "app" / "domain"
DOMAIN_MODULES = sorted(DOMAIN.glob("*.py"))

ALLOWED_PREFIXES = (
    "app.domain",
    "pydantic",
    # stdlib the domain is permitted to use
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "typing",
    "math",
    "re",
    "__future__",
)

FORBIDDEN_MODULES = [
    "fastapi",
    "httpx",
    "starlette",
    "os",
    "pathlib",
    "socket",
    "subprocess",
    "app.core",
    "app.api",
    "app.llm",
    "app.storage",
    "app.triage",
    "app.evaluation",
]

BANNED_CALLS = {"open", "input", "eval", "exec", "compile", "__import__"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("module", DOMAIN_MODULES, ids=lambda p: p.name)
def test_domain_module_has_no_outward_dependencies(module: Path):
    offenders = {n for n in _imports(module) if not n.startswith(ALLOWED_PREFIXES)}
    assert not offenders, (
        f"{module.name} imports {sorted(offenders)}. The domain layer must not "
        "depend on frameworks, I/O, or outer layers (PRD §3.1)."
    )


@pytest.mark.parametrize("forbidden", FORBIDDEN_MODULES)
def test_specific_outward_dependencies_are_absent(forbidden: str):
    for module in DOMAIN_MODULES:
        offending = [n for n in _imports(module) if n == forbidden or n.startswith(f"{forbidden}.")]
        assert not offending, f"{module.name} imports {forbidden}"


@pytest.mark.parametrize("module", DOMAIN_MODULES, ids=lambda p: p.name)
def test_domain_makes_no_io_calls(module: Path):
    """AST-based, not textual.

    An earlier substring version of this check failed on the token "httpx." inside
    a DOCSTRING explaining why httpx must not appear — a false positive that would
    have trained me to weaken the rule rather than trust it. Parsing the tree
    means the check sees code and only code.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    offenders: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in BANNED_CALLS:
            offenders.add(func.id)
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in {"os", "socket", "httpx", "requests", "subprocess"}
        ):
            offenders.add(f"{func.value.id}.{func.attr}")
    assert not offenders, f"{module.name} performs I/O: {sorted(offenders)}"


def test_the_check_covers_every_domain_module():
    """Guards the guard: if a new domain module appears it is covered
    automatically, and if the glob ever breaks this fails loudly."""
    names = {m.name for m in DOMAIN_MODULES}
    assert {"enums.py", "models.py", "ports.py", "policy.py"} <= names
