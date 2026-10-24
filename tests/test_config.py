import pytest

from yardsale import config


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://abc.ngrok.app", "https://abc.ngrok.app/submit"),
        ("https://abc.ngrok.app/", "https://abc.ngrok.app/submit"),
        ("https://abc.ngrok.app/submit", "https://abc.ngrok.app/submit"),
        ("http://host.example:8100/yardsale", "http://host.example:8100/yardsale/submit"),
    ],
)
def test_public_url(monkeypatch: pytest.MonkeyPatch, raw: str, expected: str) -> None:
    monkeypatch.setenv("YARDSALE_PUBLIC_URL", raw)
    assert config.public_url() == expected
    assert config.bureau_endpoint("live") == expected
    assert config.bureau_endpoint("offline").startswith("http://127.0.0.1:")


@pytest.mark.parametrize("raw", ["abc.ngrok.app", "ftp://x.example", "https://x.example/?a=1"])
def test_bad_public_urls_are_refused(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("YARDSALE_PUBLIC_URL", raw)
    with pytest.raises(config.ConfigError):
        config.public_url()


def test_no_public_url_means_local() -> None:
    assert config.public_url() is None
    assert config.bureau_endpoint("live").startswith("http://127.0.0.1:")


def test_each_mode_has_its_own_database() -> None:
    paths = {config.db_path(mode) for mode in config.MODES}
    assert len(paths) == len(config.MODES) == 2


def test_readiness_names_keys_but_never_shows_them(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_SECRET_VALUE")
    text, ok = config.report("live")
    assert "SECRET" not in text
    assert "[ok  ] APIFY_TOKEN" in text
    assert "[MISSING] ANTHROPIC_API_KEY" in text
    assert not ok
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET")
    assert config.report("live")[1]


def test_readiness_covers_payments_and_hosting(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("STRIPE_SECRET_KEY", "YARDSALE_PAYMENTS", "YARDSALE_HOST_ITEMS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("APIFY_TOKEN", "b")
    text, ok = config.report("live")
    assert ok and "[warn] payments" in text and "[warn] YARDSALE_HOST_ITEMS" in text
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_HIDDENVALUE")
    text, ok = config.report("live")
    assert not ok and "[MISSING] payments" in text and "HIDDENVALUE" not in text
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_HIDDENVALUE")
    monkeypatch.setenv("YARDSALE_HOST_ITEMS", "4")
    monkeypatch.setenv("AGENTVERSE_API_KEY", "c")
    text, ok = config.report("live")
    assert ok and "[ok  ] payments: buyers get a Stripe (test mode) link" in text
    assert "up to 4 item agents are hosted on Agentverse" in text and "HIDDENVALUE" not in text
