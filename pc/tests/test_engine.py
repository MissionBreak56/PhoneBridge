"""Testes de ponta a ponta do servidor (protocolos v1/v2/v3) com um backend de áudio falso.

Rodar: cd pc && python -m pytest tests -q
"""
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import protocol as pr  # noqa: E402
import security as sec  # noqa: E402
from audio_backend import Backend, chunk_bytes  # noqa: E402
from engine import BridgeServer  # noqa: E402
from pbclient import V3Client  # noqa: E402

OPUS = pr.opus_available()


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
        self.closed = False

    def read(self, timeout=0.05):
        time.sleep(0.01)
        t = (np.arange(self.rate // 100) + self.n * (self.rate // 100)) / self.rate
        self.n += 1
        s = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
        return np.repeat(s, self.channels).tobytes()

    def close(self):
        self.closed = True


class FakeBackend(Backend):
    def __init__(self, rate=48000):
        self.rate = rate
        self.sinks, self.loops = [], []

    def list_output_devices(self):
        return [("dev", "fake")]

    def open_mic_sink(self, device_id):
        s = FakeSink(self.rate)
        self.sinks.append(s)
        return s

    def open_loopback(self):
        lp = FakeLoop(self.rate, 2)
        self.loops.append(lp)
        return lp


@pytest.fixture
def env():
    reg = sec.DeviceRegistry(os.path.join(tempfile.mkdtemp(), "devices.json"))
    started = []

    def make(backend=None, **kw):
        be = backend or FakeBackend()
        kw.setdefault("discovery_enabled", False)
        s = BridgeServer(0, "dev", lambda m: None, be, registry=reg, **kw)
        s.start()
        started.append(s)
        return s, be

    yield reg, make
    for s in started:
        s.stop()


def pair(reg, name="Celular"):
    pid, psk = reg.create_pairing()
    return pid, psk


def client(server, pid, psk, **kw):
    c = V3Client("127.0.0.1", server.port, pid, psk, **kw)
    c.hello()
    return c


def wait_for(cond, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def closed_by_peer(sock, timeout=3.0):
    sock.settimeout(timeout)
    try:
        while True:
            if sock.recv(4096) == b"":
                return True
    except (ConnectionResetError, ConnectionAbortedError):
        return True
    except socket.timeout:
        return False


# ------------------------------------------------------------------ v3: caminho feliz
def test_v3_mic_pcm(env):
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    assert c.server_proof_ok  # o celular confirma que o PC conhece a chave
    r = c.request(pr.MODE_MIC)
    assert r["ok"] and (r["rate"], r["channels"], r["codec"]) == (48000, 1, pr.CODEC_PCM)
    for i in range(30):
        c.send(sec.T_AUDIO, (np.arange(480) * 20 % 3000).astype(np.int16).tobytes())
        time.sleep(0.004)  # o celular manda em tempo real; uma rajada instantânea seria descartada
    assert wait_for(lambda: be.sinks and len(be.sinks[0].received) >= 960 * 10)
    assert s.level > 0 and s.rms > 0
    assert reg.list_devices()[0]["active"] and reg.list_devices()[0]["name"] == "Teste"
    c.close()
    assert wait_for(lambda: be.sinks[0].closed)
    assert wait_for(lambda: not s.clients and not s.slots)


@pytest.mark.skipif(not OPUS, reason="libopus indisponível")
def test_v3_mic_opus(env):
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    r = c.request(pr.MODE_MIC, pr.CODEC_BIT_PCM | pr.CODEC_BIT_OPUS)
    assert r["ok"] and r["codec"] == pr.CODEC_OPUS
    enc = pr.OpusEncoder(1, 32000, "voip")
    sig = (np.sin(2 * np.pi * 440 * np.arange(960 * 20) / 48000) * 12000).astype(np.int16)
    for i in range(20):
        c.send(sec.T_AUDIO, enc.encode(sig[i * 960:(i + 1) * 960].tobytes()))
        time.sleep(0.008)
    assert wait_for(lambda: len(be.sinks[0].received) >= 960 * 2 * 8)
    c.close()


@pytest.mark.skipif(not OPUS, reason="libopus indisponível")
def test_v3_speaker_opus_and_heartbeat_from_server(env):
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    r = c.request(pr.MODE_SPEAKER, pr.CODEC_BIT_PCM | pr.CODEC_BIT_OPUS)
    assert r["ok"] and r["codec"] == pr.CODEC_OPUS and r["channels"] == 2 and r["hb_ms"] == 2000
    dec = pr.OpusDecoder(2)
    got = 0
    while got < 5:
        t, p = c.recv()
        if t == sec.T_AUDIO:
            assert len(dec.decode(p)) == 960 * 2 * 2
            got += 1
    c.close()
    assert wait_for(lambda: be.loops[0].closed)


def test_v3_speaker_pcm(env):
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    r = c.request(pr.MODE_SPEAKER)
    assert r["ok"] and r["codec"] == pr.CODEC_PCM
    n = 0
    while n < 5:
        t, p = c.recv()
        if t == sec.T_AUDIO:
            assert len(p) == chunk_bytes(48000, 2)
            n += 1
    c.close()


# ------------------------------------------------------------------ v3: autenticação
def test_v3_unknown_device_is_rejected(env):
    reg, make = env
    s, _ = make()
    c = V3Client("127.0.0.1", s.port, "11" * 8, os.urandom(32))
    with pytest.raises(ConnectionError):
        c.hello()
    c.close()


def test_v3_wrong_proof_is_rejected(env):
    reg, make = env
    s, _ = make()
    pid, psk = pair(reg)
    c = V3Client("127.0.0.1", s.port, pid, psk)
    with pytest.raises(ConnectionError):
        c.hello(tamper_proof=True)
    c.close()


def test_v3_wrong_secret_is_rejected(env):
    reg, make = env
    s, _ = make()
    pid, _psk = pair(reg)
    c = V3Client("127.0.0.1", s.port, pid, os.urandom(32))
    with pytest.raises(ConnectionError):
        c.hello()
    c.close()


def test_v3_handshake_replay_fails(env):
    reg, make = env
    s, _ = make()
    pid, psk = pair(reg)
    c1 = client(s, pid, psk)
    cnonce, old_proof = c1.cnonce, c1.last_proof
    c1.close()
    # reenvia exatamente o mesmo pedido e a mesma prova: o servidor usa um desafio novo, então falha
    sock = socket.create_connection(("127.0.0.1", s.port), timeout=3)
    sock.sendall(sec.MAGIC_V3 + bytes([3]) + bytes.fromhex(pid) + cnonce)
    assert pr.recv_exact(sock, 4) == sec.MAGIC_S3
    pr.recv_exact(sock, 17)
    sock.sendall(old_proof)
    assert closed_by_peer(sock)
    sock.close()


def test_v3_record_replay_closes_session(env):
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    assert c.request(pr.MODE_MIC)["ok"]
    rec = c.writer.seal(sec.T_AUDIO, bytes(960))
    c.sock.sendall(rec)
    c.sock.sendall(rec)  # o mesmo registro duas vezes
    assert closed_by_peer(c.sock)
    assert wait_for(lambda: be.sinks[0].closed)


def test_v3_tampered_record_closes_session(env):
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    assert c.request(pr.MODE_MIC)["ok"]
    rec = bytearray(c.writer.seal(sec.T_AUDIO, bytes(960)))
    rec[10] ^= 0xFF
    c.sock.sendall(bytes(rec))
    assert closed_by_peer(c.sock)


def test_v3_oversize_record_closes(env):
    reg, make = env
    s, _ = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    assert c.request(pr.MODE_MIC)["ok"]
    c.sock.sendall(struct.pack(">H", 60000) + b"x" * 100)
    assert closed_by_peer(c.sock)


def test_v3_malformed_pcm_length_closes(env):
    reg, make = env
    s, _ = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    assert c.request(pr.MODE_MIC)["ok"]
    c.send(sec.T_AUDIO, bytes(1001))  # não é múltiplo de 10 ms
    assert closed_by_peer(c.sock)


def test_v3_unsupported_version_is_identified(env):
    reg, make = env
    s, _ = make()
    pid, psk = pair(reg)
    c = V3Client("127.0.0.1", s.port, pid, psk)
    with pytest.raises(ConnectionError, match="versão não suportada"):
        c.hello(version=9)
    c.close()


def test_bad_first_bytes_close(env):
    reg, make = env
    s, _ = make()
    sock = socket.create_connection(("127.0.0.1", s.port), timeout=3)
    sock.sendall(b"GET / HTTP/1.1\r\n\r\n")
    assert closed_by_peer(sock)
    sock.close()


def test_brute_force_gets_blocked(env):
    reg, make = env
    s, _ = make(limiter=sec.AuthLimiter(max_fails=3, window=60, base_block=30))
    pid, psk = pair(reg)
    for _ in range(3):
        c = V3Client("127.0.0.1", s.port, pid, os.urandom(32))
        with pytest.raises(ConnectionError):
            c.hello()
        c.close()
    # agora até o dispositivo legítimo fica bloqueado por um tempo
    c = V3Client("127.0.0.1", s.port, pid, psk)
    with pytest.raises((ConnectionError, ConnectionResetError, OSError)):
        c.hello()
    c.close()
    assert s.stats["auth_fail"] >= 3


def test_pending_pairing_works_once_and_revoke_disconnects(env):
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    assert c.request(pr.MODE_MIC)["ok"]
    assert reg.revoke(pid)
    s.disconnect_device(pid)
    assert closed_by_peer(c.sock)
    c2 = V3Client("127.0.0.1", s.port, pid, psk)  # revogado: não entra mais
    with pytest.raises(ConnectionError):
        c2.hello()
    c2.close()


# ------------------------------------------------------------------ v3: sessões e conexões simultâneas
def test_second_device_gets_busy_but_other_mode_is_ok(env):
    reg, make = env
    s, be = make()
    p1, k1 = pair(reg)
    p2, k2 = pair(reg)
    a = client(s, p1, k1)
    assert a.request(pr.MODE_MIC)["ok"]
    b = client(s, p2, k2)
    r = b.request(pr.MODE_MIC)
    assert not r["ok"] and r["code"] == sec.REJECT_BUSY
    b.close()
    d = client(s, p2, k2)
    assert d.request(pr.MODE_SPEAKER)["ok"]  # microfone e alto-falante podem funcionar juntos
    a.close()
    d.close()


def test_same_device_reconnect_replaces_old_session(env):
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    a = client(s, pid, psk)
    assert a.request(pr.MODE_MIC)["ok"]
    b = client(s, pid, psk)  # o celular reconectou antes de o PC perceber que a conexão antiga morreu
    assert b.request(pr.MODE_MIC)["ok"]
    assert closed_by_peer(a.sock)
    b.close()


def test_heartbeat_timeout_closes_dead_peer(env, monkeypatch):
    monkeypatch.setattr(sec, "HEARTBEAT_TIMEOUT", 1.0)
    monkeypatch.setattr(sec, "HEARTBEAT_INTERVAL", 0.3)
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    assert c.request(pr.MODE_MIC)["ok"]
    t0 = time.time()
    closed = False
    c.sock.settimeout(4)
    try:  # não enviamos nada: o servidor deve desistir
        while True:
            c.recv()
    except (ProtocolErrorOrClosed := (sec.ProtocolError, ConnectionError, OSError)):
        closed = True
    assert closed and time.time() - t0 < 4
    assert wait_for(lambda: be.sinks[0].closed)


def test_heartbeats_keep_session_alive(env, monkeypatch):
    monkeypatch.setattr(sec, "HEARTBEAT_TIMEOUT", 1.0)
    monkeypatch.setattr(sec, "HEARTBEAT_INTERVAL", 0.3)
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    assert c.request(pr.MODE_SPEAKER)["ok"]
    end = time.time() + 2.5
    hb = 0
    while time.time() < end:
        c.send(sec.T_HEARTBEAT)
        t, _ = c.recv()
        hb += t == sec.T_HEARTBEAT
    assert hb >= 1 and not be.loops[0].closed
    c.close()


def test_many_clean_disconnects_leave_no_threads_or_sockets(env):
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    before = threading.active_count()
    for _ in range(15):
        c = client(s, pid, psk)
        assert c.request(pr.MODE_MIC)["ok"]
        c.close()
        assert wait_for(lambda: not s.clients and not s.slots)
    assert wait_for(lambda: threading.active_count() <= before + 2)
    assert all(sk.closed for sk in be.sinks) and len(s.conns) == 0


def test_stop_closes_everything(env):
    reg, make = env
    s, be = make()
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    assert c.request(pr.MODE_SPEAKER)["ok"]
    s.stop()
    assert closed_by_peer(c.sock)
    assert wait_for(lambda: be.loops[0].closed)


def test_recording_writes_valid_wav(env):
    reg, make = env
    rec_dir = tempfile.mkdtemp()
    s, be = make(record_dir=rec_dir)
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    assert c.request(pr.MODE_MIC)["ok"]
    for _ in range(40):
        c.send(sec.T_AUDIO, (np.arange(480) % 500).astype(np.int16).tobytes())
        time.sleep(0.004)
    assert wait_for(lambda: len(be.sinks[0].received) >= 960 * 20)
    c.close()
    assert wait_for(lambda: not s.clients)
    files = [f for f in os.listdir(rec_dir) if f.endswith(".wav")]
    assert len(files) == 1
    with wave.open(os.path.join(rec_dir, files[0])) as w:
        assert w.getframerate() == 48000 and w.getnchannels() == 1 and w.getnframes() >= 480 * 10


# ------------------------------------------------------------------ legado (v1/v2)
def legacy(server, magic, mode, mask=None):
    c = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    c.sendall(magic + bytes([mode]) + (bytes([mask]) if mask is not None else b""))
    return c


def test_legacy_is_refused_by_default(env):
    reg, make = env
    s, _ = make()
    c = legacy(s, pr.MAGIC_V1, 1)
    status, rate, ch = struct.unpack(">BIB", pr.recv_exact(c, 6))
    assert status == 3
    assert closed_by_peer(c)
    c = legacy(s, pr.MAGIC_V2, 1, 3)
    assert pr.recv_exact(c, 7)[0] == 3
    c.close()


def test_legacy_v1_pcm_when_allowed(env):
    reg, make = env
    s, be = make(allow_legacy=True)
    c = legacy(s, pr.MAGIC_V1, 1)
    status, rate, ch = struct.unpack(">BIB", pr.recv_exact(c, 6))
    assert (status, rate, ch) == (0, 48000, 1)
    c.sendall((np.arange(480 * 20) % 1000).astype(np.int16).tobytes())
    assert wait_for(lambda: be.sinks and len(be.sinks[0].received) > 0)
    c.close()


@pytest.mark.skipif(not OPUS, reason="libopus indisponível")
def test_legacy_v2_opus_roundtrip_when_allowed(env):
    reg, make = env
    s, be = make(allow_legacy=True)
    c = legacy(s, pr.MAGIC_V2, 1, pr.CODEC_BIT_PCM | pr.CODEC_BIT_OPUS)
    status, rate, ch, codec = struct.unpack(">BIBB", pr.recv_exact(c, 7))
    assert (status, rate, ch, codec) == (0, 48000, 1, pr.CODEC_OPUS)
    enc = pr.OpusEncoder(1, 32000, "voip")
    sig = (np.sin(2 * np.pi * 440 * np.arange(960 * 30) / 48000) * 12000).astype(np.int16)
    for i in range(30):
        pr.send_packet(c, enc.encode(sig[i * 960:(i + 1) * 960].tobytes()))
        time.sleep(0.005)
    assert wait_for(lambda: len(be.sinks[0].received) >= 960 * 2 * 10)
    c.close()


def test_legacy_speaker_pcm_when_allowed(env):
    reg, make = env
    s, _ = make(allow_legacy=True)
    c = legacy(s, pr.MAGIC_V1, 2)
    status, rate, ch = struct.unpack(">BIB", pr.recv_exact(c, 6))
    assert (status, rate, ch) == (0, 48000, 2)
    assert len(pr.recv_exact(c, chunk_bytes(48000, 2) * 5)) == chunk_bytes(48000, 2) * 5
    c.close()


def test_opus_falls_back_to_pcm_when_rate_is_not_48k(env):
    reg, make = env
    s, be = make(backend=FakeBackend(rate=44100))
    pid, psk = pair(reg)
    c = client(s, pid, psk)
    r = c.request(pr.MODE_MIC, pr.CODEC_BIT_PCM | pr.CODEC_BIT_OPUS)
    assert r["ok"] and (r["rate"], r["codec"]) == (44100, pr.CODEC_PCM)
    c.close()


# ------------------------------------------------------------------ descoberta
def test_discovery_reply_and_rate_limit():
    d = pr.Discovery("MEU-PC", 5800, True, pcid="ab" * 8)
    if not d.start():
        pytest.skip("porta UDP 5801 ocupada")
    try:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.settimeout(0.5)
        for _ in range(12):
            u.sendto(pr.DISC_REQUEST, ("127.0.0.1", pr.DISCOVERY_PORT))
        u.sendto(b"lixo", ("127.0.0.1", pr.DISCOVERY_PORT))
        replies = []
        try:
            while True:
                replies.append(u.recvfrom(256)[0])
        except socket.timeout:
            pass
        assert replies and replies[0] == b"PBHERE1|MEU-PC|5800|1|3|" + b"ab" * 8
        assert len(replies) <= pr.Discovery.MAX_PER_SECOND
        u.close()
    finally:
        d.stop()
