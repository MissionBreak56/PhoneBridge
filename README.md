# PhoneBridge

Use o celular Android como **microfone** ou **alto-falante** do PC pela rede Wi-Fi.
Projeto próprio, sem anúncios e sem telemetria. Servidor para **Windows** e **Linux**.

## Como funciona

- **Microfone:** o celular captura o áudio e envia ao PC, que o toca num dispositivo de saída virtual. No Windows isso usa o [VB-Cable](https://vb-audio.com/Cable/) (o app instala para você); no Linux o app cria sozinho um microfone virtual chamado **PhoneBridge Mic**.
- **Alto-falante:** o PC captura o som do sistema (WASAPI loopback no Windows, monitor do PulseAudio/PipeWire no Linux) e envia ao celular.
- **Protocolo:** TCP porta 5800 (áudio) e UDP porta 5801 (descoberta). PCM 16 bits cru ou Opus. Veja [PROTOCOL.md](PROTOCOL.md).

## Instalação

Baixe os arquivos na página de [Releases](../../releases).

### Windows 10/11

1. Execute `PhoneBridgeSetup.exe`. Ele pode criar atalhos, iniciar o PhoneBridge com o Windows, liberar o Firewall e definir as redes conectadas como Privadas. (Ou use o `PhoneBridgeServer.exe` portátil.)
2. Para usar o celular como microfone, clique em **Instalar VB-Cable** no app, confirme, reinicie o Windows e clique em **Atualizar**.
3. Clique em **Iniciar servidor**.

O app também pede, quando necessário, permissão de administrador para criar as regras de Firewall (TCP do áudio e UDP da descoberta) e mudar para **Privada** toda rede conectada que estiver como **Pública**. Ele pergunta uma vez por rede.

### Linux

1. Instale `pulseaudio-utils` (comandos `pactl`, `parec` e `pacat`; funciona também com PipeWire).
2. Extraia `PhoneBridgeServer-linux.tar.gz` e execute `./PhoneBridgeServer-linux`.
3. Nos apps (Discord, Zoom...) escolha **PhoneBridge Mic** como microfone.

### Android

Instale o `PhoneBridge-android.apk` (permita fontes desconhecidas). PC e celular precisam estar na mesma rede.

## Conectar o celular ao PC

- **Buscar PC:** o app procura o PC na rede sozinho (também faz isso na primeira abertura).
- **QR code:** no PC clique em **Mostrar QR code para conectar**; no celular toque em **QR code**. Lê e conecta de uma vez.
- Ou digite o IP mostrado na janela do PC.

## Recursos

| Recurso | Onde |
|---|---|
| Descoberta automática e QR code | Celular + PC |
| Reconexão automática se o Wi-Fi cair | Celular (Configurações) |
| Compressão Opus opcional (experimental) | Celular escolhe, PC permite |
| Cancelamento de eco e supressão de ruído | Celular, modo microfone |
| Serviço em primeiro plano com botão Parar | Celular |
| Isenção da otimização de bateria | Celular (pergunta uma vez) |
| Áudio de baixa latência (prioridade de áudio, modo de baixa latência do Android e do Wi-Fi) | Celular |
| Medidor de nível do áudio | PC |
| Iniciar com o sistema, bandeja, silenciar microfone | PC |
| Firewall e redes privadas automáticos, instalação guiada do VB-Cable | PC (Windows) |

## Desenvolvimento

- `pc/protocol.py` protocolo e Opus; `pc/engine.py` servidor; `pc/audio_backend.py` áudio do Windows e do Linux; `pc/sysutil.py` firewall, autostart e VB-Cable; `pc/server.py` interface.
- Testes do servidor (sem áudio real): `cd pc && python tests/test_engine.py`.
- O CI (`.github/workflows/build.yml`) compila o APK, o `.exe`, o instalador e o binário Linux. Para lançar uma release, rode o workflow **Build** manualmente informando a versão (ex.: `v2.0.0`).

## Limites

- Sem criptografia e sem autenticação: use só em rede de confiança.
- Uma conexão de cada vez por modo. O APK é de depuração (assinado com a chave de debug).
- O Opus só é usado quando o áudio do PC está em 48 kHz; senão volta para PCM automaticamente.
