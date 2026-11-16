# Protocolo PhoneBridge

Versão do protocolo: **2** (a versão 1 continua aceita). Todos os números inteiros são *big endian*.
O PC é o servidor; o celular é o cliente.

## 1. Descoberta (UDP, porta 5801)

| Sentido | Conteúdo |
|---|---|
| celular → broadcast | os 7 bytes ASCII `PBFIND1` |
| PC → celular (unicast, para quem perguntou) | texto UTF-8 `PBHERE1\|<nome do PC>\|<porta TCP>\|<opus>` |

`<opus>` é `1` se o PC tem a biblioteca Opus, senão `0`. O nome não contém `|`. O IP do PC é o endereço de origem da resposta.

## 2. QR code

Texto: `phonebridge://<ip>:<porta>` (ex.: `phonebridge://192.168.0.10:5800`).

## 3. Conexão de áudio (TCP, porta padrão 5800)

### Pedido do celular

| Versão | Bytes |
|---|---|
| 1 | `PAB1` + modo (1 byte) |
| 2 | `PAB2` + modo (1 byte) + máscara de codecs (1 byte) |

- Modo: `1` = microfone (celular → PC), `2` = alto-falante (PC → celular).
- Máscara de codecs: bit 0 (`1`) = PCM, bit 1 (`2`) = Opus.

Qualquer outro início fecha a conexão.

### Resposta do PC

| Versão | Bytes |
|---|---|
| 1 | status (1) + taxa (4) + canais (1) |
| 2 | status (1) + taxa (4) + canais (1) + codec (1) |

- Status `0` = ok; qualquer outro valor = recusado (ex.: sem dispositivo de saída) e o PC fecha.
- Taxa em Hz; canais = canais do áudio que vai trafegar (no modo microfone o celular sempre envia mono).
- Codec: `0` = PCM, `1` = Opus. Na versão 1 é sempre PCM.

### Escolha do codec

O PC responde Opus somente se **todas** as condições valerem: o celular usou a versão 2 e pediu Opus, o PC permite Opus, a taxa é 48000 Hz e a biblioteca Opus carregou. Caso contrário responde PCM.

## 4. Fluxo de áudio

### PCM

PCM 16 bits com sinal, *little endian*, intercalado, sem enquadramento. A unidade de 10 ms tem `taxa/100 × canais × 2` bytes.

### Opus

Cada pacote é: tamanho (2 bytes, *big endian*) + dados Opus. Cada pacote tem 20 ms a 48 kHz (960 amostras por canal). Microfone: mono, VoIP, ~32 kbps. Alto-falante: 1 ou 2 canais, modo áudio, ~64 kbps (mono) ou ~128 kbps (estéreo).

## 4.1 Comportamento para pouco atraso

- Os dois lados usam buffers TCP pequenos (16 KB) e `TCP_NODELAY`.
- O PC (microfone) guarda no máximo ~60 ms e descarta o excesso, ficando com ~20 ms.
- O celular (alto-falante) descarta o atraso acumulado acima de ~60 ms (PCM) ou de ~2 KB pendentes (Opus).
- Sem som no sistema, o loopback não gera dados; o PC envia blocos de silêncio.

## 5. Falhas e reconexão

O celular reconecta sozinho (se ativado) com espera crescente de 1 a 5 s. No modo alto-falante, 5 s sem receber dados derrubam a conexão; no modo microfone, uma escrita presa por mais de 4 s derruba a conexão. Um PC que só fala a versão 1 fecha a conexão ao ver `PAB2`; o celular então passa a usar a versão 1 (PCM).

## 6. Segurança

Não há criptografia nem autenticação. Use só em rede confiável; no Windows a regra de firewall limita a porta à sub-rede local.
