# Protocolo PhoneBridge

Versões: **3** (atual, autenticada e criptografada) e **1/2** (legado, sem proteção, desligadas por padrão no PC).
Inteiros em *big endian*. O PC é o servidor; o celular é o cliente.

## 1. Descoberta (UDP, porta 5801)

| Sentido | Conteúdo |
|---|---|
| celular → broadcast | os 7 bytes ASCII `PBFIND1` |
| PC → celular (unicast) | texto UTF-8 `PBHERE1\|<nome do PC>\|<porta TCP>\|<opus>\|<proto>\|<pcid>` |

- `<opus>`: `1` se o PC tem Opus. `<proto>`: versão máxima do protocolo (3). `<pcid>`: identificador público do PC (16 hex; **não é segredo**). PCs antigos mandam só os 4 primeiros campos.
- O nome não contém `|`. O IP do PC é o endereço de origem da resposta.
- Proteções: o PC só responde a origens de rede local, no máximo 5 respostas por segundo por origem, ignora pedidos diferentes de `PBFIND1` e a resposta pode ser desligada nas opções. A descoberta **não dá acesso a nada**: para conectar é preciso estar pareado.

## 2. Pareamento (QR code)

O PC gera para cada celular um `pid` (8 bytes aleatórios, 16 hex) e uma chave de pareamento `psk` de 256 bits e mostra o QR code:

```
phonebridge://<ip>:<porta>?v=3&id=<pcid>&pid=<pid hex>&k=<psk base64url sem padding>&n=<nome do PC (url-encoded)>
```

- O QR é o canal fora de banda: quem não o viu não conhece a chave.
- Vale por **10 minutos** e para **um** celular; fechar a janela do QR sem usar o cancela. Depois da primeira conexão o dispositivo fica ativo (sem expiração) até ser removido no PC.
- No PC os dispositivos ficam em `devices.json` (permissão 0600 no Linux); no celular a chave fica cifrada com o Android Keystore.
- Remover um dispositivo no PC derruba a sessão dele e invalida o pareamento imediatamente.

## 3. Conexão (TCP, porta padrão 5800)

O primeiro dado decide a versão: `PAB3` (v3), `PAB1`/`PAB2` (legado). Qualquer outro início fecha a conexão.

### 3.1 Autenticação v3 (desafio-resposta mútuo)

```
C -> S   "PAB3" | ver(1)=3 | pid(8) | cnonce(16)
S -> C   "PBS3" | ver_max(1) | snonce(16)
C -> S   client_proof(32)
S -> C   server_proof(32)             (se a prova do celular falhar, o PC fecha sem responder)
```

```
auth_key     = HKDF-SHA256(ikm=psk, salt="PBv3", info="PBv3 auth", 32)
client_proof = HMAC-SHA256(auth_key, "PBv3 client" | pid | cnonce | snonce)
server_proof = HMAC-SHA256(auth_key, "PBv3 server" | pid | cnonce | snonce)
okm          = HKDF-SHA256(ikm=psk, salt=cnonce|snonce, info="PBv3 session"|pid, 64)
k_c2s = okm[0:32]   (celular -> PC)       k_s2c = okm[32:64]   (PC -> celular)
```

- O desafio do PC (`snonce`) é novo a cada conexão: repetir uma gravação da autenticação não funciona.
- A prova do PC impede que um impostor na rede finja ser o seu PC.
- Versão diferente de 3: o PC responde `"PBE3" | 1 | ver_max` e fecha. Não existe volta automática para uma versão menos segura.
- Um `pid` desconhecido recebe o mesmo desafio e só falha no fim (não revela quais ids existem).

### 3.2 Registros cifrados (tudo depois da autenticação)

```
registro = tamanho(2) | AES-256-GCM(chave_do_sentido, nonce, tipo(1) | dados, AAD = os 2 bytes do tamanho)
tamanho  = bytes cifrados + tag (16)        nonce = 0x00000000 | contador(8)      (contador começa em 0 em cada sentido)
```

- O contador sobe a cada registro: repetição, reordenação e adulteração falham na verificação da tag e **encerram a conexão**.
- Limites: texto claro até 16 384 bytes (tipo + dados). Tamanho fora de `17..16 400` fecha a conexão.

