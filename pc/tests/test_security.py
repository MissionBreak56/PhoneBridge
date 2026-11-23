"""Testes unitários da camada de segurança (chaves, registros cifrados, pareamento, limites)."""
import base64
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import security as sec  # noqa: E402


def test_hkdf_rfc5869_case1():
    ikm = bytes([0x0B] * 22)
    salt = bytes(range(13))
    info = bytes(range(0xF0, 0xFA))
    okm = sec.hkdf_sha256(ikm, salt, info, 42)
    assert okm.hex() == ("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
                         "34007208d5b887185865")


def test_record_roundtrip_and_counter():
    key = os.urandom(32)
    w, r = sec.RecordWriter(key), sec.RecordReader(key)
    for i in range(5):
        rec = w.seal(sec.T_AUDIO, bytes([i]) * 100)
        t, p = r.open(rec[:2], rec[2:])
        assert (t, p) == (sec.T_AUDIO, bytes([i]) * 100)
    assert w.counter == r.counter == 5


def test_record_tamper_replay_and_reorder_are_rejected():
    key = os.urandom(32)
    w, r = sec.RecordWriter(key), sec.RecordReader(key)
    a, b = w.seal(sec.T_AUDIO, b"um"), w.seal(sec.T_AUDIO, b"dois")
    with pytest.raises(sec.ProtocolError):  # fora de ordem
        r.open(b[:2], b[2:])
    t, p = r.open(a[:2], a[2:])  # a ordem certa ainda funciona (o contador não avançou na falha)
    assert p == b"um"
    with pytest.raises(sec.ProtocolError):  # repetição
        r.open(a[:2], a[2:])
    bad = bytearray(b)
    bad[-1] ^= 1
    with pytest.raises(sec.ProtocolError):  # adulterado
        r.open(bytes(bad[:2]), bytes(bad[2:]))
    with pytest.raises(sec.ProtocolError):  # cabeçalho (tamanho) adulterado é autenticado como dado associado
        r.open(b"\x00\x30", b[2:])


def test_record_too_large_for_writer():
    w = sec.RecordWriter(os.urandom(32))
    with pytest.raises(sec.ProtocolError):
        w.seal(sec.T_AUDIO, bytes(sec.MAX_PLAINTEXT))


def test_session_keys_differ_by_direction_and_nonces():
    psk, pid = os.urandom(32), os.urandom(8)
    c, s = os.urandom(16), os.urandom(16)
    k1, k2 = sec.derive_session_keys(psk, pid, c, s)
    assert k1 != k2
    assert sec.derive_session_keys(psk, pid, c, os.urandom(16)) != (k1, k2)
    assert sec.client_proof(sec.derive_auth_key(psk), pid, c, s) != sec.server_proof(sec.derive_auth_key(psk), pid, c, s)


def test_is_local_address():
    for ip in ("192.168.0.5", "10.1.2.3", "172.16.9.9", "127.0.0.1", "169.254.3.4", "100.100.1.1"):
        assert sec.is_local_address(ip), ip
    for ip in ("8.8.8.8", "200.150.10.1", "100.63.0.1", "nao-e-ip"):
        assert not sec.is_local_address(ip), ip


def test_limiter_blocks_and_grows_and_resets():
    now = [1000.0]
    lim = sec.AuthLimiter(max_fails=3, window=60, base_block=10, max_block=40, clock=lambda: now[0])
    assert not lim.record_fail("1.1.1.1") and not lim.record_fail("1.1.1.1")
    assert lim.record_fail("1.1.1.1")
    assert 9 < lim.blocked("1.1.1.1") <= 10
    assert lim.blocked("2.2.2.2") == 0
    now[0] += 11
    assert lim.blocked("1.1.1.1") == 0
    for _ in range(3):
        lim.record_fail("1.1.1.1")
    assert 19 < lim.blocked("1.1.1.1") <= 20  # o bloqueio dobra
    lim.record_success("1.1.1.1")
    assert lim.blocked("1.1.1.1") == 0


def test_registry_pairing_lifecycle(tmp_path=None):
    d = tempfile.mkdtemp()
    now = [5000.0]
    path = os.path.join(d, "devices.json")
    reg = sec.DeviceRegistry(path, clock=lambda: now[0])
    pid, psk = reg.create_pairing()
    assert reg.get_secret(pid) == psk and len(psk) == 32
    assert reg.get_secret("00" * 8) is None
    reg.mark_seen(pid, "192.168.0.9", "Meu celular")
    devs = reg.list_devices()
    assert devs[0]["active"] and devs[0]["name"] == "Meu celular" and devs[0]["last_ip"] == "192.168.0.9"
    now[0] += sec.PAIRING_TTL * 10  # dispositivo ativo não expira
    assert reg.get_secret(pid) == psk
    reg2 = sec.DeviceRegistry(path, clock=lambda: now[0])  # persistência
    assert reg2.get_secret(pid) == psk and reg2.pcid == reg.pcid
    assert reg2.revoke(pid) and reg2.get_secret(pid) is None and not reg2.revoke(pid)
    if os.name != "nt":
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_registry_pending_pairing_expires_and_limit():
    now = [0.0]
    reg = sec.DeviceRegistry(os.path.join(tempfile.mkdtemp(), "d.json"), clock=lambda: now[0])
    pid, _ = reg.create_pairing()
    now[0] += sec.PAIRING_TTL + 1
    assert reg.get_secret(pid) is None
    assert reg.list_devices() == []
    for _ in range(sec.MAX_DEVICES):
        reg.create_pairing()
    with pytest.raises(ValueError):
        reg.create_pairing()


def test_registry_ignores_corrupt_file():
    path = os.path.join(tempfile.mkdtemp(), "d.json")
    with open(path, "w") as f:
        f.write("{isso nao e json")
    reg = sec.DeviceRegistry(path)
    assert reg.list_devices() == [] and len(reg.pcid) == 16


def test_pairing_link_format():
    link = sec.pairing_link("192.168.0.10", 5800, "ab" * 8, "cd" * 8, bytes(range(32)), "Meu PC")
    assert link.startswith("phonebridge://192.168.0.10:5800?v=3&id=" + "ab" * 8 + "&pid=" + "cd" * 8 + "&k=")
    k = link.split("&k=")[1].split("&")[0]
    assert sec.b64u_decode(k) == bytes(range(32)) and link.endswith("&n=Meu%20PC")


def test_print_vectors_for_android(capsys=None):
    """Gera os vetores de teste usados pelo teste em Kotlin (ver android/app/src/test)."""
    psk = bytes(range(32))
    pid = bytes(range(1, 9))
    cn, sn = bytes(range(16)), bytes(range(16, 32))
    ak = sec.derive_auth_key(psk)
    k1, k2 = sec.derive_session_keys(psk, pid, cn, sn)
    w = sec.RecordWriter(k1)
    rec = w.seal(sec.T_AUDIO, b"PhoneBridge")
    vec = {
        "auth_key": ak.hex(), "client_proof": sec.client_proof(ak, pid, cn, sn).hex(),
        "server_proof": sec.server_proof(ak, pid, cn, sn).hex(), "k_c2s": k1.hex(), "k_s2c": k2.hex(),
        "record0": rec.hex(),
    }
    assert all(len(v) > 0 for v in vec.values())
    if os.environ.get("PB_PRINT_VECTORS"):
        for k, v in vec.items():
            print(f"{k}={v}")
