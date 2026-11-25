"""Cliente de referência do protocolo v3 (usado nos testes; serve de exemplo para outras implementações)."""
import os
import socket
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import protocol as pr  # noqa: E402
import security as sec  # noqa: E402


class V3Client:
    def __init__(self, host, port, pid_hex, psk, name="Teste", timeout=5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.pid = bytes.fromhex(pid_hex)
        self.psk = psk
        self.name = name
        self.reader = self.writer = None
        self.server_proof_ok = False

    def hello(self, tamper_proof=False, version=3, cnonce=None):
        s = self.sock
        cnonce = cnonce or os.urandom(16)
        self.cnonce = cnonce
        s.sendall(sec.MAGIC_V3 + bytes([version]) + self.pid + cnonce)
        head = pr.recv_exact(s, 4)
        if head == sec.MAGIC_E3:
            raise ConnectionError("versão não suportada: " + str(list(pr.recv_exact(s, 2))))
        assert head == sec.MAGIC_S3
        pr.recv_exact(s, 1)
        snonce = pr.recv_exact(s, 16)
        key = sec.derive_auth_key(self.psk)
        proof = sec.client_proof(key, self.pid, cnonce, snonce)
        if tamper_proof:
            proof = bytes([proof[0] ^ 1]) + proof[1:]
        self.last_proof = proof
        s.sendall(proof)
        sp = pr.recv_exact(s, 32)  # se a autenticação falhar o servidor fecha e isto levanta ConnectionError
        self.server_proof_ok = sp == sec.server_proof(key, self.pid, cnonce, snonce)
        k_c2s, k_s2c = sec.derive_session_keys(self.psk, self.pid, cnonce, snonce)
        self.writer, self.reader = sec.RecordWriter(k_c2s), sec.RecordReader(k_s2c)

    def send(self, rtype, payload=b""):
        self.sock.sendall(self.writer.seal(rtype, payload))

    def recv(self):
        return sec.read_record(self.sock, self.reader, pr.recv_exact)

    def request(self, mode, mask=pr.CODEC_BIT_PCM):
        nm = self.name.encode()
        self.send(sec.T_REQUEST, bytes([mode, mask, len(nm)]) + nm)
        rtype, p = self.recv()
        if rtype == sec.T_ACCEPT:
            rate, ch, codec = struct.unpack(">IBB", p[:6])
            return {"ok": True, "rate": rate, "channels": ch, "codec": codec, "session": p[6:14],
                    "hb_ms": struct.unpack(">H", p[14:16])[0]}
        return {"ok": False, "code": p[0], "msg": p[1:].decode()}

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def pair_request(host, port, name="Celular de Teste", timeout=10, on_code=None, tamper_peer=False):
    """Pede pareamento com confirmação no PC. Retorna dict(ok, pid, psk, pcid, pcname, code) ou (ok=False, reason)."""
    import socket as _s

    sock = _s.create_connection((host, port), timeout=timeout)
    priv, cpub = sec.pair_generate()
    nm = name.encode()
    sock.sendall(sec.MAGIC_PAIR + bytes([3]) + cpub + bytes([len(nm)]) + nm)
    head = pr.recv_exact(sock, 4)
    assert head == sec.MAGIC_PAIR_S, head
    spub = pr.recv_exact(sock, sec.PAIR_PUB_LEN)
    # a ordem do sal é sempre cpub | spub, igual nos dois lados
    k_c2s, k_s2c, code = sec.pair_derive(priv, spub, cpub, spub)
    if on_code:
        on_code(code)
    reader = sec.RecordReader(k_s2c)
    sock.settimeout(timeout)
    try:
        rtype, p = sec.read_record(sock, reader, pr.recv_exact)
    finally:
        pass
    sock.close()
    if rtype == sec.T_PAIR_GRANT:
        return {"ok": True, "pid": p[:8].hex(), "psk": p[8:40], "pcid": p[40:48].hex(), "pcname": p[48:].decode(), "code": code}
    return {"ok": False, "reason": p.decode(), "code": code}
