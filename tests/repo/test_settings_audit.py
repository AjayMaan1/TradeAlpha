"""Unification U5 exit test: no duplicate settings left. Every key the code reads or writes is registered with ONE
concept (app.core.settings_registry), no two keys claim the same concept, and legacy keys only appear in the
adoption code."""
from __future__ import annotations

import ast
import os
import re
from collections import Counter

from tests.support.paths import SRC
from tradealpha.platform.config.registry import ADOPTION_CODE, LEGACY, NESTED_LEGACY, REGISTRY

APP = SRC
ROOT = os.path.normpath(os.path.join(APP, ".."))
CALL = re.compile(r"""(?:settings\.(?:get|set)|_setting)\(\s*["']([a-z0-9_]+)["']""")


def _files():
    for d, _, fs in os.walk(APP):
        if "__pycache__" in d or os.sep + "migrations" in d:
            continue
        for f in fs:
            if f.endswith(".py"):
                p = os.path.join(d, f)
                yield os.path.relpath(p, ROOT).replace(os.sep, "/"), open(p, encoding="utf-8").read()


def test_every_setting_the_code_uses_is_registered():
    used = {}
    for rel, src in _files():
        for m in CALL.finditer(src):
            used.setdefault(m.group(1), set()).add(rel)
    unknown = {k: sorted(v) for k, v in used.items() if k not in REGISTRY and k not in LEGACY}
    assert unknown == {}, f"register these settings in tradealpha/platform/config/registry.py: {unknown}"


def test_the_settings_api_only_exposes_registered_keys():
    from tradealpha.interfaces.api.settings import SETTING_TYPES
    assert set(SETTING_TYPES) - set(REGISTRY) == set()


def test_one_key_per_concept():
    dup = [c for c, n in Counter(c.lower() for _, c in REGISTRY.values()).items() if n > 1]
    assert dup == []
    assert not set(LEGACY) & set(REGISTRY)
    assert set(LEGACY.values()) <= set(REGISTRY) and set(NESTED_LEGACY.values()) <= set(REGISTRY)


def test_legacy_keys_only_appear_in_the_adoption_code():
    offenders = []
    for rel, src in _files():
        if not any(k in src for k in LEGACY):
            continue
        if (rel, None) in ADOPTION_CODE:
            continue
        tree = ast.parse(src)
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)] + [tree]:
            body = fn.body if fn is not tree else [n for n in tree.body if not isinstance(n, ast.FunctionDef | ast.ClassDef)]
            for node in (x for b in body for x in ast.walk(b)):
                if isinstance(node, ast.Constant) and node.value in LEGACY:
                    name = fn.name if fn is not tree else None
                    if (rel, name) not in ADOPTION_CODE:
                        offenders.append((rel, name, node.value))
    # a function nested in an allowed one is reported under its own name - none exist today
    assert sorted(set(offenders)) == []


def test_nested_duplicates_are_gone_from_the_defaults():
    from tradealpha.lifecycle.signals import service
    from tradealpha.research.runs import backtests
    assert service.DEFAULTS["telegram"] == {"enabled": False}          # bot, chat and API base: notify_telegram_*
    assert "workers" in backtests.DEFAULTS                             # accepted (alias), never stored under backtest
