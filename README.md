# PhoneBridge

Use o celular Android como **microfone** ou **alto-falante** do PC pelo Wi-Fi, sem anúncios e sem telemetria.
O app do PC roda em **Windows 10/11** e **Linux**; o app do celular precisa de **Android 7.0** (API 24) ou mais novo.

Versão atual: **2.0.1** · [Baixar na página de Releases](../../releases/latest)

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
- **Transporte:** TCP (áudio) e UDP (descoberta). O áudio vai em PCM 16 bits cru ou, opcionalmente, em Opus.
  O protocolo está documentado em [PROTOCOL.md](PROTOCOL.md).

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

1. Execute `PhoneBridgeSetup.exe`. Na instalação você pode marcar: atalho na área de trabalho, **iniciar com o Windows** (fica na bandeja) e **liberar o Firewall e definir as redes conectadas como Privadas**. Se preferir, use só o `PhoneBridgeServer.exe` portátil.
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

Há três jeitos:

1. **Buscar PC:** o app procura o PC na rede sozinho. Também faz isso ao abrir pela primeira vez, quando o campo de IP está vazio. Se achar mais de um PC, mostra uma lista.
2. **QR code:** no PC, clique em **Mostrar QR code para conectar**; no celular, toque em **QR code** e aponte para o quadro. Ele lê e já conecta.
3. **Manual:** digite no celular o IP mostrado na janela do PC.

## Usando

No celular:

- Escolha **Microfone** ou **Alto-falante** e toque em **Iniciar**.
- **Silenciar** corta o áudio sem desconectar; o controle de volume ajusta o ganho do microfone ou o volume do alto-falante.
- Enquanto transmite, há uma notificação com o botão **Parar**. Tocar nela abre o app.
- Se a conexão cair, o app **reconecta sozinho** (mostra "Reconectando…") até você parar.

No PC (janela, ícone da bandeja ou ambos):

- **Iniciar/Parar servidor**, **Silenciar microfone** e **Mostrar QR code** (também no menu da bandeja).
- **Nível do áudio:** barra que mostra o sinal que está passando, útil para saber se o áudio chega.
- **Saída do microfone:** escolha o dispositivo onde o áudio do celular toca (no Windows, `CABLE Input`).
- **Permitir compressão Opus:** libera o Opus quando o celular pedir.
- **Iniciar com o Windows/sistema:** abre escondido na bandeja e já liga o servidor.
- **Instalar VB-Cable**, **Atualizar** (lista de dispositivos) e **Config. de som**.
- Só uma janela fica aberta: abrir de novo traz a existente para a frente. A janela lembra posição e tamanho.

As configurações do PC ficam em `%APPDATA%\PhoneBridge\config.json` (Windows) ou `~/.config/phonebridge/config.json` (Linux).

## Configurações do app

| Opção | O que faz | Padrão |
|---|---|---|
| Porta do servidor | Porta TCP do PC | 5800 |
| Fonte de áudio | Padrão, Microfone, Microfone traseiro, Reconhecimento de voz ou Comunicação por voz | Padrão |
| Compressão do áudio | PCM sem compressão ou **Opus** (usa menos Wi-Fi; experimental) | PCM |
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

**No Windows** o app oferece criar as regras de Firewall (TCP do áudio e UDP da descoberta, limitadas à **sub-rede local**) e mudar para **Privada** toda rede conectada que estiver como **Pública**. Ele pede permissão de administrador, pergunta uma vez por rede nova e lembra se você recusar. Para refazer depois, use o menu da bandeja: **Liberar no Firewall e redes privadas**. Redes de domínio corporativo não são alteradas.

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
pip install -r requirements.txt
python server.py            # --tray inicia escondido na bandeja e liga o servidor
python tests/test_engine.py # testes do servidor com áudio falso (precisam do libopus instalado para os casos de Opus)
```

Dependências: `numpy`, `pyaudiowpatch` (só Windows), `pystray`, `Pillow`, `qrcode` e `opuslib-next`. O Opus precisa da biblioteca nativa: `opus.dll` no Windows e `libopus.so.0` no Linux; sem ela o programa funciona só em PCM. No Linux também é preciso o Tk (`python3-tk`).

### Executáveis e instalador (CI)

O workflow [`build.yml`](.github/workflows/build.yml) compila tudo no GitHub Actions:

- **Android:** APK de depuração.
- **Windows:** roda os testes, gera o `PhoneBridgeServer.exe` com PyInstaller (com o `opus.dll` do vcpkg dentro), abre o `.exe` para um teste de fumaça e monta o `PhoneBridgeSetup.exe` com o Inno Setup (`installer/`).
- **Linux:** roda os testes, gera o binário com PyInstaller (Ubuntu 22.04, para funcionar em distros mais antigas) e faz o teste de fumaça sob Xvfb.

Cada push em `main` compila e testa. Para **lançar uma release**, rode o workflow **Build** manualmente (Actions → Build → Run workflow) informando a versão, por exemplo `v2.0.2`; ele publica os quatro arquivos.

## Estrutura do repositório

```
android/                       app Android (Kotlin)
  app/src/main/java/com/phonebridge/
    MainActivity.kt            tela principal, busca de PC, leitor de QR, pedido de bateria
    AudioService.kt            serviço em primeiro plano: conexão, microfone/alto-falante, Opus, reconexão
    DiscoveryClient.kt         descoberta de PCs por UDP
    PortraitCaptureActivity.kt leitor de QR em pé com moldura quadrada
    SettingsActivity.kt        configurações
  app/src/main/java/org/concentus/   codec Opus em Java (código de terceiros, BSD)
