"""Segurança do protocolo v3: chaves, pareamento, autenticação, criptografia e limites.

Modelo (detalhes em ../PROTOCOL.md):
  * Pareamento fora de banda: o PC gera um segredo de 256 bits e o mostra num QR code. Só quem leu o QR
    (celular) conhece o segredo.
  * Cada conexão faz desafio-resposta com HMAC-SHA256 nos dois sentidos (o celular prova que conhece o
    segredo e o PC prova a mesma coisa ao celular) e deriva chaves de sessão novas com HKDF-SHA256.
  * Todo o tráfego depois do desafio vai em registros AES-256-GCM com contador (sem repetição, sem reordenação).
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import struct
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROTO_V3 = 3
PID_LEN = 8
PSK_LEN = 32
NONCE_LEN = 16
TAG_LEN = 16

MAGIC_V3 = b"PAB3"
MAGIC_S3 = b"PBS3"  # resposta do servidor ao pedido v3
MAGIC_E3 = b"PBE3"  # erro de versão/negociação

# tipos de registro (dentro do envelope cifrado)
T_REQUEST = 0x10
T_ACCEPT = 0x11
T_REJECT = 0x12
T_AUDIO = 0x20
T_HEARTBEAT = 0x30
T_CLOSE = 0x40

REJECT_BUSY = 1
REJECT_NO_OUTPUT = 2
REJECT_UNSUPPORTED = 3
REJECT_INTERNAL = 4

MAX_PLAINTEXT = 16384  # tipo + dados de um registro
MAX_CIPHERTEXT = MAX_PLAINTEXT + TAG_LEN
MAX_NAME = 32
HEARTBEAT_INTERVAL = 2.0
HEARTBEAT_TIMEOUT = 6.0
SESSION_MAX_SECONDS = 12 * 3600
PAIRING_TTL = 600  # segundos que um QR não usado continua válido
MAX_DEVICES = 32


class ProtocolError(Exception):
    """Mensagem inválida, corrompida, repetida ou fora de ordem."""


# ----------------------------------------------------------------- derivação de chaves
def hkdf_sha256(ikm, salt, info, length):
    prk = hmac.new(salt if salt else b"\x00" * 32, ikm, hashlib.sha256).digest()
    okm, t, i = b"", b"", 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
        i += 1
    return okm[:length]


def derive_auth_key(psk):
    return hkdf_sha256(psk, b"PBv3", b"PBv3 auth", 32)


def client_proof(auth_key, pid, cnonce, snonce):
    return hmac.new(auth_key, b"PBv3 client" + pid + cnonce + snonce, hashlib.sha256).digest()


def server_proof(auth_key, pid, cnonce, snonce):
    return hmac.new(auth_key, b"PBv3 server" + pid + cnonce + snonce, hashlib.sha256).digest()


def derive_session_keys(psk, pid, cnonce, snonce):
    """Retorna (chave celular->PC, chave PC->celular)."""
    okm = hkdf_sha256(psk, cnonce + snonce, b"PBv3 session" + pid, 64)
    return okm[:32], okm[32:]


# ----------------------------------------------------------------- registros cifrados
def _nonce(counter):
    return b"\x00\x00\x00\x00" + struct.pack(">Q", counter)


class RecordWriter:
    def __init__(self, key):
        self.aes = AESGCM(key)
        self.counter = 0
        self.lock = threading.Lock()

    def seal(self, rtype, payload=b""):
        plain = bytes([rtype]) + payload
        if len(plain) > MAX_PLAINTEXT:
            raise ProtocolError("registro grande demais")
        with self.lock:
            header = struct.pack(">H", len(plain) + TAG_LEN)
            ct = self.aes.encrypt(_nonce(self.counter), plain, header)
            self.counter += 1
        return header + ct


class RecordReader:
    def __init__(self, key):
        self.aes = AESGCM(key)
        self.counter = 0

    def open(self, header, ciphertext):
        try:
            plain = self.aes.decrypt(_nonce(self.counter), ciphertext, header)
        except InvalidTag:
            raise ProtocolError("registro inválido (adulterado, repetido ou fora de ordem)")
        self.counter += 1
        if not plain:
            raise ProtocolError("registro vazio")
        return plain[0], plain[1:]


def read_record(conn, reader, recv_exact):
    header = recv_exact(conn, 2)
    (n,) = struct.unpack(">H", header)
    if n < TAG_LEN + 1 or n > MAX_CIPHERTEXT:
        raise ProtocolError(f"tamanho de registro inválido: {n}")
    return reader.open(header, recv_exact(conn, n))


# ----------------------------------------------------------------- origem da conexão
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def is_local_address(ip):
    """Só aceitamos conexões de redes locais (privadas, link-local, loopback e CGNAT/Tailscale)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local or (a.version == 4 and a in _CGNAT)