| Tipo | Valor | Sentido | Dados |
|---|---|---|---|
| REQUEST | 0x10 | C→S | `modo(1) \| máscara de codecs(1) \| tamanho do nome(1) \| nome UTF-8 (≤32)` |
| ACCEPT | 0x11 | S→C | `taxa(4) \| canais(1) \| codec(1) \| id da sessão(8) \| intervalo do heartbeat em ms(2)` |
| REJECT | 0x12 | S→C | `código(1) \| mensagem UTF-8` (1 = ocupado, 2 = sem saída de áudio, 3 = não suportado, 4 = interno) |
| AUDIO | 0x20 | ambos | PCM (múltiplo de 10 ms, até 80 ms) ou um pacote Opus de 20 ms |
| HEARTBEAT | 0x30 | ambos | vazio, a cada 2 s |
| CLOSE | 0x40 | ambos | motivo UTF-8 opcional |

- Modo: `1` = microfone (celular → PC), `2` = alto-falante (PC → celular). Máscara: bit 0 = PCM, bit 1 = Opus.
- Codec: `0` = PCM 16 bits *little endian* intercalado; `1` = Opus a 48 kHz. O PC só escolhe Opus se o celular pediu, o PC permite, a taxa é 48 000 Hz e a biblioteca carregou.
- O PC aceita **um celular por modo**. Se o mesmo celular reconecta, assume o lugar da sessão antiga; outro celular recebe REJECT (ocupado).

### 3.3 Vida da sessão

- **Heartbeat:** os dois lados mandam HEARTBEAT a cada 2 s. Sem receber nada (nem áudio) por 6 s, o lado que espera encerra a conexão. Leituras têm limite de 8 s.
- **Expiração:** sessões duram no máximo 12 h; o PC avisa com CLOSE e o celular reconecta sozinho.
- **Antes de autenticar:** 5 s de limite, no máximo 8 conexões simultâneas nessa fase e só de origens de rede local (privadas, link-local, loopback e CGNAT/Tailscale).
- **Tentativas inválidas:** 5 falhas em 60 s bloqueiam a origem por 60 s, dobrando a cada reincidência (máximo 15 min).
- **Pacotes inválidos:** PCM com tamanho que não é múltiplo de 10 ms, Opus maior que 1 500 bytes ou muitos pacotes Opus corrompidos encerram a sessão.

## 4. Legado (v1 e v2, sem proteção)

Desligado por padrão. Quando o PC não aceita legado, responde `status = 3` (autenticação obrigatória) e fecha.

| Versão | Pedido do celular | Resposta do PC |
|---|---|---|
| 1 | `PAB1` + modo(1) | status(1) + taxa(4) + canais(1) |
| 2 | `PAB2` + modo(1) + máscara(1) | status(1) + taxa(4) + canais(1) + codec(1) |

Depois: PCM cru (blocos de 10 ms) ou, se Opus, pacotes de `tamanho(2) \| dados`. **Sem autenticação e sem criptografia**: qualquer aparelho da rede pode usar a conexão.

## 5. Comportamento para pouco atraso

- `TCP_NODELAY` e buffers TCP de 16 KB nos dois lados.
- PC (microfone): lê sempre e guarda no máximo ~60 ms; o excesso mais antigo é descartado (contado em `dropped_chunks`).
- Celular (alto-falante): uma thread lê e outra toca; a fila guarda no máximo 60 ms **medidos pela duração dos blocos recebidos** (não por bytes disponíveis no socket) e descarta os mais antigos.
- Sem som no sistema o loopback não gera dados; o PC envia blocos de silêncio.

## 6. Segurança: o que protege e o que não protege

Protege: acesso não autorizado (autenticação mútua), escuta (AES-256-GCM), repetição e adulteração (contador + tag), força bruta (limite e bloqueio), conexões de fora da rede local, mensagens malformadas ou gigantes.
Não protege: quem vê o QR code enquanto ele está na tela; um PC ou celular já comprometidos; **sigilo futuro** (a chave de pareamento é de longo prazo: quem a roubar pode decifrar tráfego gravado antes — por isso remover o pareamento e parear de novo troca a chave). A descoberta revela na rede local o nome e a porta do PC.
