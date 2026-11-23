"""Servidor TCP do PhoneBridge (sem interface gráfica).

  modo 1 (microfone):    celular -> PC, toca na saída escolhida pelo usuário (VB-Cable / microfone virtual)
  modo 2 (alto-falante): PC -> celular, captura o som do sistema

Conexões:
  * protocolo v3 (padrão): autenticado, criptografado, com heartbeat e limites (PROTOCOL.md)
  * protocolos v1/v2 (legado): sem proteção; só aceitos se `allow_legacy` for ligado explicitamente
"""
import collections
import hmac
import os
import socket
import struct
import threading
import time

import numpy as np

import protocol as pr
import security as sec
from audio_backend import chunk_bytes

MAX_PREAUTH = 8  # conexões ainda sem autenticar ao mesmo tempo
PREAUTH_TIMEOUT = 5.0
SESSION_IO_TIMEOUT = 8.0  # sem receber nada (nem heartbeat) por esse tempo = par morto
MAX_OPUS_PACKET = 1500
MAX_PCM_CHUNKS = 8  # um registro de áudio PCM do celular tem no máximo 80 ms


class AuthError(Exception):
    pass


def kill(conn):
    """Fecha de verdade: o shutdown acorda quem está bloqueado lendo este socket em outra thread."""
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        conn.close()
    except OSError:
        pass


# ----------------------------------------------------------------- E/S de áudio por protocolo
class LegacyIO:
    """Protocolos v1/v2: PCM cru (blocos de 10 ms) ou pacotes Opus com tamanho de 2 bytes."""

    secure = False

    def __init__(self, conn):
        self.conn = conn
        self.opus = False
        self.chunk = 0
        self.closed = False

    def configure(self, opus, chunk):
        self.opus, self.chunk = opus, chunk

    def start(self):
        pass

    def start_drain(self):
        pass

    def read_audio(self):
        return pr.recv_packet(self.conn) if self.opus else pr.recv_exact(self.conn, self.chunk)

    def send_audio(self, data):
        if self.opus:
            pr.send_packet(self.conn, data)
        else:
            self.conn.sendall(data)

    def close(self, reason=None):
        self.closed = True
        kill(self.conn)


class SecureIO:
    """Protocolo v3: registros AES-GCM, heartbeat nos dois sentidos e expiração de sessão."""

    secure = True

    def __init__(self, conn, reader, writer, log):
        self.conn, self.reader, self.writer, self.log = conn, reader, writer, log
        self.send_lock = threading.Lock()
        self.last_rx = time.time()
        self.started = time.time()
        self.closed = False

    # -- envio / recebimento
    def send(self, rtype, payload=b""):
        with self.send_lock:
            self.conn.sendall(self.writer.seal(rtype, payload))

    def recv(self):
        rtype, payload = sec.read_record(self.conn, self.reader, pr.recv_exact)
        self.last_rx = time.time()
        return rtype, payload

    def configure(self, opus, chunk):
        pass

    def read_audio(self):
        while True:
            rtype, payload = self.recv()
            if rtype == sec.T_AUDIO:
                return payload
            if rtype == sec.T_HEARTBEAT:
                continue
            if rtype == sec.T_CLOSE:
                raise ConnectionError("encerrado pelo outro lado")
            raise sec.ProtocolError(f"tipo de registro inesperado: {rtype}")

    def send_audio(self, data):
        self.send(sec.T_AUDIO, data)

    # -- vigilância
    def start(self):
        self.last_rx = time.time()
        threading.Thread(target=self._keepalive, daemon=True).start()

    def start_drain(self):
        """Modo alto-falante: o PC só envia áudio, mas precisa ler os heartbeats do celular."""
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        try:
            while not self.closed:
                rtype, _ = self.recv()
                if rtype == sec.T_CLOSE:
                    break
                if rtype not in (sec.T_HEARTBEAT, sec.T_AUDIO):
                    raise sec.ProtocolError("registro inesperado")
        except Exception:  # noqa: BLE001
            pass
        self.close()

    def _keepalive(self):
        next_hb = time.time()
        while not self.closed:
            time.sleep(0.25)
            now = time.time()
            if now - self.last_rx > sec.HEARTBEAT_TIMEOUT:
                self.log("Sem sinal do celular: conexão encerrada")
                self.close()
                return
            if now - self.started > sec.SESSION_MAX_SECONDS:
                self.close("sessão expirada")
                return
            if now >= next_hb:
                next_hb = now + sec.HEARTBEAT_INTERVAL
                try:
                    self.send(sec.T_HEARTBEAT)
                except OSError:
                    self.close()
                    return

    def close(self, reason=None):
        if self.closed:
            return
        self.closed = True
        if reason:
            try:
                self.send(sec.T_CLOSE, reason.encode("utf-8")[:64])
            except (OSError, sec.ProtocolError):
                pass
        kill(self.conn)


