"""The scripted demo is part of the test suite, so it can't quietly stop working."""

from pathlib import Path

import pytest

from yardsale import demo


async def test_every_demo_check_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = await demo.run(tmp_path)
    failed = [label for label, ok in results if not ok]
    assert not failed, capsys.readouterr().out
    assert len(results) >= 25
