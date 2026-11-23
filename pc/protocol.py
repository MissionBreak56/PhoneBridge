"""Protocolo PhoneBridge (documentação completa em ../PROTOCOL.md).

Este módulo não depende de interface gráfica nem de áudio: só rede e Opus.
"""
import json
import os
import socket
import struct
import sys
import threading

MAGIC_V1 = b"PAB1"
MAGIC_V2 = b"PAB2"
MODE_MIC = 1
MODE_SPEAKER = 2

CODEC_PCM = 0
CODEC_OPUS = 1
CODEC_BIT_PCM = 1
CODEC_BIT_OPUS = 2

DEFAULT_PORT = 5800
DISCOVERY_PORT = 5801
DISC_REQUEST = b"PBFIND1"
DISC_REPLY = b"PBHERE1"

OPUS_RATE = 48000
OPUS_FRAME_SAMPLES = 960  # 20 ms em 48 kHz


# ---------------------------------------------------------------- handshake
def recv_exact(conn, n):
    buf = bytearray()
    while len(buf) < n:
        part = conn.recv(n - len(buf))
        if not part:
            raise ConnectionError("conexão encerrada")
        buf += part
    return bytes(buf)


def read_handshake(conn):
    """Lê o pedido do celular. Retorna (versão, modo, máscara_de_codecs)."""
    magic = recv_exact(conn, 4)
    if magic == MAGIC_V1:
        return 1, recv_exact(conn, 1)[0], CODEC_BIT_PCM
    if magic == MAGIC_V2:
        mode, mask = recv_exact(conn, 2)
        return 2, mode, mask
    raise ValueError("handshake inválido")


def build_reply(version, status, rate=0, channels=0, codec=CODEC_PCM):
    """Resposta do PC. A versão 1 não tem o byte de codec (sempre PCM)."""
    if version == 1:
        return struct.pack(">BIB", status, rate, channels)
    return struct.pack(">BIBB", status, rate, channels, codec)


def choose_codec(version, mask, rate, opus_allowed):
    """Opus só é usado se o celular pediu, o PC permite, a taxa é 48 kHz e a biblioteca existe."""
    if version >= 2 and (mask & CODEC_BIT_OPUS) and opus_allowed and rate == OPUS_RATE and opus_available():
        return CODEC_OPUS
    return CODEC_PCM


# ---------------------------------------------------------------- pacotes Opus
def send_packet(conn, payload):
    if len(payload) > 0xFFFF:
        raise ValueError("pacote grande demais")
    conn.sendall(struct.pack(">H", len(payload)) + payload)


def recv_packet(conn):
    (n,) = struct.unpack(">H", recv_exact(conn, 2))
    return recv_exact(conn, n) if n else b""


# ---------------------------------------------------------------- Opus
_opus = None


def _bootstrap_opus_path():
    """No executável do PyInstaller o libopus fica junto do programa (pasta temporária _MEIPASS).
    O opuslib só procura pela biblioteca do sistema, então apontamos o caminho dela."""
    import ctypes.util

    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.argv[0] or "."))
    os.environ["PATH"] = base + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory") and os.path.isdir(base):
        try:
            os.add_dll_directory(base)
        except OSError:
            pass
    original = ctypes.util.find_library

    def find_library(name):
        if name == "opus":
            for cand in ("opus.dll", "libopus.so.0", "libopus-0.dll"):
                path = os.path.join(base, cand)
                if os.path.isfile(path):
                    return path
        return original(name)

    ctypes.util.find_library = find_library


def _load_opus():
    global _opus
    if _opus is None:
        try:
            _bootstrap_opus_path()
            import opuslib_next as mod

            mod.Decoder(OPUS_RATE, 1)  # força o carregamento da biblioteca nativa
            _opus = mod
        except Exception:  # noqa: BLE001 - biblioteca ausente: usamos PCM
            _opus = False
    return _opus or None


def opus_available():
    return _load_opus() is not None


class OpusEncoder:
    def __init__(self, channels, bitrate=None, application="audio"):
        mod = _load_opus()
        if mod is None:
            raise RuntimeError("Opus indisponível")
        self.channels = channels
        self.enc = mod.Encoder(OPUS_RATE, channels, application)
        self.enc.bitrate = bitrate or (64000 if channels == 1 else 128000)

    def encode(self, pcm):
        """pcm: 20 ms de PCM int16 intercalado (bytes)."""
        return self.enc.encode(pcm, OPUS_FRAME_SAMPLES)


class OpusDecoder:
    def __init__(self, channels):
        mod = _load_opus()
        if mod is None:
            raise RuntimeError("Opus indisponível")
        self.channels = channels
        self.dec = mod.Decoder(OPUS_RATE, channels)

    def decode(self, packet):
        return self.dec.decode(packet, OPUS_FRAME_SAMPLES)


# ---------------------------------------------------------------- descoberta (UDP)
def local_ips():
    try:
        ips = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        ips = []
    return [i for i in ips if not i.startswith("127.")]


def primary_ip():
    """IP da interface que sai para a rede (sem enviar nenhum pacote)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        ips = local_ips()
        return ips[0] if ips else None
    finally:
        s.close()


def qr_payload(host, port):
    return f"phonebridge://{host}:{port}"


class Discovery:
    """Responde ao 'PBFIND1' do app com 'PBHERE1|nome|porta|opus|proto|pcid'.

    A resposta traz só o nome do PC, a porta, o suporte a Opus, a versão máxima do protocolo e um
    identificador público do PC (não é segredo). Respostas têm limite de frequência por origem e só vão
    para endereços de rede local.
    """

    MAX_PER_SECOND = 5

    def __init__(self, name, tcp_port, opus, log=None, pcid="", proto=3):
        self.name = name
        self.tcp_port = tcp_port
        self.opus = opus
        self.pcid = pcid
        self.proto = proto
        self.log = log or (lambda m: None)
        self.sock = None
        self.running = False
        self._hits = {}

    def reply_bytes(self):
        name = self.name.replace("|", " ")[:60]
        parts = [DISC_REPLY, name.encode("utf-8"), str(self.tcp_port).encode(),
                 b"1" if self.opus else b"0", str(self.proto).encode(), self.pcid.encode()]
        return b"|".join(parts)

    def _allowed(self, ip, now):
        hits = [t for t in self._hits.get(ip, []) if now - t < 1.0]
        if len(hits) >= self.MAX_PER_SECOND:
            self._hits[ip] = hits
            return False
        hits.append(now)
        self._hits[ip] = hits
        if len(self._hits) > 256:  # evita crescer sem limite
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < 1.0}
        return True

    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", DISCOVERY_PORT))
        except OSError as e:
            s.close()
            self.log(f"Descoberta automática indisponível (porta UDP {DISCOVERY_PORT}): {e}")
            return False
        s.settimeout(1.0)
        self.sock = s
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    def _loop(self):
        import time

        from security import is_local_address

        while self.running:
            try:
                data, addr = self.sock.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:
                break
            if data != DISC_REQUEST or not is_local_address(addr[0]):
                continue
            if not self._allowed(addr[0], time.time()):
                continue
            try:
                self.sock.sendto(self.reply_bytes(), addr)
            except OSError:
                pass

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