pc/                            servidor do PC (Python)
  server.py                    interface (Tk), bandeja, QR code
  engine.py                    servidor TCP (sem interface)
  protocol.py                  protocolo, Opus e descoberta
  audio_backend.py             áudio: Windows (WASAPI) e Linux (PulseAudio/PipeWire)
  sysutil.py                   firewall, redes privadas, iniciar com o sistema, VB-Cable
  tests/test_engine.py         testes de ponta a ponta com áudio falso
installer/                     script do Inno Setup e do Firewall/redes (Windows)
PROTOCOL.md                    especificação do protocolo
.github/workflows/build.yml    CI e release
```

## Privacidade e segurança

- Não há anúncios, contas, telemetria nem servidores de terceiros; o áudio vai direto do celular para o PC, na sua rede. A única conexão externa é o download opcional do VB-Cable, feito só quando você clica em **Instalar VB-Cable**.
- **Não há criptografia nem senha.** Qualquer aparelho na mesma rede que conheça o IP e a porta pode se conectar. Use só em rede de confiança. No Windows a regra de Firewall limita a porta à sub-rede local.
- Permissões do Android: `INTERNET`, `RECORD_AUDIO` (microfone), `MODIFY_AUDIO_SETTINGS`, `WAKE_LOCK` e `FOREGROUND_SERVICE` (e seus tipos de microfone e mídia) para continuar com a tela apagada, `POST_NOTIFICATIONS` (notificação do serviço), `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` (pedido de isenção de bateria) e a câmera, usada só no leitor de QR (a permissão vem da biblioteca de leitura).
- Mudar redes para **Privada** deixa o PC mais visível nelas: o app só faz isso com a sua confirmação, e você pode recusar e liberar apenas o Firewall manualmente.

## Limitações

- Sem criptografia nem autenticação (veja acima).
- O APK é de **depuração**: o CI gera uma chave de debug nova a cada compilação, então o Android pode recusar a atualização por cima da versão anterior. Nesse caso, **desinstale o PhoneBridge antes de instalar a nova versão** (as configurações do app são perdidas).
- Um cliente por vez em cada modo.
- O Opus só é usado com áudio de 48 kHz; senão volta para PCM.
- O modo microfone no Windows depende do VB-Cable; o app não consegue embutir um driver de áudio próprio.
- Não usa Oboe/AAudio nativo: a baixa latência vem de prioridade de áudio, do modo de baixa latência do `AudioTrack` e do Wi-Fi, e de buffers pequenos.
- O app do celular ainda não foi testado em todos os modelos; a captura com cancelamento de eco/ruído depende do suporte do aparelho.

## Créditos e licenças

- [Concentus](https://github.com/lostromb/concentus): implementação do Opus em Java, incluída em `android/app/src/main/java/org/concentus` (licença BSD, arquivo `LICENSE.txt` ao lado do código).
- [libopus](https://opus-codec.org/) (BSD), empacotada nos executáveis do PC e usada via `opuslib-next`.
- [zxing-android-embedded](https://github.com/journeyapps/zxing-android-embedded) (Apache 2.0), leitor de QR do Android.
- [VB-Cable](https://vb-audio.com/Cable/) do VB-Audio: driver de áudio virtual de terceiros, baixado pelo usuário; não faz parte deste projeto.
- Também usados: Material Components e AndroidX (Android); `pyaudiowpatch`, `pystray`, `Pillow`, `qrcode`, `numpy` (PC).

Este repositório ainda **não tem um arquivo de licença** para o código do PhoneBridge; sem ele, o padrão é "todos os direitos reservados". Se quiser permitir uso e contribuições, adicione um `LICENSE`.

## Histórico de versões

- **2.0.1** — Leitor de QR em pé, com moldura quadrada.
- **2.0.0** — Busca automática do PC e QR code; reconexão automática; Opus opcional; cancelamento de eco e supressão de ruído; serviço com botão Parar e isenção de bateria; PC: medidor de nível, iniciar com o sistema, Firewall e redes privadas automáticos, instalação guiada do VB-Cable, versão para Linux e instalador do Windows; protocolo v2 documentado em `PROTOCOL.md`.
- **1.x** — Modos microfone e alto-falante em PCM; interface escura, bandeja e silenciar microfone no PC; uma única janela aberta; menos atraso (filas curtas, descarte do áudio atrasado, buffers TCP pequenos, Wi-Fi de baixa latência); regra de Firewall pelo app e janela que lembra posição e tamanho.