# ----------------------------------------------------------------- servidor
class BridgeServer:
    def __init__(self, port, output_id, log, backend, opus_allowed=True, registry=None,
                 allow_legacy=False, limiter=None, discovery_enabled=True, record_dir=None):
        self.port = port
        self.output_id = output_id
        self.log = log
        self.backend = backend
        self.opus_allowed = opus_allowed
        self.registry = registry
        self.allow_legacy = allow_legacy
        self.limiter = limiter or sec.AuthLimiter()
        self.discovery_enabled = discovery_enabled
        self.record_dir = record_dir  # None = não grava
        self.running = False
        self.sock = None
        self.conns = set()
        self.clients = {}  # conn -> (ip, modo, codec)
        self.slots = {}  # modo -> (conn, pid)
        self.muted = False
        self.lock = threading.Lock()
        self.preauth = 0
        self.discovery = None
        self.level = 0.0  # pico recente (0..1)
        self.rms = 0.0
        self.level_ts = 0.0
        self.clip_ts = 0.0
        self.stats = collections.Counter()
        self.recorder = None
        self._blocked_logged = {}

    # ------------------------------------------------------------- ciclo de vida
    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.port))
        self.port = self.sock.getsockname()[1]  # vale também para a porta 0 (usada nos testes)
        self.sock.listen(8)
        self.sock.settimeout(1.0)
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        if self.discovery_enabled:
            try:
                name = socket.gethostname()
            except OSError:
                name = "PC"
            self.discovery = pr.Discovery(name, self.port, pr.opus_available(), self.log,
                                          pcid=self.registry.pcid if self.registry else "")
            self.discovery.start()
        self.log(f"Servidor aguardando na porta {self.port}")

    def stop(self):
        self.running = False
        if self.discovery:
            self.discovery.stop()
            self.discovery = None
        with self.lock:
            for c in list(self.conns):
                kill(c)
            self.conns.clear()
            self.clients.clear()
            self.slots.clear()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.log("Servidor parado")

    def disconnect_device(self, pid_hex):
        """Derruba as sessões de um dispositivo (ex.: depois de revogar o pareamento)."""
        with self.lock:
            victims = [c for (c, p) in self.slots.values() if p == pid_hex]
        for c in victims:
            kill(c)

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)  # pouco atraso acumulado no TCP
            with self.lock:
                self.conns.add(conn)
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()

    # ------------------------------------------------------------- medição e gravação
    def _emit(self, data):
        """Medidor de nível (pico e RMS), detector de clipping e gravação do que está passando."""
        if len(data) >= 2:
            arr = np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16).astype(np.int32)
            peak = int(np.abs(arr).max())
            now = time.time()
            self.level = max(peak / 32768.0, self.level * 0.85)
            self.rms = max(float(np.sqrt(np.mean(arr * arr))) / 32768.0, self.rms * 0.85)
            self.level_ts = now
            if peak >= 32760:
                self.clip_ts = now
                self.stats["clips"] += 1
        rec = self.recorder
        if rec:
            try:
                rec.write(data)
            except (OSError, ValueError):
                self.recorder = None

    def _start_recording(self, rate, channels, label):
        if not self.record_dir:
            return None
        try:
            from recorder import WavRecorder

            rec = WavRecorder(self.record_dir, rate, channels, label)
            self.recorder = rec
            self.log(f"Gravando em {rec.path}")
            return rec
        except OSError as e:
            self.log(f"Não foi possível gravar: {e}")
            return None

    def _stop_recording(self, rec):
        if rec:
            rec.close()
            if self.recorder is rec:
                self.recorder = None

    # ------------------------------------------------------------- conexão
    def _handle(self, conn, addr):
        ip = addr[0]
        counted = [False]

        def preauth_done():
            if counted[0]:
                counted[0] = False
                with self.lock:
                    self.preauth -= 1

        try:
            if not sec.is_local_address(ip):
                self.log(f"Conexão recusada de fora da rede local: {ip}")
                return
            wait = self.limiter.blocked(ip)
            if wait > 0:
                if time.time() - self._blocked_logged.get(ip, 0) > 30:
                    self._blocked_logged[ip] = time.time()
                    self.log(f"{ip} bloqueado por tentativas inválidas ({int(wait)} s)")
                return
            with self.lock:
                if self.preauth >= MAX_PREAUTH:
                    return
                self.preauth += 1
                counted[0] = True
            conn.settimeout(PREAUTH_TIMEOUT)
            magic = pr.recv_exact(conn, 4)
            if magic == sec.MAGIC_V3:
                self._handle_v3(conn, addr, preauth_done)
            elif magic in (pr.MAGIC_V1, pr.MAGIC_V2):
                self._handle_legacy(conn, addr, 1 if magic == pr.MAGIC_V1 else 2, preauth_done)
            else:
                raise sec.ProtocolError("início de conexão inválido")
        except AuthError as e:
            self.stats["auth_fail"] += 1
            self.log(f"Autenticação recusada de {ip}: {e}")
        except Exception as e:  # noqa: BLE001
            if self.running and conn.fileno() != -1:  # conexão já fechada por nós = encerramento normal
                self.stats["errors"] += 1
                self.log(f"Erro ({ip}): {e}")
        finally:
            preauth_done()
            kill(conn)
            with self.lock:
                self.conns.discard(conn)
                info = self.clients.pop(conn, None)
                for m, (c, _) in list(self.slots.items()):
                    if c is conn:
                        del self.slots[m]
            if info:
                self.log(f"Desconectado: {ip}")

    # ---- protocolo v3
    def _handle_v3(self, conn, addr, preauth_done):
        ip = addr[0]
        ver = pr.recv_exact(conn, 1)[0]
        pid = pr.recv_exact(conn, sec.PID_LEN)
        cnonce = pr.recv_exact(conn, sec.NONCE_LEN)
        if ver != sec.PROTO_V3:
            conn.sendall(sec.MAGIC_E3 + bytes([1, sec.PROTO_V3]))  # 1 = versão não suportada, depois a máxima
            raise sec.ProtocolError(f"versão de protocolo não suportada: {ver}")
        if self.registry is None:
            raise AuthError("servidor sem lista de dispositivos")
        snonce = os.urandom(sec.NONCE_LEN)
        conn.sendall(sec.MAGIC_S3 + bytes([sec.PROTO_V3]) + snonce)
        proof = pr.recv_exact(conn, 32)
        psk = self.registry.get_secret(pid.hex())
        key = sec.derive_auth_key(psk if psk is not None else self.registry.dummy_secret(pid))
        good = hmac.compare_digest(proof, sec.client_proof(key, pid, cnonce, snonce))
        if psk is None or not good:
            blocked = self.limiter.record_fail(ip)
            raise AuthError("dispositivo não pareado ou chave incorreta" + (" (origem bloqueada)" if blocked else ""))
        self.limiter.record_success(ip)
        conn.sendall(sec.server_proof(key, pid, cnonce, snonce))
        k_c2s, k_s2c = sec.derive_session_keys(psk, pid, cnonce, snonce)
        io = SecureIO(conn, sec.RecordReader(k_c2s), sec.RecordWriter(k_s2c), self.log)

        rtype, payload = io.recv()  # pedido, já cifrado e autenticado
        if rtype != sec.T_REQUEST or len(payload) < 3:
            raise sec.ProtocolError("pedido inválido")
        mode, mask, nlen = payload[0], payload[1], payload[2]
        if nlen > sec.MAX_NAME or len(payload) != 3 + nlen:
            raise sec.ProtocolError("pedido malformado")
        try:
            name = payload[3:3 + nlen].decode("utf-8")
        except UnicodeDecodeError:
            name = ""
        pid_hex = pid.hex()
        self.registry.mark_seen(pid_hex, ip, name)
        conn.settimeout(SESSION_IO_TIMEOUT)
        preauth_done()
        if mode not in (pr.MODE_MIC, pr.MODE_SPEAKER):
            io.send(sec.T_REJECT, bytes([sec.REJECT_UNSUPPORTED]) + "modo inválido".encode())
            return
        if not self._claim(mode, conn, pid_hex):
            self.stats["rejected"] += 1
            io.send(sec.T_REJECT, bytes([sec.REJECT_BUSY]) + "outro celular já está usando este modo".encode())
            return
        with self.lock:
            self.clients[conn] = (ip, mode, pr.CODEC_PCM)
        self.stats["sessions"] += 1
        self.log(f"Conectado (seguro): {name or ip} [{ip}]")
        session_id = os.urandom(8)

        def accept(rate, channels, codec):
            io.send(sec.T_ACCEPT, struct.pack(">IBB", rate, channels, codec) + session_id +
                    struct.pack(">H", int(sec.HEARTBEAT_INTERVAL * 1000)))
            io.start()

        def reject(code, msg):
            io.send(sec.T_REJECT, bytes([code]) + msg.encode("utf-8")[:80])

        try:
            if mode == pr.MODE_MIC:
                self._mic(io, conn, mask, sec.PROTO_V3, accept, reject)
            else:
                self._speaker(io, conn, mask, sec.PROTO_V3, accept, reject)
        finally:
            io.close()

    def _claim(self, mode, conn, pid_hex):
        """Um celular por modo. Se o mesmo celular reconecta, assume o lugar da sessão antiga."""
        old = None
        with self.lock:
            cur = self.slots.get(mode)
            if cur and cur[1] != pid_hex:
                return False
            if cur:
                old = cur[0]
            self.slots[mode] = (conn, pid_hex)
        if old is not None and old is not conn:
            kill(old)
        return True

    # ---- protocolos v1/v2 (legado)
    def _handle_legacy(self, conn, addr, version, preauth_done):
        ip = addr[0]
        if version == 1:
            mode, mask = pr.recv_exact(conn, 1)[0], pr.CODEC_BIT_PCM
        else:
            mode, mask = pr.recv_exact(conn, 2)
        if not self.allow_legacy:
            conn.sendall(pr.build_reply(version, 3))  # 3 = autenticação obrigatória
            raise AuthError("app antigo (sem proteção); atualize o app do celular ou permita o modo legado")
        conn.settimeout(None)
        preauth_done()
        io = LegacyIO(conn)
        if mode not in (pr.MODE_MIC, pr.MODE_SPEAKER):
            conn.sendall(pr.build_reply(version, 1))
            return
        if not self._claim(mode, conn, "legacy:" + ip):
            conn.sendall(pr.build_reply(version, 1))
            return
        with self.lock:
            self.clients[conn] = (ip, mode, pr.CODEC_PCM)
        self.log(f"Conectado (SEM PROTEÇÃO, protocolo v{version}): {ip}")

        def accept(rate, channels, codec):
            conn.sendall(pr.build_reply(version, 0, rate, channels, codec))
            io.configure(codec == pr.CODEC_OPUS, chunk_bytes(rate, channels))

        def reject(code, msg):
            conn.sendall(pr.build_reply(version, 1))

        if mode == pr.MODE_MIC:
            self._mic(io, conn, mask, version, accept, reject)
        else:
            self._speaker(io, conn, mask, version, accept, reject)

    def _set_codec(self, conn, codec):
        with self.lock:
            if conn in self.clients:
                ip, mode, _ = self.clients[conn]
                self.clients[conn] = (ip, mode, codec)

    # ------------------------------------------------------------- áudio
    # celular -> PC
    def _mic(self, io, conn, mask, version, accept, reject):
        if self.output_id is None:
            reject(sec.REJECT_NO_OUTPUT, "nenhum dispositivo de saída selecionado")
            raise RuntimeError("nenhum dispositivo de saída selecionado")
        try:
            sink = self.backend.open_mic_sink(self.output_id)
        except Exception as e:  # noqa: BLE001
            reject(sec.REJECT_NO_OUTPUT, "não foi possível abrir a saída de áudio")
            raise RuntimeError(f"não foi possível abrir a saída: {e}")
        rec = None
        try:
            rate = sink.rate
            codec = pr.choose_codec(version, mask, rate, self.opus_allowed)
            accept(rate, 1, codec)  # celular envia mono
            self._set_codec(conn, codec)
            self.log(f"Microfone: {rate} Hz, {'Opus' if codec == pr.CODEC_OPUS else 'PCM'} -> {sink.name}")
            chunk = chunk_bytes(rate, 1)  # 10 ms
            q = collections.deque()
            cv = threading.Condition()
            st = {"dead": False, "err": None}
            decoder = pr.OpusDecoder(1) if codec == pr.CODEC_OPUS else None
            rec = self._start_recording(rate, 1, "microfone")

            def reader():
                # lê sempre, para o TCP não acumular; guarda no máximo ~60 ms
                bad = 0
                try:
                    while self.running and not io.closed:
                        payload = io.read_audio()
                        if decoder:
                            if not payload or len(payload) > MAX_OPUS_PACKET:
                                raise sec.ProtocolError("pacote Opus inválido")
                            try:
                                pcm = decoder.decode(payload)
                                bad = 0
                            except Exception:  # noqa: BLE001
                                self.stats["decode_errors"] += 1
                                bad += 1
                                if bad > 25:
                                    raise sec.ProtocolError("muitos pacotes Opus corrompidos")
                                continue
                        else:
                            if not payload or len(payload) % chunk or len(payload) > chunk * MAX_PCM_CHUNKS:
                                raise sec.ProtocolError("bloco PCM com tamanho inválido")
                            pcm = payload
                        parts = [pcm[i:i + chunk] for i in range(0, len(pcm), chunk)]
                        self.stats["rx_bytes"] += len(pcm)
                        with cv:
                            q.extend(parts)
                            if len(q) > 6:
                                self.stats["dropped_chunks"] += len(q) - 2
                                while len(q) > 2:
                                    q.popleft()
                            cv.notify()
                except Exception as e:  # noqa: BLE001
                    st["err"] = e
                finally:
                    with cv:
                        st["dead"] = True
                        cv.notify()

            threading.Thread(target=reader, daemon=True).start()
            with cv:  # pré-buffer mínimo (20 ms) contra picos do Wi-Fi
                while len(q) < 2 and not st["dead"] and self.running:
                    cv.wait(0.2)
            while self.running and not io.closed:
                with cv:
                    while not q and not st["dead"] and self.running:
                        cv.wait(0.2)
                    if not q:
                        break
                    data = q.popleft()
                if self.muted:
                    data = bytes(len(data))  # silêncio, mantém o ritmo do stream
                self._emit(data)
                sink.write(data)
            if st["err"] and self.running and not io.closed:
                raise st["err"]
        finally:
            self._stop_recording(rec)
            sink.close()

    # PC -> celular
    def _speaker(self, io, conn, mask, version, accept, reject):
        try:
            src = self.backend.open_loopback()
        except Exception as e:  # noqa: BLE001
            reject(sec.REJECT_NO_OUTPUT, "captura do sistema indisponível")
            raise RuntimeError(str(e))
        rec = None
        try:
            rate, ch = src.rate, src.channels
            codec = pr.choose_codec(version, mask, rate, self.opus_allowed)
            accept(rate, ch, codec)
            self._set_codec(conn, codec)
            self.log(f"Alto-falante: {rate} Hz, {ch} canal(is), {'Opus' if codec == pr.CODEC_OPUS else 'PCM'} <- {src.name}")
            encoder = pr.OpusEncoder(ch) if codec == pr.CODEC_OPUS else None
            rec = self._start_recording(rate, ch, "sistema")
            io.start_drain()
            pending = b""
            while self.running and not io.closed:
                data = src.read(0.05)
                self._emit(data)
                if encoder:
                    pending += data
                    if len(pending) >= 2 * len(data):  # 20 ms = 2 blocos de 10 ms
                        io.send_audio(encoder.encode(pending[: 2 * len(data)]))
                        pending = pending[2 * len(data):]
                else:
                    io.send_audio(data)
                self.stats["tx_bytes"] += len(data)
        finally:
            self._stop_recording(rec)
            src.close()
