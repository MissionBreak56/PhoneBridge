"""Testes de ponta a ponta do servidor com um backend de áudio falso.

Rodar: cd pc && python -m pytest tests -q   (ou: python tests/test_engine.py)
"""
import os
import socket
import struct
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import protocol as pr  # noqa: E402
from audio_backend import Backend, chunk_bytes  # noqa: E402
from engine import BridgeServer  # noqa: E402


class FakeSink:
    def __init__(self, rate):
        self.rate, self.channels, self.name = rate, 1, "fake-out"
        self.received = bytearray()
        self.closed = False

    def write(self, data):
        self.received += data

    def close(self):
        self.closed = True


class FakeLoop:
    def __init__(self, rate, ch):
        self.rate, self.channels, self.name = rate, ch, "fake-loop"
        self.n = 0

    def read(self, timeout=0.05):
        time.sleep(0.01)
        t = (np.arange(self.rate // 100) + self.n * (self.rate // 100)) / self.rate
        self.n += 1
        s = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
        return np.repeat(s, self.channels).tobytes()

    def close(self):
        pass


class FakeBackend(Backend):
    def __init__(self, rate=48000):
        self.rate = rate
        self.sink = None

    def list_output_devices(self):
        return [("dev", "fake")]

    def open_mic_sink(self, device_id):
        self.sink = FakeSink(self.rate)
        return self.sink

    def open_loopback(self):
        return FakeLoop(self.rate, 2)


def start(backend, **kw):
    s = BridgeServer(0, "dev", lambda m: None, backend, **kw)
    # porta efêmera: abre o socket manualmente para descobrir qual foi
    s.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.sock.bind(("127.0.0.1", 0))
    s.port = s.sock.getsockname()[1]
    s.sock.listen(4)
    s.sock.settimeout(1.0)
    s.running = True
    import threading
    threading.Thread(target=s._accept_loop, daemon=True).start()
    return s


def connect(server, magic, mode, mask=None):
    c = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    c.sendall(magic + bytes([mode]) + (bytes([mask]) if mask is not None else b""))
    return c


def test_mic_v1_pcm():
    be = FakeBackend()
    s = start(be)
    c = connect(s, pr.MAGIC_V1, 1)
    status, rate, ch = struct.unpack(">BIB", pr.recv_exact(c, 6))
    assert (status, rate, ch) == (0, 48000, 1)
    payload = (np.arange(480 * 20) % 1000).astype(np.int16).tobytes()
    c.sendall(payload)
    time.sleep(0.5)
    assert len(be.sink.received) > 0
    assert s.level > 0
    c.close()
    s.stop()


def test_mic_v2_opus_roundtrip():
    if not pr.opus_available():
        return
    be = FakeBackend()
    s = start(be)
    c = connect(s, pr.MAGIC_V2, 1, pr.CODEC_BIT_PCM | pr.CODEC_BIT_OPUS)
    status, rate, ch, codec = struct.unpack(">BIBB", pr.recv_exact(c, 7))
    assert (status, rate, ch, codec) == (0, 48000, 1, pr.CODEC_OPUS)
    enc = pr.OpusEncoder(1, 32000, "voip")
    t = np.arange(960 * 30) / 48000
    sig = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    for i in range(30):
        pr.send_packet(c, enc.encode(sig[i * 960:(i + 1) * 960].tobytes()))
        time.sleep(0.005)
    time.sleep(0.5)
    assert len(be.sink.received) >= 960 * 2 * 10
    assert s.level > 0.1
    c.close()
    s.stop()


def test_mic_opus_falls_back_when_rate_is_not_48k():
    be = FakeBackend(rate=44100)
    s = start(be)
    c = connect(s, pr.MAGIC_V2, 1, pr.CODEC_BIT_PCM | pr.CODEC_BIT_OPUS)
    status, rate, ch, codec = struct.unpack(">BIBB", pr.recv_exact(c, 7))
    assert (status, rate, codec) == (0, 44100, pr.CODEC_PCM)
    c.close()
    s.stop()


def test_opus_not_requested_means_pcm():
    be = FakeBackend()
    s = start(be)
    c = connect(s, pr.MAGIC_V2, 1, pr.CODEC_BIT_PCM)
    status, rate, ch, codec = struct.unpack(">BIBB", pr.recv_exact(c, 7))
    assert codec == pr.CODEC_PCM
    c.close()
    s.stop()


def test_speaker_v1_pcm():
    s = start(FakeBackend())
    c = connect(s, pr.MAGIC_V1, 2)
    status, rate, ch = struct.unpack(">BIB", pr.recv_exact(c, 6))
    assert (status, rate, ch) == (0, 48000, 2)
    data = pr.recv_exact(c, chunk_bytes(48000, 2) * 5)
    assert len(data) == chunk_bytes(48000, 2) * 5
    c.close()
    s.stop()


def test_speaker_v2_opus():
    if not pr.opus_available():
        return
    s = start(FakeBackend())
    c = connect(s, pr.MAGIC_V2, 2, pr.CODEC_BIT_PCM | pr.CODEC_BIT_OPUS)
    status, rate, ch, codec = struct.unpack(">BIBB", pr.recv_exact(c, 7))
    assert (status, rate, ch, codec) == (0, 48000, 2, pr.CODEC_OPUS)
    dec = pr.OpusDecoder(2)
    for _ in range(5):
        pcm = dec.decode(pr.recv_packet(c))
        assert len(pcm) == 960 * 2 * 2
    c.close()
    s.stop()


def test_bad_handshake_closes():
    s = start(FakeBackend())
    c = socket.create_connection(("127.0.0.1", s.port), timeout=5)
    c.sendall(b"XXXX\x01")
    c.settimeout(3)
    try:
        assert c.recv(10) == b""
    except ConnectionResetError:
        pass  # o servidor fechou a conexão: também vale
    c.close()
    s.stop()


def test_discovery_reply():
    d = pr.Discovery("MEU-PC", 5800, True)
    if not d.start():
        return
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.settimeout(3)
    u.sendto(pr.DISC_REQUEST, ("127.0.0.1", pr.DISCOVERY_PORT))
    data, _ = u.recvfrom(256)
    assert data == b"PBHERE1|MEU-PC|5800|1"
    d.stop()
    u.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
