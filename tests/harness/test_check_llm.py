"""scripts/check_llm.py must report an unreachable endpoint cleanly instead of crashing."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_llm.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_llm", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module via sys.modules
    spec.loader.exec_module(module)
    return module


async def test_connection_refused_is_reported():
    check_llm = _load()
    report = await check_llm.probe("http://127.0.0.1:9/v1", "m", "EMPTY", timeout_s=2)
    assert not report.reachable
    assert report.probes[0].status == "fail" and "unreachable" in report.probes[0].detail
    assert {p.status for p in report.probes[1:]} == {"untested"}
    assert "unreachable" in check_llm.render(report)
    assert report.recommended_structured_output() == "none"
