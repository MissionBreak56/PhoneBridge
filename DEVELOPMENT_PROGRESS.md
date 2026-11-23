# Acompanhamento do desenvolvimento

Arquivo interno: tarefas, decisões técnicas e pendências.

## Concluído na 3.0.0
- Protocolo v3 (pareamento por QR, autenticação mútua HMAC-SHA256, AES-256-GCM com contador, heartbeat, expiração, limites, bloqueio).
- Engine do PC reescrita (sessões, um celular por modo, substituição ao reconectar, shutdown que acorda threads, medição RMS/pico/clipping, gravação WAV).
- Android: `SecureChannel`, `JitterQueue`, `PairingStore` (Keystore), serviço com fila por duração (sem `available()`), pareamento por QR v3, lista de PCs pareados, permissões corrigidas.
- Atualizador assinado (Ed25519) com SHA-256; VB-Cable com hash e Zip Slip; firewall por nome exato e rede privada opcional.
- CI: testes, ruff, pip-audit, APK release assinado, SHA256SUMS assinado, verificação antes de publicar.
- Documentação: README, PROTOCOL, SECURITY, CONTRIBUTING, CHANGELOG.

## Decisões
- **Sem TLS:** celular e PC não têm certificados e o Android só tem X25519 a partir da API 33 (o app suporta API 24). Optou-se por chave compartilhada de 256 bits trocada por QR + HKDF + AES-GCM (API 24+ sem bibliotecas extras). Custo: sem sigilo futuro.
- **Legado desligado por padrão** nos dois lados; ligar é sempre explícito.
- **Oboe não adotado:** exigiria código nativo (NDK/CMake) e não há medição que comprove ganho sobre `AudioTrack` em modo de baixa latência.
- **Compose/MVVM não migrados:** sem aparelho/emulador para validar a interface, a reescrita traria risco alto sem teste; ficou como trabalho futuro.
- **Opus em Java puro (Concentus)** para não depender de código nativo no Android.

## Pendências
- Migração da UI para Compose/Material 3, DataStore, histórico de conexões e favoritos.
- Gravação em FLAC/OGG/MP3; reamostragem para Opus em 44,1 kHz.
- Testes instrumentados no Android (serviço, QR, permissões) em emulador.
- Proteção do `devices.json` no Windows com DPAPI.
- Bluetooth e dispositivo de áudio virtual próprio (sem driver de kernel).
