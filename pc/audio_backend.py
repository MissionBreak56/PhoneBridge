"""Backends de áudio: Windows (WASAPI via pyaudiowpatch) e Linux (PulseAudio/PipeWire via pactl/parec/pacat).

Interface comum:
  backend.list_output_devices() -> [(id, nome)]
  backend.preferred_device(devices) -> id | None
  backend.open_mic_sink(device_id) -> sink (.rate, .channels, .name, .write(mono_pcm), .close())
  backend.open_loopback() -> fonte (.rate, .channels, .name, .read(timeout) -> bytes de 10 ms, .close())
"""
import queue
import subprocess
import sys
import threading

import numpy as np

IS_WIN = sys.platform == "win32"


def chunk_bytes(rate, channels):
    """Tamanho de 10 ms de áudio PCM 16 bits."""
    return rate // 100 * channels * 2


class Backend:
    name = "base"
    virtual_mic_name = ""

    def list_output_devices(self):
        raise NotImplementedError

    def preferred_device(self, devices):
        return devices[0][0] if devices else None

    def virtual_mic_ready(self):
        return False

    def virtual_mic_hint(self):
        return ""

    def prepare(self):
        pass

    def open_mic_sink(self, device_id):
        raise NotImplementedError

    def open_loopback(self):
        raise NotImplementedError

    def shutdown(self):
        pass


# ======================================================================= Windows
class WinMicSink:
    def __init__(self, p, stream, rate, out_ch, name):
        self.p, self.stream, self.rate, self.out_ch, self.name = p, stream, rate, out_ch, name
        self.channels = 1  # o celular envia mono

    def write(self, data):
        if self.out_ch == 2:
            data = np.repeat(np.frombuffer(data, dtype=np.int16), 2).tobytes()
        self.stream.write(data)

    def close(self):
        try:
            self.stream.stop_stream()
            self.stream.close()
        finally:
            self.p.terminate()


class WinLoopback:
    def __init__(self, p, stream, q, rate, in_ch, out_ch, name):
        self.p, self.stream, self.q = p, stream, q
        self.rate, self.in_ch, self.channels, self.name = rate, in_ch, out_ch, name
        self.silence = bytes(chunk_bytes(rate, out_ch))

    def read(self, timeout=0.05):
        try:
            data = self.q.get(timeout=timeout)
        except queue.Empty:
            return self.silence  # o loopback não gera dados em silêncio
        if self.in_ch > 2:
            arr = np.frombuffer(data, dtype=np.int16).reshape(-1, self.in_ch)
            data = np.ascontiguousarray(arr[:, :2]).tobytes()
        return data

    def close(self):
        try:
            self.stream.stop_stream()
            self.stream.close()
        finally:
            self.p.terminate()


class WindowsBackend(Backend):
    name = "windows"

    def __init__(self):
        import pyaudiowpatch as pyaudio

        self.pa = pyaudio

    def list_output_devices(self):
        pyaudio = self.pa
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

    def preferred_device(self, devices):
        for i, n in devices:
            if "CABLE Input" in n:
                return i
        return devices[0][0] if devices else None

    def virtual_mic_ready(self):
        return any("CABLE Input" in n for _, n in self.list_output_devices())

    def virtual_mic_hint(self):
        if self.virtual_mic_ready():
            return 'VB-Cable detectado. Nos apps (Discord, Zoom...) escolha "CABLE Output" como microfone.'
        return ("VB-Cable não encontrado. Necessário só para usar o celular como microfone "
                "(clique em Instalar VB-Cable, reinicie o Windows e clique em Atualizar).")

    def open_mic_sink(self, device_id):
        pyaudio = self.pa
        p = pyaudio.PyAudio()
        try:
            dev = p.get_device_info_by_index(device_id)
            rate = int(dev["defaultSampleRate"])
            out_ch = 2 if dev["maxOutputChannels"] >= 2 else 1
            stream = p.open(
                format=pyaudio.paInt16, channels=out_ch, rate=rate, output=True,
                output_device_index=device_id, frames_per_buffer=rate // 100,
            )
        except Exception:
            p.terminate()
            raise
        return WinMicSink(p, stream, rate, out_ch, dev["name"])

    def open_loopback(self):
        pyaudio = self.pa
        p = pyaudio.PyAudio()
        try:
            try:
                lb = p.get_default_wasapi_loopback()
            except Exception:
                raise RuntimeError("loopback WASAPI indisponível")
            rate = int(lb["defaultSampleRate"])
            in_ch = int(lb["maxInputChannels"])
            out_ch = 1 if in_ch == 1 else 2
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
                format=pyaudio.paInt16, channels=in_ch, rate=rate, input=True,
                input_device_index=lb["index"], frames_per_buffer=rate // 100, stream_callback=cb,
            )
        except Exception:
            p.terminate()
            raise
        return WinLoopback(p, stream, q, rate, in_ch, out_ch, lb["name"])


