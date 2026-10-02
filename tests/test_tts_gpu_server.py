"""Tests for services/tts-gpu/server.py — HTTP contract of the shared GPU
Piper service. PiperVoice is never loaded: VoiceBank.synthesize is stubbed,
and the server runs on an ephemeral localhost port."""
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "services" / "tts-gpu" / "server.py"
spec = importlib.util.spec_from_file_location("tts_gpu_server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class StubBank(server.VoiceBank):
    def __init__(self, data_dir):
        super().__init__(data_dir, use_cuda=False)
        self.requests = []

    def synthesize(self, stem, text, **kwargs):
        if stem not in self.stems():
            raise KeyError(stem)
        if text == "boom":
            raise RuntimeError("synthesis exploded")
        self.requests.append((stem, text, kwargs))
        return b"RIFFfakewav"


@pytest.fixture
def running(tmp_path):
    (tmp_path / "en_US-lessac-low.onnx").write_bytes(b"x")
    (tmp_path / "en_US-joe-medium.onnx").write_bytes(b"x")
    bank = StubBank(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(bank, "en_US-lessac-low"))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", bank
    httpd.shutdown()
    httpd.server_close()


def _post(url, payload):
    req = urllib.request.Request(url + "/synthesize", json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_synthesize_returns_wav_and_passes_params(running):
    url, bank = running
    status, body = _post(url, {"text": "hi", "voice": "en_US-joe-medium", "length_scale": 0.8})
    assert status == 200 and body == b"RIFFfakewav"
    stem, text, kwargs = bank.requests[0]
    assert (stem, text) == ("en_US-joe-medium", "hi")
    assert kwargs["length_scale"] == pytest.approx(0.8)
    assert kwargs["noise_scale"] is None


def test_synthesize_defaults_voice(running):
    url, bank = running
    assert _post(url, {"text": "hi"})[0] == 200
    assert bank.requests[0][0] == "en_US-lessac-low"


@pytest.mark.parametrize("payload,code", [
    ({"text": "  "}, 400),
    ({"text": "hi", "voice": "nope"}, 404),
    ({"text": "boom"}, 500),
])
def test_synthesize_errors(running, payload, code):
    url, _bank = running
    assert _post(url, payload)[0] == code


def test_health_lists_voices(running):
    url, _bank = running
    with urllib.request.urlopen(url + "/health", timeout=5) as resp:
        body = json.loads(resp.read())
    assert body["ok"] is True and body["cuda"] is False
    assert body["voices"] == ["en_US-joe-medium", "en_US-lessac-low"]


def test_cuda_not_requested_skips_probe():
    assert server._cuda_requested_and_usable(False) is False
