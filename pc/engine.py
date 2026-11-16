"""Servidor TCP do PhoneBridge (sem interface gráfica).

  modo 1 (microfone):    celular -> PC, toca num dispositivo de saída (VB-Cable / microfone virtual)
  modo 2 (alto-falante): PC -> celular, captura o som do sistema
"""
import collections
import queue
import socket
import struct
import threading
import time

import numpy as np

import protocol as pr
from audio_backend import chunk_bytes


class BridgeServer:
    def __init__(self, port, output_id, log, backend, opus_allowed=True):
        self.port = port
        self.output_id = output_id
        self.log = log
        self.backend = backend
        self.opus_allowed = opus_allowed
        self.running = False
        self.sock = None
        self.conns = set()
        self.clients = {}  # conn -> (ip, modo, codec)
        self.muted = False
        self.lock = threading.Lock()
        self.discovery = None
        self.level = 0.0  # pico recente (0..1) do áudio que está passando
        self.level_ts = 0.0

    # ------------------------------------------------------------- ciclo de vida
    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.port))
        self.sock.listen(4)
        self.sock.settimeout(1.0)
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        try:
            name = socket.gethostname()
        except OSError:
            name = "PC"
        self.discovery = pr.Discovery(name, self.port, pr.opus_available(), self.log)
        self.discovery.start()
        self.log(f"Servidor aguardando na porta {self.port}")

    def stop(self):
        self.running = False
        if self.discovery:
            self.discovery.stop()
            self.discovery = None
        with self.lock:
            for c in list(self.conns):
                try:
                    c.close()
                except OSError:
                    pass
            self.conns.clear()
            self.clients.clear()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.log("Servidor parado")

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

    # ------------------------------------------------------------- nível de áudio
    def _meter(self, data):
        if len(data) < 2:
            return
        arr = np.frombuffer(data, dtype=np.int16)
        peak = float(np.abs(arr.astype(np.int32)).max()) / 32768.0
        self.level = max(peak, self.level * 0.85)
        self.level_ts = time.time()

    # ------------------------------------------------------------- conexão
    def _handle(self, conn, addr):
        self.log(f"Conectado: {addr[0]}")
        try:
            conn.settimeout(10)
            version, mode, mask = pr.read_handshake(conn)
            conn.settimeout(None)
            with self.lock:
                self.clients[conn] = (addr[0], mode, pr.CODEC_PCM)
            if mode == pr.MODE_MIC:
                self._mic(conn, addr, version, mask)
            elif mode == pr.MODE_SPEAKER:
                self._speaker(conn, addr, version, mask)
            else:
                conn.sendall(pr.build_reply(version, 1))
        except Exception as e:  # noqa: BLE001
            if self.running:
                self.log(f"Erro: {e}")
        finally:
            try:
                conn.close()
            except OSError:
                pass
            with self.lock:
                self.conns.discard(conn)
                self.clients.pop(conn, None)
            self.log(f"Desconectado: {addr[0]}")

    def _set_codec(self, conn, codec):
        with self.lock:
            if conn in self.clients:
                ip, mode, _ = self.clients[conn]
                self.clients[conn] = (ip, mode, codec)

    # celular -> PC
    def _mic(self, conn, addr, version, mask):
        if self.output_id is None:
            conn.sendall(pr.build_reply(version, 1))
            raise RuntimeError("nenhum dispositivo de saída selecionado")
        try:
            sink = self.backend.open_mic_sink(self.output_id)
        except Exception as e:  # noqa: BLE001
            conn.sendall(pr.build_reply(version, 1))
            raise RuntimeError(f"não foi possível abrir a saída: {e}")
        try:
            rate = sink.rate
            codec = pr.choose_codec(version, mask, rate, self.opus_allowed)
            conn.sendall(pr.build_reply(version, 0, rate, 1, codec))  # celular envia mono
            self._set_codec(conn, codec)
            self.log(f"Microfone: {rate} Hz, {'Opus' if codec == pr.CODEC_OPUS else 'PCM'} -> {sink.name}")
            chunk = chunk_bytes(rate, 1)  # 10 ms
            q = collections.deque()
            cv = threading.Condition()
            st = {"dead": False, "err": None}
            decoder = pr.OpusDecoder(1) if codec == pr.CODEC_OPUS else None

            def reader():
                # lê sempre, para o TCP não acumular; guarda no máximo ~60 ms
                try:
                    while self.running:
                        if decoder:
                            pcm = decoder.decode(pr.recv_packet(conn))
                            parts = [pcm[i:i + chunk] for i in range(0, len(pcm), chunk)]
                        else:
                            parts = [pr.recv_exact(conn, chunk)]
                        with cv:
                            q.extend(parts)
                            if len(q) > 6:
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
            while self.running:
                with cv:
                    while not q and not st["dead"] and self.running:
                        cv.wait(0.2)
                    if not q:
                        break
                    data = q.popleft()
                if self.muted:
                    data = bytes(len(data))  # silêncio, mantém o ritmo do stream
                self._meter(data)
                sink.write(data)
            if st["err"] and self.running:
                raise st["err"]
        finally:
            sink.close()

    # PC -> celular
    def _speaker(self, conn, addr, version, mask):
        try:
            src = self.backend.open_loopback()
        except Exception as e:  # noqa: BLE001
            conn.sendall(pr.build_reply(version, 1))
            raise RuntimeError(str(e))
        try:
            rate, ch = src.rate, src.channels
            codec = pr.choose_codec(version, mask, rate, self.opus_allowed)
            conn.sendall(pr.build_reply(version, 0, rate, ch, codec))
            self._set_codec(conn, codec)
            self.log(f"Alto-falante: {rate} Hz, {ch} canal(is), {'Opus' if codec == pr.CODEC_OPUS else 'PCM'} <- {src.name}")
            encoder = pr.OpusEncoder(ch) if codec == pr.CODEC_OPUS else None
            pending = b""
            while self.running:
                data = src.read(0.05)
                self._meter(data)
                if encoder:
                    pending += data
                    if len(pending) >= 2 * len(data):  # 20 ms = 2 blocos de 10 ms
                        pr.send_packet(conn, encoder.encode(pending[: 2 * len(data)]))
                        pending = pending[2 * len(data):]
                else:
                    conn.sendall(data)
        finally:
            src.close()
