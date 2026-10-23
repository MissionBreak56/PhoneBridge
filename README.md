# PhoneBridge

Use o celular Android como **microfone** ou **alto-falante** do PC pela rede Wi-Fi.
Projeto novo, escrito do zero (não usa código nem o patch do APK analisado). Sem anúncios, sem telemetria.

## Como funciona

- **Microfone:** celular captura o áudio e envia ao PC, que o toca num dispositivo de saída. Para o Windows enxergar isso como microfone, instale o [VB-Cable](https://vb-audio.com/Cable/) e use "CABLE Output" como microfone nos apps.
- **Alto-falante:** o PC captura o som do sistema (WASAPI loopback) e envia ao celular, que toca.
- Protocolo: TCP porta 5800, PCM 16 bits cru, sem compressão (ótima qualidade, ~1,5 Mbps em estéreo 48 kHz).

## PC (Windows 10/11)

1. Instale Python 3.10+.
2. `cd pc` e `pip install -r requirements.txt`
3. `python server.py`
4. Escolha o dispositivo de saída (para microfone, "CABLE Input") e clique em **Iniciar servidor**.
5. Anote o IP do PC mostrado na janela. Libere o Python no Firewall do Windows (rede privada) quando pedir.

O app detecta o VB-Cable sozinho, seleciona "CABLE Input" e tem botões para baixar o instalador, atualizar a lista de dispositivos e abrir as configurações de som. Após instalar o VB-Cable, reinicie o Windows e clique em "Atualizar dispositivos".

## Android

1. Abra a pasta `android` no Android Studio e deixe sincronizar o Gradle.
2. Gere o APK (Build > Build APK) ou rode direto no celular (depuração USB).
3. No app: digite o IP do PC, escolha o modo e toque em **Conectar**. Aceite a permissão de microfone.

PC e celular precisam estar na mesma rede Wi-Fi (ou use USB tethering).

## Limites desta versão

- Não testado em dispositivos reais (código escrito e revisado, mas não compilado aqui).
- Sem compressão, sem criptografia e sem autenticação: use só em rede de confiança.
- Uma conexão de cada vez por modo; sem descoberta automática (digite o IP).
- Latência típica de 30 a 100 ms em Wi-Fi bom.

## Interface do app Android (v1.1)

Visual em Material 3, tela única centralizada: estado (Parado / Iniciando / Conectado), seletor Microfone/Alto-falante, IP do PC, botão Iniciar/Parar, interruptor Silenciar e controle de volume (ganho do microfone no modo microfone). Menu > Configurações: porta, fonte de áudio, silenciar em chamadas, manter tela ligada, tema e sobre.

## Interface do PC (v1.2)

- Visual escuro minimalista, no mesmo estilo do app Android.
- Ao fechar a janela, o app continua em segundo plano na bandeja do sistema; clique no ícone para reabrir ou use **Sair** no menu da bandeja para encerrar.
- Botão **Silenciar microfone** (também no menu da bandeja): o PC passa a receber silêncio do celular sem derrubar a conexão.
- Porta e dispositivo de saída escolhidos ficam salvos entre execuções.