# ======================================================================= Linux
LINUX_RATE = 48000
LINUX_MIC_SINK = "phonebridge_mic"
LINUX_MIC_SOURCE = "phonebridge_mic_source"


def _pactl(*args):
    return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=10)


class LinuxMicSink:
    def __init__(self, proc, name):
        self.proc, self.name = proc, name
        self.rate, self.channels = LINUX_RATE, 1

    def write(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class LinuxLoopback:
    def __init__(self, proc, name, channels=2):
        self.proc, self.name = proc, name
        self.rate, self.channels = LINUX_RATE, channels
        self.q = queue.Queue(maxsize=8)
        self.silence = bytes(chunk_bytes(self.rate, channels))
        self.alive = True
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        n = chunk_bytes(self.rate, self.channels)
        try:
            while self.alive:
                buf = b""
                while len(buf) < n:  # o pipe pode devolver leituras curtas
                    part = self.proc.stdout.read(n - len(buf))
                    if not part:
                        return
                    buf += part
                try:
                    self.q.put_nowait(buf)
                except queue.Full:
                    try:
                        self.q.get_nowait()
                        self.q.put_nowait(buf)
                    except (queue.Empty, queue.Full):
                        pass
        finally:
            self.alive = False

    def read(self, timeout=0.05):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            if not self.alive:
                raise RuntimeError("captura do sistema encerrada (parec)")
            return self.silence

    def close(self):
        self.alive = False
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class LinuxBackend(Backend):
    """Usa só pactl/parec/pacat (pacote pulseaudio-utils; funciona também no PipeWire)."""

    name = "linux"
    virtual_mic_name = "PhoneBridge Mic"

    def __init__(self):
        self.modules = []

    def list_output_devices(self):
        r = _pactl("list", "short", "sinks")
        devices = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                devices.append((parts[1], parts[1]))
        return devices

    def preferred_device(self, devices):
        for i, _ in devices:
            if i == LINUX_MIC_SINK:
                return i
        return devices[0][0] if devices else None

    def virtual_mic_ready(self):
        return any(i == LINUX_MIC_SINK for i, _ in self.list_output_devices())

    def virtual_mic_hint(self):
        if self.virtual_mic_ready():
            return 'Microfone virtual criado. Nos apps (Discord, Zoom...) escolha "PhoneBridge Mic" como microfone.'
        return "Não foi possível criar o microfone virtual (precisa de pactl/PulseAudio ou PipeWire-Pulse)."

    def prepare(self):
        """Cria o microfone virtual (sink nulo + fonte remapeada) se ainda não existir."""
        try:
            if self.virtual_mic_ready():
                return
            prev_default = _pactl("get-default-sink").stdout.strip()
            r = _pactl("load-module", "module-null-sink", f"sink_name={LINUX_MIC_SINK}",
                       "sink_properties=device.description=PhoneBridge_Mic_(saida)")
            if r.returncode == 0 and r.stdout.strip().isdigit():
                self.modules.append(r.stdout.strip())
            r = _pactl("load-module", "module-remap-source", f"master={LINUX_MIC_SINK}.monitor",
                       f"source_name={LINUX_MIC_SOURCE}",
                       "source_properties=device.description=PhoneBridge_Mic")
            if r.returncode == 0 and r.stdout.strip().isdigit():
                self.modules.append(r.stdout.strip())
            # o sink virtual não pode virar a saída padrão: o som do sistema deixaria de tocar nas caixas
            if prev_default and prev_default != LINUX_MIC_SINK:
                if _pactl("get-default-sink").stdout.strip() == LINUX_MIC_SINK:
                    _pactl("set-default-sink", prev_default)
        except (OSError, subprocess.SubprocessError):
            pass

    def shutdown(self):
        for m in reversed(self.modules):
            try:
                _pactl("unload-module", m)
            except (OSError, subprocess.SubprocessError):
                pass
        self.modules.clear()

    def open_mic_sink(self, device_id):
        dev = device_id or LINUX_MIC_SINK
        proc = subprocess.Popen(
            ["pacat", "--playback", f"--device={dev}", "--format=s16le", f"--rate={LINUX_RATE}",
             "--channels=1", "--latency-msec=20", "--client-name=PhoneBridge", "--stream-name=celular"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0,
        )
        return LinuxMicSink(proc, dev)

    def open_loopback(self):
        sink = _pactl("get-default-sink").stdout.strip()
        dev = f"{sink}.monitor" if sink else "@DEFAULT_MONITOR@"
        proc = subprocess.Popen(
            ["parec", f"--device={dev}", "--format=s16le", f"--rate={LINUX_RATE}", "--channels=2",
             "--latency-msec=20", "--client-name=PhoneBridge", "--stream-name=sistema"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
        )
        return LinuxLoopback(proc, dev)


def create_backend():
    return WindowsBackend() if IS_WIN else LinuxBackend()
