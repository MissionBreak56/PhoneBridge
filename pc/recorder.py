"""Gravação local do áudio que passa pelo PhoneBridge (WAV, PCM 16 bits)."""
import os
import threading
import time
import wave

MAX_FILE_BYTES = 3_500_000_000  # abaixo do limite de 4 GB do formato WAV


def default_dir():
    base = os.path.join(os.path.expanduser("~"), "Documents")
    if not os.path.isdir(base):
        base = os.path.expanduser("~")
    return os.path.join(base, "PhoneBridge")


class WavRecorder:
    def __init__(self, directory, rate, channels, label="audio"):
        os.makedirs(directory, exist_ok=True)
        self.directory, self.rate, self.channels, self.label = directory, rate, channels, label
        self.lock = threading.Lock()
        self.wav = None
        self.path = None
        self.written = 0
        self.closed = False
        self._open()

    def _open(self):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        n = 0
        while True:
            suffix = f"_{n}" if n else ""
            path = os.path.join(self.directory, f"PhoneBridge_{self.label}_{stamp}{suffix}.wav")
            if not os.path.exists(path):
                break
            n += 1
        w = wave.open(path, "wb")
        w.setnchannels(self.channels)
        w.setsampwidth(2)
        w.setframerate(self.rate)
        self.wav, self.path, self.written = w, path, 0

    def write(self, pcm):
        with self.lock:
            if self.closed or not pcm:
                return
            if self.written + len(pcm) > MAX_FILE_BYTES:
                self.wav.close()
                self._open()
            self.wav.writeframes(pcm)
            self.written += len(pcm)

    def close(self):
        """Fecha o arquivo escrevendo o cabeçalho final (seguro chamar mais de uma vez)."""
        with self.lock:
            if not self.closed:
                self.closed = True
                try:
                    self.wav.close()
                except (OSError, wave.Error):
                    pass
