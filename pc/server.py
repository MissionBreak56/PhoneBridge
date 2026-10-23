#!/usr/bin/env python3
"""PhoneBridge - lado PC (Windows).

Servidor TCP que recebe conexões do app Android:
  modo 1 (microfone):     celular -> PC, toca num dispositivo de saída (ex.: VB-Cable)
  modo 2 (alto-falante):  PC -> celular, captura o som do sistema (WASAPI loopback)

Protocolo (TCP, porta padrão 5800):
  celular -> PC : "PAB1" + 1 byte de modo
  PC -> celular : 1 byte status (0 = ok), 4 bytes taxa (big endian), 1 byte canais
  depois: PCM 16 bits little-endian cru, sem enquadramento.
"""
import queue
import socket
import struct
import threading
import tkinter as tk
from tkinter import ttk

import numpy as np
import pyaudiowpatch as pyaudio

MAGIC = b"PAB1"
MODE_MIC = 1
MODE_SPEAKER = 2
DEFAULT_PORT = 5800


def recv_exact(conn, n):
    buf = bytearray()
    while len(buf) < n:
        part = conn.recv(n - len(buf))
        if not part:
            raise ConnectionError("conexão encerrada")
        buf += part
    return bytes(buf)


def list_output_devices():
    p = pyaudio.PyAudio()
    try:
        api = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        result = []
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if (
                d["hostApi"] == api["index"]
                and d["maxOutputChannels"] > 0
                and not d.get("isLoopbackDevice", False)
            ):
                result.append((i, d["name"]))
        return result
    finally:
        p.terminate()


