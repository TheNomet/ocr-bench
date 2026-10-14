from types import SimpleNamespace

from ocrbench import cli
from ocrbench.backends import openai_compat


class _FakeBackend:
    """Accepts only temperature 1, like a gateway that pins it."""

    def __init__(self, name, spec):
        self.model = spec["model"]
        self.temperature_override = object()
        self.client = SimpleNamespace(get=self._get)

    async def _auth(self, force):
        return {}

    async def _get(self, url, headers):
        return SimpleNamespace(status_code=404, json=lambda: {})

    async def complete(self, req):
        if self.temperature_override != 1:
            raise RuntimeError(f"HTTP 400 temperature {self.temperature_override} not supported")
        return SimpleNamespace(text="OK")


def test_probe_tries_each_temperature(monkeypatch, capsys):
    monkeypatch.setattr(openai_compat, "OpenAICompatBackend", _FakeBackend)
    cfg = SimpleNamespace(backends={"m": {"kind": "openai_compatible", "base_url": "https://gw", "model": "x"}})
    a = SimpleNamespace(backend=None, filter=None, model=None, temperature=[0.0, 1.0, None])
    cli.cmd_probe_models(cfg, a)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "call x @ temperature 0.0: FAILED" in out
    assert "call x @ temperature 1.0: OK" in out
    assert "call x @ temperature omitted: FAILED" in out