# ----------------------------------------------------------------- limite de tentativas
class AuthLimiter:
    """Bloqueia por um tempo (crescente) endereços que erram a autenticação várias vezes."""

    def __init__(self, max_fails=5, window=60.0, base_block=60.0, max_block=900.0, clock=time.time):
        self.max_fails, self.window = max_fails, window
        self.base_block, self.max_block = base_block, max_block
        self.clock = clock
        self.lock = threading.Lock()
        self.fails = {}  # ip -> [timestamps]
        self.blocked_until = {}
        self.strikes = {}

    def blocked(self, ip):
        with self.lock:
            until = self.blocked_until.get(ip, 0)
            now = self.clock()
            if until > now:
                return until - now
            return 0.0

    def record_fail(self, ip):
        with self.lock:
            now = self.clock()
            lst = [t for t in self.fails.get(ip, []) if now - t < self.window]
            lst.append(now)
            self.fails[ip] = lst
            if len(lst) >= self.max_fails:
                n = self.strikes.get(ip, 0)
                self.strikes[ip] = n + 1
                self.blocked_until[ip] = now + min(self.base_block * (2 ** n), self.max_block)
                self.fails[ip] = []
                return True
            return False

    def record_success(self, ip):
        with self.lock:
            self.fails.pop(ip, None)
            self.strikes.pop(ip, None)
            self.blocked_until.pop(ip, None)


# ----------------------------------------------------------------- dispositivos autorizados
class DeviceRegistry:
    """Lista de celulares pareados (segredos incluídos), salva em JSON com permissão restrita."""

    def __init__(self, path, clock=time.time):
        self.path = path
        self.clock = clock
        self.lock = threading.RLock()
        self.dummy = secrets.token_bytes(32)  # para não revelar, pelo tempo, se um id existe
        self.data = {"pcid": secrets.token_hex(PID_LEN), "devices": []}
        self._load()

    # -- arquivo
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            devices = []
            for e in d.get("devices", []):
                if isinstance(e, dict) and isinstance(e.get("pid"), str) and isinstance(e.get("secret"), str):
                    if len(bytes.fromhex(e["pid"])) == PID_LEN and len(base64.b64decode(e["secret"])) == PSK_LEN:
                        devices.append(e)
            pcid = d.get("pcid")
            if isinstance(pcid, str) and len(pcid) == PID_LEN * 2:
                self.data["pcid"] = pcid
            self.data["devices"] = devices
        except (OSError, ValueError, TypeError, AttributeError):
            self._save()

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # -- operações
    @property
    def pcid(self):
        return self.data["pcid"]

    def _purge(self):
        now = self.clock()
        before = len(self.data["devices"])
        self.data["devices"] = [d for d in self.data["devices"]
                                if d.get("active") or d.get("expires", 0) > now]
        return before != len(self.data["devices"])

    def create_pairing(self):
        """Cria um pareamento novo (ainda pendente). Retorna (pid_hex, segredo_bytes)."""
        with self.lock:
            self._purge()
            if len(self.data["devices"]) >= MAX_DEVICES:
                raise ValueError("limite de dispositivos pareados atingido; remova algum")
            pid = secrets.token_hex(PID_LEN)
            psk = secrets.token_bytes(PSK_LEN)
            self.data["devices"].append({
                "pid": pid, "secret": base64.b64encode(psk).decode(), "name": "", "created": self.clock(),
                "last_seen": 0, "last_ip": "", "active": False, "expires": self.clock() + PAIRING_TTL,
            })
            self._save()
            return pid, psk

    def get_secret(self, pid_hex):
        with self.lock:
            for d in self.data["devices"]:
                if d["pid"] == pid_hex:
                    if not d.get("active") and d.get("expires", 0) <= self.clock():
                        return None
                    return base64.b64decode(d["secret"])
            return None

    def mark_seen(self, pid_hex, ip, name):
        with self.lock:
            for d in self.data["devices"]:
                if d["pid"] == pid_hex:
                    d["active"], d["expires"] = True, 0
                    d["last_seen"], d["last_ip"] = self.clock(), ip
                    if name:
                        d["name"] = name[:MAX_NAME]
            self._save()

    def revoke(self, pid_hex):
        with self.lock:
            n = len(self.data["devices"])
            self.data["devices"] = [d for d in self.data["devices"] if d["pid"] != pid_hex]
            self._save()
            return len(self.data["devices"]) != n

    def list_devices(self):
        with self.lock:
            if self._purge():
                self._save()
            return [{k: d[k] for k in ("pid", "name", "created", "last_seen", "last_ip", "active")}
                    for d in self.data["devices"]]

    def dummy_secret(self, pid):
        return hmac.new(self.dummy, pid, hashlib.sha256).digest()


# ----------------------------------------------------------------- link de pareamento (QR)
def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def pairing_link(host, port, pcid, pid, psk, name):
    from urllib.parse import quote

    return f"phonebridge://{host}:{port}?v=3&id={pcid}&pid={pid}&k={b64u(psk)}&n={quote(name)}"