class BridgeServer:
    def __init__(self, port, output_index, log):
        self.port = port
        self.output_index = output_index
        self.log = log
        self.running = False
        self.sock = None
        self.conns = set()
        self.lock = threading.Lock()

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.port))
        self.sock.listen(4)
        self.sock.settimeout(1.0)
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.log(f"Servidor aguardando na porta {self.port}")

    def stop(self):
        self.running = False
        with self.lock:
            for c in list(self.conns):
                try:
                    c.close()
                except OSError:
                    pass
            self.conns.clear()
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
            with self.lock:
                self.conns.add(conn)
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()

    def _handle(self, conn, addr):
        self.log(f"Conectado: {addr[0]}")
        try:
            hdr = recv_exact(conn, 5)
            if hdr[:4] != MAGIC:
                raise ValueError("handshake inválido")
            mode = hdr[4]
            if mode == MODE_MIC:
                self._mic(conn)
            elif mode == MODE_SPEAKER:
                self._speaker(conn)
            else:
                conn.sendall(struct.pack(">BIB", 1, 0, 0))
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
            self.log(f"Desconectado: {addr[0]}")

    # celular -> PC
    def _mic(self, conn):
        if self.output_index is None:
            conn.sendall(struct.pack(">BIB", 1, 0, 0))
            raise RuntimeError("nenhum dispositivo de saída selecionado")
        p = pyaudio.PyAudio()
        stream = None
        try:
            dev = p.get_device_info_by_index(self.output_index)
            rate = int(dev["defaultSampleRate"])
            out_ch = 2 if dev["maxOutputChannels"] >= 2 else 1
            conn.sendall(struct.pack(">BIB", 0, rate, 1))  # celular envia mono
            stream = p.open(
                format=pyaudio.paInt16,
                channels=out_ch,
                rate=rate,
                output=True,
                output_device_index=self.output_index,
                frames_per_buffer=rate // 100,
            )
            self.log(f"Microfone: {rate} Hz -> {dev['name']}")
            chunk = rate // 100 * 2
            while self.running:
                data = recv_exact(conn, chunk)
                if out_ch == 2:
                    data = np.repeat(np.frombuffer(data, dtype=np.int16), 2).tobytes()
                stream.write(data)
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            p.terminate()

    # PC -> celular
    def _speaker(self, conn):
        p = pyaudio.PyAudio()
        stream = None
        try:
            try:
                lb = p.get_default_wasapi_loopback()
            except Exception:
                conn.sendall(struct.pack(">BIB", 1, 0, 0))
                raise RuntimeError("loopback WASAPI indisponível")
            rate = int(lb["defaultSampleRate"])
            in_ch = int(lb["maxInputChannels"])
            out_ch = 1 if in_ch == 1 else 2
            conn.sendall(struct.pack(">BIB", 0, rate, out_ch))
            q = queue.Queue(maxsize=50)

            def cb(in_data, frame_count, time_info, status):
                try:
                    q.put_nowait(in_data)
                except queue.Full:
                    pass
                return (None, pyaudio.paContinue)

            stream = p.open(
                format=pyaudio.paInt16,
                channels=in_ch,
                rate=rate,
                input=True,
                input_device_index=lb["index"],
                frames_per_buffer=rate // 100,
                stream_callback=cb,
            )
            self.log(f"Alto-falante: {rate} Hz, {out_ch} canal(is) <- {lb['name']}")
            silence = bytes(rate // 100 * out_ch * 2)
            while self.running:
                try:
                    data = q.get(timeout=0.05)
                except queue.Empty:
                    data = silence  # loopback não gera dados em silêncio
                if in_ch > 2:
                    arr = np.frombuffer(data, dtype=np.int16).reshape(-1, in_ch)
                    data = np.ascontiguousarray(arr[:, :2]).tobytes()
                conn.sendall(data)
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            p.terminate()


class App:
    def __init__(self, root):
        self.root = root
        root.title("PhoneBridge")
        root.geometry("520x380")
        self.server = None
        self.log_q = queue.Queue()
        self.devices = list_output_devices()

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Porta:").grid(row=0, column=0, sticky="w")
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(frm, textvariable=self.port_var, width=8).grid(row=0, column=1, sticky="w")

        ttk.Label(frm, text="Saída para o microfone\n(ex.: CABLE Input):").grid(
            row=1, column=0, sticky="w", pady=6
        )
        names = [n for _, n in self.devices]
        self.dev_var = tk.StringVar()
        self.combo = ttk.Combobox(frm, textvariable=self.dev_var, values=names, state="readonly", width=48)
        self.combo.grid(row=1, column=1, sticky="we")
        for n in names:
            if "CABLE Input" in n:
                self.dev_var.set(n)
                break
        else:
            if names:
                self.dev_var.set(names[0])

        self.btn = ttk.Button(frm, text="Iniciar servidor", command=self.toggle)
        self.btn.grid(row=2, column=0, columnspan=2, pady=8)

        try:
            ips = socket.gethostbyname_ex(socket.gethostname())[2]
        except OSError:
            ips = []
        ttk.Label(frm, text="IP(s) deste PC: " + ", ".join(ips)).grid(row=3, column=0, columnspan=2, sticky="w")

        self.logbox = tk.Text(frm, height=10, state="disabled")
        self.logbox.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=6)
        frm.rowconfigure(4, weight=1)
        frm.columnconfigure(1, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll()

    def log(self, msg):
        self.log_q.put(msg)

    def poll(self):
        while not self.log_q.empty():
            self.logbox.config(state="normal")
            self.logbox.insert("end", self.log_q.get() + "\n")
            self.logbox.see("end")
            self.logbox.config(state="disabled")
        self.root.after(200, self.poll)

    def toggle(self):
        if self.server:
            self.server.stop()
            self.server = None
            self.btn.config(text="Iniciar servidor")
            return
        try:
            port = int(self.port_var.get())
        except ValueError:
            self.log("Porta inválida")
            return
        idx = None
        for i, n in self.devices:
            if n == self.dev_var.get():
                idx = i
        try:
            self.server = BridgeServer(port, idx, self.log)
            self.server.start()
            self.btn.config(text="Parar servidor")
        except OSError as e:
            self.server = None
            self.log(f"Não foi possível abrir a porta: {e}")

    def close(self):
        if self.server:
            self.server.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
