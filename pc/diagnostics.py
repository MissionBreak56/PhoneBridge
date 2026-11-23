"""Relatório de diagnóstico para copiar e colar ao pedir ajuda (sem segredos)."""
import platform
import socket
import sys

import protocol as pr
import sysutil
from version import APP_VERSION


def _port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def build_report(port, backend, server=None, registry=None):
    lines = [f"PhoneBridge {APP_VERSION}", f"Sistema: {platform.platform()}", f"Python: {sys.version.split()[0]}"]
    lines.append(f"IPs locais: {', '.join(pr.local_ips()) or 'nenhum'} (principal: {pr.primary_ip() or '?'})")
    if server:
        lines.append(f"Servidor: ligado na porta {server.port} (modo legado {'LIGADO' if server.allow_legacy else 'desligado'})")
        with server.lock:
            lines.append(f"Sessões ativas: {len(server.clients)}")
        lines.append("Contadores: " + ", ".join(f"{k}={v}" for k, v in sorted(server.stats.items())) if server.stats
                     else "Contadores: (vazio)")
    else:
        lines.append(f"Servidor: parado (porta {port} {'livre' if _port_free(port) else 'EM USO por outro programa'})")
    lines.append(f"Opus: {'disponível' if pr.opus_available() else 'indisponível (usa PCM)'}")
    try:
        ready = backend.virtual_mic_ready()
        lines.append(f"Microfone virtual: {'pronto' if ready else 'não encontrado'}")
        lines.append(f"Dispositivos de saída: {len(backend.list_output_devices())}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"Áudio: erro ao consultar ({e})")
    if registry is not None:
        devs = registry.list_devices()
        lines.append(f"Celulares pareados: {sum(1 for d in devs if d['active'])} (pendentes: {sum(1 for d in devs if not d['active'])})")
    if sysutil.IS_WIN:
        state = sysutil.fw_rules_state(port)
        lines.append(f"Regras de Firewall: {state}")
        pubs = sysutil.public_networks()
        lines.append("Redes públicas conectadas: " + (", ".join(pubs) if pubs else "nenhuma"))
    return "\n".join(lines)
