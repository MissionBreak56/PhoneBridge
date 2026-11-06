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
import collections
import ctypes
import json
import os
import queue
import socket
import struct
import subprocess
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import numpy as np
import pyaudiowpatch as pyaudio
import pystray
from PIL import Image, ImageDraw, ImageTk

MAGIC = b"PAB1"
MODE_MIC = 1
MODE_SPEAKER = 2
DEFAULT_PORT = 5800
VBCABLE_URL = "https://vb-audio.com/Cable/"
SINGLE_PORT = 47653  # trava local para permitir só uma janela aberta


def acquire_single_instance():
    """Retorna um socket-trava se esta é a única instância; senão avisa a
    janela já aberta (para ir à frente) e retorna None."""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", SINGLE_PORT))
        lock.listen(1)
        return lock
    except OSError:
        lock.close()
        try:
            with socket.create_connection(("127.0.0.1", SINGLE_PORT), timeout=2) as c:
                c.sendall(b"show")
        except OSError:
            pass
        return None


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
        self.clients = {}  # conn -> (ip, modo)
        self.muted = False
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

    def _handle(self, conn, addr):
        self.log(f"Conectado: {addr[0]}")
        try:
            hdr = recv_exact(conn, 5)
            if hdr[:4] != MAGIC:
                raise ValueError("handshake inválido")
            mode = hdr[4]
            with self.lock:
                self.clients[conn] = (addr[0], mode)
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
                self.clients.pop(conn, None)
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
            q = collections.deque()
            cv = threading.Condition()
            st = {"dead": False, "err": None}

            def reader():
                # lê sempre, para o TCP não acumular; guarda no máximo ~60 ms
                try:
                    while self.running:
                        d = recv_exact(conn, chunk)
                        with cv:
                            q.append(d)
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
                if out_ch == 2:
                    data = np.repeat(np.frombuffer(data, dtype=np.int16), 2).tobytes()
                stream.write(data)
            if st["err"] and self.running:
                raise st["err"]
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
            q = queue.Queue(maxsize=8)  # ~80 ms; descarta o mais antigo

            def cb(in_data, frame_count, time_info, status):
                try:
                    q.put_nowait(in_data)
                except queue.Full:
                    try:
                        q.get_nowait()
                        q.put_nowait(in_data)
                    except (queue.Empty, queue.Full):
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


# ---------------------------------------------------------------- interface
BG = "#0f1115"
SURFACE = "#181b21"
BORDER = "#262a33"
TEXT = "#e6e8ec"
MUTED_TXT = "#8b919c"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6b9fff"
DANGER = "#ef5350"
OK = "#3ddc84"
FONT = "Segoe UI"

CONFIG_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PhoneBridge", "config.json")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except OSError:
        pass


# ---- Firewall do Windows (regra de entrada para a porta do servidor)
NO_WINDOW = 0x08000000


def fw_rule_name(port):
    return f"PhoneBridge {port}"


def fw_rule_exists(port):
    if os.name != "nt":
        return True
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={fw_rule_name(port)}"],
            capture_output=True, creationflags=NO_WINDOW, timeout=10,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True  # não deu para checar: melhor não incomodar


def fw_add_rule_elevated(port):
    """Pede permissão de administrador (UAC) e cria a regra. True se o pedido foi aceito."""
    params = (f'advfirewall firewall add rule name="{fw_rule_name(port)}" dir=in action=allow '
              f'protocol=TCP localport={port} remoteip=localsubnet profile=any')
    try:
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", "netsh", params, None, 0) > 32
    except Exception:  # noqa: BLE001
        return False


