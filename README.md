# PhoneBridge

Use o celular Android como **microfone** ou **alto-falante** do PC pelo Wi-Fi, sem anúncios e sem telemetria.
O app do PC roda em **Windows 10/11** e **Linux**; o app do celular precisa de **Android 7.0** (API 24) ou mais novo.

Versão atual: **3.0.0** · [Baixar na página de Releases](../../releases/latest)

---

## Sumário

1. [Como funciona](#como-funciona)
2. [Download](#download)
3. [Instalação](#instalação)
4. [Conectar o celular ao PC](#conectar-o-celular-ao-pc)
5. [Usando](#usando)
6. [Configurações do app](#configurações-do-app)
7. [Rede, portas e firewall](#rede-portas-e-firewall)
8. [Solução de problemas](#solução-de-problemas)
9. [Compilar a partir do código](#compilar-a-partir-do-código)
10. [Estrutura do repositório](#estrutura-do-repositório)
11. [Privacidade e segurança](#privacidade-e-segurança)
12. [Limitações](#limitações)
13. [Créditos e licenças](#créditos-e-licenças)
14. [Histórico de versões](#histórico-de-versões)

---

## Como funciona

```
Modo Microfone:      celular (microfone) ──Wi-Fi──▶ PC ──▶ saída virtual ──▶ apps (Discord, Zoom...)
Modo Alto-falante:   celular (caixa de som) ◀──Wi-Fi── PC (som do sistema)
```

- **Microfone:** o celular captura o áudio e envia ao PC, que o toca num dispositivo de saída virtual.
  No **Windows** isso usa o [VB-Cable](https://vb-audio.com/Cable/) (o app instala para você).
  No **Linux** o app cria sozinho um microfone virtual chamado **PhoneBridge Mic**.
- **Alto-falante:** o PC captura o som do sistema (WASAPI loopback no Windows; monitor do PulseAudio/PipeWire no Linux) e envia ao celular.
- **Transporte:** TCP (áudio) e UDP (descoberta). O áudio vai em PCM 16 bits ou, opcionalmente, em Opus.
- **Segurança (v3):** o celular é **pareado** com o PC por QR code; toda conexão tem autenticação mútua e o áudio vai **criptografado** (AES-256-GCM). O protocolo está em [PROTOCOL.md](PROTOCOL.md) e a política de segurança em [SECURITY.md](SECURITY.md).

## Download

Na página de [Releases](../../releases/latest):

| Arquivo | Para quê |
|---|---|
| `PhoneBridge-android.apk` | App do celular (APK de depuração; é preciso permitir a instalação de fontes desconhecidas e, ao trocar de versão, desinstalar a anterior) |
| `PhoneBridgeSetup.exe` | Instalador para Windows (atalhos, iniciar com o Windows, Firewall e redes privadas) |
| `PhoneBridgeServer.exe` | Versão portátil para Windows, sem instalar |
| `PhoneBridgeServer-linux.tar.gz` | Servidor para Linux (binário único) |

## Instalação

### Windows 10/11

1. Execute `PhoneBridgeSetup.exe`. Na instalação você pode marcar: atalho na área de trabalho, **iniciar com o Windows** (fica na bandeja), **criar as regras do Firewall** (só rede local) e, separadamente e desmarcado por padrão, **mudar as redes conectadas de Pública para Privada**. Se preferir, use só o `PhoneBridgeServer.exe` portátil.
2. Para usar o celular como **microfone**, clique em **Instalar VB-Cable** no app. Ele baixa o pacote oficial do VB-Audio e abre o instalador; confirme a permissão de administrador, clique em *Install Driver*, **reinicie o Windows** e depois clique em **Atualizar** no PhoneBridge.
   Se o download automático falhar, o app abre a página oficial para baixar manualmente. O VB-Cable é gratuito (donationware) e não vem embutido neste projeto.
3. Clique em **Iniciar servidor**.

O modo **alto-falante** não precisa do VB-Cable.

### Linux

1. Instale o `pactl`, `parec` e `pacat` (pacote `pulseaudio-utils`; funciona também com PipeWire, que expõe a mesma interface).
2. Extraia o `PhoneBridgeServer-linux.tar.gz` e execute `./PhoneBridgeServer-linux`.
3. Nos apps (Discord, Zoom...), escolha **PhoneBridge Mic** como microfone.

O microfone virtual é criado quando o programa abre e removido quando ele fecha. A interface usa Tk; em desktops sem bandeja do sistema o programa fica só na janela (fechar a janela encerra o programa).

### Android

Instale o `PhoneBridge-android.apk`. Na primeira vez o app pede permissão para usar o **microfone** (e para **notificações**, no Android 13+). PC e celular precisam estar na **mesma rede**.

## Conectar o celular ao PC

Na versão 3 o celular precisa ser **pareado** uma vez com cada PC. Depois disso conecta sozinho.

1. **Parear (uma vez):** no PC, clique em **Parear celular (QR code)**; no celular, toque em **QR code** e aponte para o quadro. O app guarda a chave (cifrada com o Android Keystore) e já conecta. O QR vale por 10 minutos e para um aparelho só; contém a chave de pareamento, então mostre apenas ao seu celular.
2. **Depois do pareamento:** toque em **Buscar PC** (acha o PC na rede e atualiza o IP se ele mudou) ou digite o IP e toque em **Iniciar**.
3. **Remover um celular:** no PC, **Celulares autorizados** > Remover (a sessão cai na hora). No celular: Configurações > **PCs pareados**.

PCs e celulares da versão 2.x ou anterior não têm pareamento: veja [Compatibilidade com versões antigas](#compatibilidade-com-versões-antigas).

## Usando

No celular:

- Escolha **Microfone** ou **Alto-falante** e toque em **Iniciar**.
- **Silenciar** corta o áudio sem desconectar; o controle de volume ajusta o ganho do microfone ou o volume do alto-falante.
- Enquanto transmite, há uma notificação com o botão **Parar**. Tocar nela abre o app.
- Se a conexão cair, o app **reconecta sozinho** (mostra "Reconectando…") até você parar.

No PC (janela, ícone da bandeja ou ambos):

- **Iniciar/Parar servidor**, **Silenciar microfone** e **Parear celular (QR code)** (também no menu da bandeja).
- **Nível do áudio:** barra que mostra o sinal que está passando (fica vermelha quando o áudio estoura, o chamado clipping).
- **Saída do microfone:** escolha o dispositivo onde o áudio do celular toca (no Windows, `CABLE Input`).
- **Mais opções:** permitir Opus, iniciar com o Windows/sistema (abre escondido na bandeja e liga o servidor), **gravar o áudio em WAV** (pasta Documentos/PhoneBridge; vale para as próximas conexões), responder à busca automática, verificar atualizações, **diagnóstico de conexão** (texto para copiar ao pedir ajuda) e o modo legado sem proteção.
- **Instalar VB-Cable**, **Atualizar** (lista de dispositivos) e **Config. de som**.
- Só uma janela fica aberta: abrir de novo traz a existente para a frente. A janela lembra posição e tamanho.

As configurações do PC ficam em `%APPDATA%\PhoneBridge\config.json` (Windows) ou `~/.config/phonebridge/config.json` (Linux).

## Configurações do app

| Opção | O que faz | Padrão |
|---|---|---|
| Porta do servidor | Porta TCP do PC | 5800 |
| Fonte de áudio | Padrão, Microfone, Microfone traseiro, Reconhecimento de voz ou Comunicação por voz | Padrão |
| Compressão do áudio | PCM sem compressão ou **Opus** (usa menos Wi-Fi; experimental) | PCM |
| PCs pareados | Lista e remove os PCs pareados | — |
| Permitir PCs antigos sem proteção | Aceita falar com PhoneBridge 2.x ou anterior no PC, **sem senha nem criptografia** | Desligado |
| Reconectar automaticamente | Continua tentando se o Wi-Fi ou o PC cair | Ligado |
| Cancelamento de eco | Efeito do Android no modo microfone, se o aparelho suportar | Desligado |
| Supressão de ruído | Efeito do Android no modo microfone, se o aparelho suportar | Desligado |
| Otimização de bateria | Atalho para permitir funcionar com a tela apagada (o app também pergunta uma vez) | — |
| Silenciar automaticamente | Silencia durante chamadas | Desligado |
| Manter a tela ligada | Enquanto conectado | Desligado |
| Tema | Sistema, Claro ou Escuro | Sistema |

Ligar o cancelamento de eco ou a supressão de ruído com a fonte em "Padrão" faz o app usar a fonte "Comunicação por voz", que costuma funcionar melhor com esses efeitos.

**Opus:** o celular pede, o PC permite e a taxa precisa ser 48 kHz. Se qualquer condição falhar, o PhoneBridge usa PCM automaticamente. Se o PC for de uma versão antiga (só protocolo 1), o celular também cai para PCM.

## Rede, portas e firewall

| Porta | Protocolo | Uso |
|---|---|---|
| 5800 | TCP | Áudio (configurável) |
| 5801 | UDP | Descoberta automática do PC |
| 47653 | TCP, só em `127.0.0.1` | Trava interna para abrir uma janela só |

O PC só aceita conexões de **redes locais** (privadas, link-local e CGNAT/Tailscale); origens de fora são recusadas mesmo que a porta seja exposta por engano.

**No Windows** o app oferece, **em perguntas separadas e sempre opcionais**: (1) criar as regras de Firewall (TCP do áudio e UDP da descoberta, limitadas à sub-rede local) e (2) mudar para **Privada** as redes conectadas que estiverem como **Pública**. Cada uma pede permissão de administrador; recusar é lembrado. A segunda deixa o PC mais visível nessas redes (compartilhamento do Windows), então só aceite em redes de confiança. O app mexe apenas em regras chamadas `PhoneBridge <porta>` e `PhoneBridge descoberta`. Para refazer depois: menu da bandeja > **Firewall e redes (opcional)**. Se o app não conseguir verificar o Firewall, ele avisa em vez de presumir que está tudo certo. Redes de domínio corporativo não são alteradas.

Redes de convidado e alguns roteadores bloqueiam a comunicação entre aparelhos ("isolamento de cliente"); nesses casos a busca automática e a conexão não funcionam.

## Solução de problemas

| Problema | O que fazer |
|---|---|
| "Buscar PC" não acha nada | Confirme que o PhoneBridge está com o servidor iniciado, que PC e celular estão na mesma rede e que o Firewall está liberado (menu da bandeja). Use o QR code ou o IP manual como alternativa |
| Conecta, mas sem som no modo microfone (Windows) | Instale o VB-Cable e nos apps escolha **CABLE Output** como microfone; veja o nível do áudio na janela do PC |
| Sem som no modo microfone (Linux) | Escolha **PhoneBridge Mic** como microfone no app; confira se `pactl`, `parec` e `pacat` estão instalados |
| O QR code não conecta | O QR usa o IP da interface principal; com VPN ou várias placas de rede ele pode apontar para a errada. Digite o IP manualmente |
| O áudio para com a tela apagada | Aceite o pedido de isenção da otimização de bateria (ou use a opção nas configurações) |
| Mensagem "O PC recusou a conexão" | Escolha uma saída de áudio válida no PC (modo microfone) e confira se o dispositivo não está em uso exclusivo por outro app |
| "Este PC ainda não foi pareado" / "O PC recusou este celular" | Pareie de novo: no PC **Parear celular (QR code)** e leia no celular. Acontece se o pareamento foi removido, expirou (10 min sem usar) ou se o PC foi reinstalado |
| "O PC exige pareamento" (app antigo no celular) | Atualize o app do celular para a versão 3 e pareie. Se não puder, ligue o modo legado só em rede de confiança |
| "Outro celular já está usando este modo" | O PC aceita um celular por modo (microfone e alto-falante podem ser dois celulares diferentes). Desconecte o outro |
| "Origem bloqueada" no registro do PC | Houve várias tentativas inválidas de um mesmo endereço; o bloqueio passa sozinho (de 1 a 15 min) |
| Cortes ou atraso no Wi-Fi fraco | Aproxime-se do roteador, prefira a rede de 5 GHz e teste o Opus |
| O PC fica aberto só na janela (Linux) | Falta uma bandeja de sistema no desktop; é esperado |

## Compilar a partir do código

### App Android

Requer JDK 17 e Android SDK (API 34). O CI usa o Gradle 8.7:

```bash
cd android
gradle assembleDebug
# APK em app/build/outputs/apk/debug/
```

### App do PC

```bash
cd pc
pip install -r requirements.txt pytest
python server.py            # --tray inicia escondido na bandeja e liga o servidor
python -m pytest tests -q   # 55 testes: protocolo, autenticação, criptografia, sessões, atualizador, sysutil
```

Dependências: `numpy`, `pyaudiowpatch` (só Windows), `pystray`, `Pillow`, `qrcode`, `opuslib-next` e `cryptography`. O Opus precisa da biblioteca nativa: `opus.dll` no Windows e `libopus.so.0` no Linux; sem ela o programa funciona só em PCM. No Linux também é preciso o Tk (`python3-tk`).

Os testes do Android (JVM, sem aparelho) rodam com `cd android && gradle testDebugUnitTest`; eles conferem a criptografia contra vetores gerados pelo Python.

### Executáveis e instalador (CI)

O workflow [`build.yml`](.github/workflows/build.yml) compila tudo no GitHub Actions:

- **Android:** testes unitários, APK de depuração e **APK de release assinado** (chave nos GitHub Secrets; o CI confere o certificado).
- **Qualidade:** `ruff` (erros de sintaxe/nomes) e `pip-audit` (vulnerabilidades conhecidas; só avisa).
- **Windows:** roda os testes, gera o `PhoneBridgeServer.exe` com PyInstaller (com o `opus.dll` do vcpkg dentro), abre o `.exe` para um teste de fumaça e monta o `PhoneBridgeSetup.exe` com o Inno Setup (`installer/`).
- **Linux:** roda os testes, gera o binário com PyInstaller (Ubuntu 22.04, para funcionar em distros mais antigas) e faz o teste de fumaça sob Xvfb.

Cada push em `main` compila e testa. Para **lançar uma release**, rode o workflow **Build** manualmente (Actions → Build → Run workflow) informando a versão, por exemplo `v3.0.1`; ele gera `SHA256SUMS`, assina a lista (Ed25519), **verifica tudo** e publica os arquivos. Segredos usados: `ANDROID_KEYSTORE_B64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` e `UPDATE_SIGNING_KEY` (veja [CONTRIBUTING.md](CONTRIBUTING.md)).

## Estrutura do repositório

```
android/                       app Android (Kotlin)
  app/src/main/java/com/phonebridge/
    MainActivity.kt            tela principal, busca de PC, leitor de QR, permissões
    AudioService.kt            serviço em primeiro plano: conexão, microfone/alto-falante, Opus, reconexão
    SecureChannel.kt           protocolo v3 (criptografia, autenticação, heartbeat) - sem dependência de Android
    JitterQueue.kt             fila de áudio limitada por duração - sem dependência de Android
    PairingStore.kt            PCs pareados (chaves cifradas com o Android Keystore) e link do QR
    DiscoveryClient.kt         descoberta de PCs por UDP
    PortraitCaptureActivity.kt leitor de QR em pé com moldura quadrada
    SettingsActivity.kt        configurações e lista de PCs pareados
  app/src/test/                testes unitários (JVM)
  app/src/main/java/org/concentus/   codec Opus em Java (código de terceiros, BSD)
pc/                            servidor do PC (Python)
  server.py                    interface (Tk), bandeja, pareamento, dispositivos autorizados
  engine.py                    servidor TCP: sessões, limites, heartbeat, gravação, medição
  protocol.py                  protocolo legado, Opus e descoberta
  security.py                  protocolo v3: chaves, registros cifrados, pareamento, limite de tentativas
  audio_backend.py             áudio: Windows (WASAPI) e Linux (PulseAudio/PipeWire)
  sysutil.py                   firewall, redes privadas, iniciar com o sistema, VB-Cable
  updater.py                   atualizador (GitHub Releases + assinatura Ed25519 + SHA-256)
  recorder.py                  gravação em WAV
  diagnostics.py               relatório de diagnóstico
  version.py                   versão do programa
  tests/                       testes (pytest) e cliente de referência do protocolo v3
installer/                     script do Inno Setup e do Firewall/redes (Windows)
PROTOCOL.md  SECURITY.md  CHANGELOG.md  CONTRIBUTING.md  DEVELOPMENT_PROGRESS.md
.github/workflows/build.yml    CI e release
```

## Privacidade e segurança

- Sem anúncios, contas ou telemetria; o áudio vai direto do celular para o PC, na sua rede. Conexões externas do PC: consulta às versões em `api.github.com` (pode ser desligada em Mais opções) e, só quando você clica, o download do VB-Cable.
- **Segurança da conexão (v3):** pareamento por QR code com chave de 256 bits, autenticação mútua (HMAC-SHA256), tráfego em AES-256-GCM com contador (sem repetição nem reordenação), limite de tentativas com bloqueio, só origens de rede local, limites de tamanho, heartbeat e expiração de sessão. Detalhes e limites em [SECURITY.md](SECURITY.md) e [PROTOCOL.md](PROTOCOL.md).
- As chaves ficam no Android Keystore (celular) e em `devices.json` com permissão restrita (PC; no Windows protegido pelo perfil do usuário).
- Permissões do Android: `INTERNET`, `RECORD_AUDIO` (só pedida no modo microfone), `MODIFY_AUDIO_SETTINGS`, `WAKE_LOCK`, `FOREGROUND_SERVICE` (e os tipos de microfone e mídia), `POST_NOTIFICATIONS`, `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` e a câmera (só no leitor de QR).

### Compatibilidade com versões antigas

PhoneBridge 2.x e anteriores não usam pareamento nem criptografia. Por segurança o PC da versão 3 **recusa** esses apps por padrão (o celular antigo vê "o PC recusou a conexão"), e o app da versão 3 só fala com PCs antigos se você ligar **Permitir PCs antigos sem proteção**. No PC existe o equivalente em Mais opções > **Aceitar apps antigos SEM proteção**. Não há volta automática para o modo inseguro: é sempre uma escolha sua. O ideal é atualizar os dois lados.

## Limitações

- **Criptografia própria sobre TCP, não TLS:** o protocolo usa pareamento por chave compartilhada (sem certificados) com AES-256-GCM; não há sigilo futuro. Quem vê o QR code na tela pode parear; mostre-o só ao seu celular.
- A interface do Android continua em Views/Material (não foi migrada para Compose) e não há histórico de conexões nem favoritos.
- Gravação só em WAV (FLAC, OGG e MP3 não foram implementados).
- O APK é assinado com a chave de release do projeto; quem instalou versões antigas (assinadas com chave de depuração) precisa **desinstalar** antes de instalar a 3.0.0, uma vez.
- Um celular por modo (microfone e alto-falante podem ser celulares diferentes).
- O Opus só é usado com áudio de 48 kHz; senão volta para PCM. Não há reamostragem.
- O modo microfone no Windows depende do VB-Cable (não dá para embutir um driver próprio).
- Sem Oboe/AAudio nativo: a baixa latência vem de prioridade de áudio, modo de baixa latência do `AudioTrack` e do Wi-Fi, buffers pequenos e fila limitada a 60 ms.
- Bluetooth e hotspot não têm suporte próprio: o hotspot do Android funciona como qualquer rede Wi-Fi (conecte o PC nele e use a Buscar PC ou o QR). Bluetooth (HFP/A2DP) tem latência e qualidade incompatíveis com este uso e não foi implementado.
- Testado de verdade: servidor e criptografia (automático, Windows e Linux no CI), áudio do Linux com PulseAudio real, protocolo entre o cliente Kotlin e o servidor Python. **Não testado em aparelhos**: o app Android completo (interface, serviço, QR), o instalador e a captura de áudio no Windows real (o CI só abre o programa).

## Créditos e licenças

- [Concentus](https://github.com/lostromb/concentus): implementação do Opus em Java, incluída em `android/app/src/main/java/org/concentus` (licença BSD, arquivo `LICENSE.txt` ao lado do código).
- [libopus](https://opus-codec.org/) (BSD), empacotada nos executáveis do PC e usada via `opuslib-next`.
- [zxing-android-embedded](https://github.com/journeyapps/zxing-android-embedded) (Apache 2.0), leitor de QR do Android.
- [VB-Cable](https://vb-audio.com/Cable/) do VB-Audio: driver de áudio virtual de terceiros, baixado pelo usuário; não faz parte deste projeto.
- Também usados: Material Components e AndroidX (Android); `pyaudiowpatch`, `pystray`, `Pillow`, `qrcode`, `numpy` (PC).

Este repositório ainda **não tem um arquivo de licença** para o código do PhoneBridge; sem ele, o padrão é "todos os direitos reservados". Se quiser permitir uso e contribuições, adicione um `LICENSE`.

## Histórico de versões

- **3.0.0** — Segurança: pareamento por QR, autenticação mútua, criptografia AES-256-GCM, limites e bloqueio de tentativas, só rede local; heartbeat e expiração de sessões; fila de áudio por duração (sem `available()`); atualizador assinado; gravação WAV; diagnóstico; APK de release assinado e SHA-256. Veja o [CHANGELOG.md](CHANGELOG.md).
- **2.0.1** — Leitor de QR em pé, com moldura quadrada.
- **2.0.0** — Busca automática do PC e QR code; reconexão automática; Opus opcional; cancelamento de eco e supressão de ruído; serviço com botão Parar e isenção de bateria; PC: medidor de nível, iniciar com o sistema, Firewall e redes privadas automáticos, instalação guiada do VB-Cable, versão para Linux e instalador do Windows; protocolo v2 documentado em `PROTOCOL.md`.
- **1.x** — Modos microfone e alto-falante em PCM; interface escura, bandeja e silenciar microfone no PC; uma única janela aberta; menos atraso (filas curtas, descarte do áudio atrasado, buffers TCP pequenos, Wi-Fi de baixa latência); regra de Firewall pelo app e janela que lembra posição e tamanho.