def make_icon(size=64, muted=False):
    """Ícone minimalista: quadrado escuro arredondado com um microfone."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 4, fill=(24, 27, 33, 255))
    col = (239, 83, 80, 255) if muted else (79, 140, 255, 255)
    w = s // 14
    d.rounded_rectangle((s * 0.38, s * 0.16, s * 0.62, s * 0.56), radius=s * 0.12, fill=col)
    d.arc((s * 0.27, s * 0.30, s * 0.73, s * 0.72), 0, 180, fill=col, width=w)
    d.line((s * 0.5, s * 0.72, s * 0.5, s * 0.84), fill=col, width=w)
    d.line((s * 0.38, s * 0.84, s * 0.62, s * 0.84), fill=col, width=w)
    if muted:
        d.line((s * 0.2, s * 0.2, s * 0.8, s * 0.8), fill=(255, 255, 255, 255), width=w)
    return img.resize((size, size), Image.LANCZOS)


class FlatButton(tk.Label):
    def __init__(self, master, text, command, bg=ACCENT, hover=ACCENT_HOVER, fg="#ffffff"):
        super().__init__(master, text=text, bg=bg, fg=fg, font=(FONT, 11, "bold"),
                         cursor="hand2", pady=10)
        self.command, self.base, self.hover = command, bg, hover
        self.bind("<Button-1>", lambda e: self.command())
        self.bind("<Enter>", lambda e: super(FlatButton, self).config(bg=self.hover))
        self.bind("<Leave>", lambda e: super(FlatButton, self).config(bg=self.base))

    def set(self, text, bg, hover, fg="#ffffff"):
        self.base, self.hover = bg, hover
        super().config(text=text, bg=bg, fg=fg)


class App:
    def __init__(self, root, lock=None):
        self.root = root
        self.lock_sock = lock
        self.cfg = load_config()
        self.server = None
        self.muted = False
        self.log_q = queue.Queue()
        self.ui_q = queue.Queue()
        self.devices = []
        self.has_cable = False
        self.tray = None
        self.hint_shown = False

        root.title("PhoneBridge")
        self._win_job = None
        self._fw_busy = False
        self._restore_window()
        root.minsize(400, 640)
        root.configure(bg=BG)
        self.win_icon = ImageTk.PhotoImage(make_icon(64))
        root.iconphoto(True, self.win_icon)
        self._style()

        pad = 20
        wrap = tk.Frame(root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=pad, pady=(pad, 12))

        tk.Label(wrap, text="PhoneBridge", bg=BG, fg=TEXT, font=(FONT, 22, "bold")).pack(anchor="w")
        row = tk.Frame(wrap, bg=BG)
        row.pack(anchor="w", pady=(2, 16))
        self.dot = tk.Canvas(row, width=10, height=10, bg=BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 8))
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=MUTED_TXT, outline="")
        self.status = tk.Label(row, text="Servidor parado", bg=BG, fg=MUTED_TXT, font=(FONT, 10))
        self.status.pack(side="left")

        self._label(wrap, "PORTA")
        self.port_var = tk.StringVar(value=str(self.cfg.get("port", DEFAULT_PORT)))
        ttk.Entry(wrap, textvariable=self.port_var, style="D.TEntry").pack(fill="x", pady=(4, 12), ipady=4)

        self._label(wrap, "SAÍDA DO MICROFONE (EX.: CABLE INPUT)")
        self.dev_var = tk.StringVar()
        self.combo = ttk.Combobox(wrap, textvariable=self.dev_var, state="readonly", style="D.TCombobox")
        self.combo.pack(fill="x", pady=(4, 6), ipady=4)
        self.cable_lbl = tk.Label(wrap, text="", bg=BG, fg=MUTED_TXT, font=(FONT, 9),
                                  justify="left", wraplength=390)
        self.cable_lbl.pack(anchor="w")
        tools = tk.Frame(wrap, bg=BG)
        tools.pack(anchor="w", pady=(6, 12))
        for txt, cmd in (("Baixar VB-Cable", lambda: webbrowser.open(VBCABLE_URL)),
                         ("Atualizar", self.refresh_devices),
                         ("Config. de som", self.open_sound)):
            b = FlatButton(tools, txt, cmd, bg=SURFACE, hover=BORDER, fg=TEXT)
            b.config(font=(FONT, 9), pady=5, padx=10)
            b.pack(side="left", padx=(0, 6))
        self.refresh_devices()

        try:
            ips = [i for i in socket.gethostbyname_ex(socket.gethostname())[2] if not i.startswith("127.")]
        except OSError:
            ips = []
        self._label(wrap, "IP DESTE PC")
        tk.Label(wrap, text="   ".join(ips) or "indisponível", bg=BG, fg=TEXT,
                 font=(FONT, 11), justify="left", wraplength=360).pack(anchor="w", pady=(4, 16))

        self.btn = FlatButton(wrap, "Iniciar servidor", self.toggle)
        self.btn.pack(fill="x")
        self.mute_btn = FlatButton(wrap, "Silenciar microfone", self.toggle_mute,
                                   bg=SURFACE, hover=BORDER, fg=TEXT)
        self.mute_btn.pack(fill="x", pady=(10, 16))

        self._label(wrap, "REGISTRO")
        box = tk.Frame(wrap, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="both", expand=True, pady=(4, 0))
        self.logbox = tk.Text(box, height=6, bg=SURFACE, fg=MUTED_TXT, relief="flat", bd=0,
                              font=("Consolas", 9), state="disabled", wrap="word",
                              padx=8, pady=6, insertbackground=TEXT)
        self.logbox.pack(fill="both", expand=True)

        root.protocol("WM_DELETE_WINDOW", self.hide)
        root.bind("<Configure>", self._on_configure)
        self._start_tray()
        self.poll()
        root.after(1200, lambda: self.check_firewall(self._cur_port()))
        if lock is not None:
            threading.Thread(target=self._listen_show, args=(lock,), daemon=True).start()

    def _listen_show(self, lock):
        while True:
            try:
                conn, _ = lock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.recv(8)
                except OSError:
                    pass
            self.ui_q.put("show")  # traz a janela existente à frente

    def refresh_devices(self):
        self.devices = list_output_devices()
        names = [n for _, n in self.devices]
        self.combo["values"] = names
        cable = next((n for n in names if "CABLE Input" in n), None)
        if self.dev_var.get() not in names:
            saved = self.cfg.get("device")
            self.dev_var.set(saved if saved in names else (cable or (names[0] if names else "")))
        self.has_cable = cable is not None
        if cable:
            self.cable_lbl.config(text='VB-Cable detectado. Nos apps (Discord, Zoom...) escolha "CABLE Output" como microfone.')
        else:
            self.cable_lbl.config(text="VB-Cable não encontrado. Necessário só para usar o celular como microfone "
                                       "(instale, reinicie o Windows e clique em Atualizar).")

    def open_sound(self):
        try:
            os.startfile("ms-settings:sound")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            self.log("Não foi possível abrir as configurações de som")

    # ---- helpers de estilo
    def _style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("D.TEntry", fieldbackground=SURFACE, foreground=TEXT, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, insertcolor=TEXT, padding=4)
        st.configure("D.TCombobox", fieldbackground=SURFACE, background=SURFACE, foreground=TEXT,
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, arrowcolor=MUTED_TXT,
                     selectbackground=SURFACE, selectforeground=TEXT, padding=4)
        st.map("D.TCombobox", fieldbackground=[("readonly", SURFACE)], foreground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", SURFACE)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    def _label(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=MUTED_TXT, font=(FONT, 8, "bold")).pack(anchor="w")

    # ---- bandeja
    def _start_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Abrir PhoneBridge", lambda: self.ui_q.put("show"), default=True),
            pystray.MenuItem(lambda item: "Reativar microfone" if self.muted else "Silenciar microfone",
                             lambda: self.ui_q.put("mute")),
            pystray.MenuItem(lambda item: "Parar servidor" if self.server else "Iniciar servidor",
                             lambda: self.ui_q.put("toggle")),
            pystray.MenuItem("Liberar no Firewall", lambda: self.ui_q.put("firewall")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", lambda: self.ui_q.put("quit")),
        )
        self.tray = pystray.Icon("PhoneBridge", make_icon(64), "PhoneBridge", menu)
        self.tray.run_detached()

    # ---- lembrar posição/tamanho da janela
    def _virtual_screen(self):
        try:
            u = ctypes.windll.user32
            return u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        except Exception:  # noqa: BLE001
            return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _restore_window(self):
        geo = "440x720"
        w = self.cfg.get("window")
        if isinstance(w, dict):
            try:
                vx, vy, vw, vh = self._virtual_screen()
                ww = max(400, min(int(w["w"]), vw))
                hh = max(640, min(int(w["h"]), vh))
                x = max(vx, min(int(w["x"]), vx + vw - ww))  # garante que fique dentro de alguma tela
                y = max(vy, min(int(w["y"]), vy + vh - hh))
                geo = f"{ww}x{hh}+{x}+{y}"
            except (KeyError, TypeError, ValueError):
                pass
        self.root.geometry(geo)
        if self.cfg.get("zoomed"):
            try:
                self.root.state("zoomed")
            except tk.TclError:
                pass

    def _on_configure(self, e):
        if e.widget is not self.root:
            return
        if self._win_job:
            self.root.after_cancel(self._win_job)
        self._win_job = self.root.after(600, self._save_window)

    def _save_window(self):
        self._win_job = None
        try:
            state = self.root.state()
        except tk.TclError:
            return
        if state == "normal":
            self.cfg["window"] = {"x": self.root.winfo_x(), "y": self.root.winfo_y(),
                                  "w": self.root.winfo_width(), "h": self.root.winfo_height()}
            self.cfg["zoomed"] = False
        elif state == "zoomed":
            self.cfg["zoomed"] = True
        else:
            return
        save_config(self.cfg)

    # ---- firewall
    def _cur_port(self):
        try:
            return int(self.port_var.get())
        except ValueError:
            return DEFAULT_PORT

    def check_firewall(self, port, force=False):
        if os.name != "nt" or self._fw_busy:
            return
        if fw_rule_exists(port):
            if force:
                self.log(f"Firewall: a regra para a porta {port} já existe.")
            return
        declined = self.cfg.setdefault("fw_declined", [])
        if port in declined and not force:
            return
        ok = messagebox.askyesno(
            "PhoneBridge - Firewall",
            f"Para o celular conseguir conectar neste PC mesmo com a rede como \"Pública\", "
            f"o PhoneBridge pode criar uma regra no Firewall do Windows liberando a porta {port} "
            f"somente para dispositivos da sua rede local.\n\n"
            f"O Windows vai pedir permissão de administrador. Criar a regra agora?",
            parent=self.root,
        )
        if not ok:
            if port not in declined:
                declined.append(port)
                save_config(self.cfg)
            self.log("Firewall: regra não criada. Se o celular não conectar, deixe a rede como Privada "
                     'ou use o menu da bandeja > "Liberar no Firewall".')
            return
        if port in declined:
            declined.remove(port)
            save_config(self.cfg)
        self._fw_busy = True
        self.log("Firewall: aguardando a permissão de administrador...")
        threading.Thread(target=self._fw_create, args=(port,), daemon=True).start()

    def _fw_create(self, port):
        created = False
        if fw_add_rule_elevated(port):
            for _ in range(20):
                time.sleep(0.5)
                if fw_rule_exists(port):
                    created = True
                    break
        if created:
            self.log(f"Firewall: porta {port} liberada para a rede local.")
        else:
            declined = self.cfg.setdefault("fw_declined", [])
            if port not in declined:
                declined.append(port)
                save_config(self.cfg)
            self.log("Firewall: não foi possível criar a regra (permissão negada?).")
        self._fw_busy = False

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()
        if not self.hint_shown and self.tray:
            self.hint_shown = True
            try:
                self.tray.notify("Continua em execução na bandeja do sistema.", "PhoneBridge")
            except Exception:  # noqa: BLE001
                pass

    # ---- ações
    def log(self, msg):
        self.log_q.put(msg)

    def poll(self):
        while not self.log_q.empty():
            self.logbox.config(state="normal")
            self.logbox.insert("end", self.log_q.get() + "\n")
            self.logbox.see("end")
            self.logbox.config(state="disabled")
        while not self.ui_q.empty():
            act = self.ui_q.get()
            if act == "show":
                self.show()
            elif act == "mute":
                self.toggle_mute()
            elif act == "toggle":
                self.toggle()
            elif act == "firewall":
                self.check_firewall(self._cur_port(), force=True)
            elif act == "quit":
                self.quit()
                return
        self.update_status()
        self.root.after(200, self.poll)

    def update_status(self):
        if not self.server:
            text, col = "Servidor parado", MUTED_TXT
        else:
            with self.server.lock:
                clients = list(self.server.clients.values())
            if clients:
                ips = ", ".join(sorted({ip for ip, _ in clients}))
                text, col = f"Conectado: {ips}", OK
            else:
                text, col = f"Aguardando conexão na porta {self.server.port}", ACCENT
        self.status.config(text=text)
        self.dot.itemconfig(self.dot_id, fill=col)

    def toggle_mute(self):
        self.muted = not self.muted
        if self.server:
            self.server.muted = self.muted
        if self.muted:
            self.mute_btn.set("Microfone silenciado", DANGER, "#f4726f")
        else:
            self.mute_btn.set("Silenciar microfone", SURFACE, BORDER, TEXT)
        if self.tray:
            self.tray.icon = make_icon(64, self.muted)
            self.tray.update_menu()
        self.log("Microfone silenciado" if self.muted else "Microfone reativado")

    def toggle(self):
        if self.server:
            self.server.stop()
            self.server = None
            self.btn.set("Iniciar servidor", ACCENT, ACCENT_HOVER)
            if self.tray:
                self.tray.update_menu()
            return
        try:
            port = int(self.port_var.get())
        except ValueError:
            self.log("Porta inválida")
            return
        idx = next((i for i, n in self.devices if n == self.dev_var.get()), None)
        try:
            self.server = BridgeServer(port, idx, self.log)
            self.server.muted = self.muted
            self.server.start()
            if not self.has_cable:
                self.log("Aviso: sem VB-Cable, o modo microfone toca só na saída escolhida; "
                         "outros apps não o verão como microfone. O modo alto-falante funciona normalmente.")
            self.btn.set("Parar servidor", SURFACE, BORDER, TEXT)
            self.cfg.update(port=port, device=self.dev_var.get())
            save_config(self.cfg)
        except OSError as e:
            self.server = None
            self.log(f"Não foi possível abrir a porta: {e}")
        if self.tray:
            self.tray.update_menu()
        if self.server:
            self.check_firewall(port)

    def quit(self):
        self._save_window()
        if self.server:
            self.server.stop()
        if self.tray:
            self.tray.stop()
        try:
            if self.lock_sock:
                self.lock_sock.close()
        except OSError:
            pass
        self.root.destroy()


if __name__ == "__main__":
    lock = acquire_single_instance()
    if lock is None:
        raise SystemExit(0)  # já está aberto: a janela existente foi trazida à frente
    root = tk.Tk()
    App(root, lock)
    root.mainloop()
