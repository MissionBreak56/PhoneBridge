# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versões seguem [SemVer](https://semver.org/lang/pt-BR/).

## 3.1.0 — 2026-11-24

### Novo
- **Pareamento com confirmação no PC (sem QR code):** ao tocar em Iniciar num PC ainda não pareado, o PC mostra uma janela "Um celular quer parear" com um **código de 6 dígitos** que também aparece no celular. Confira que são iguais e clique em **Permitir**. A conexão do pedido usa ECDH P-256 efêmero; os códigos diferentes denunciam alguém no meio da conversa. O QR code continua funcionando.
- O celular que perdeu o pareamento (removido no PC, PC reinstalado, QR expirado) agora **esquece a chave antiga e pede um novo pareamento sozinho**, em vez de só mostrar "o PC recusou este celular".
- Opção no PC (Mais opções) para desligar os pedidos de pareamento.

### Segurança do novo recurso
- Uma pergunta de cada vez, no máximo 3 pedidos por origem e 10 no total por minuto, só rede local, chave pública inválida recusada, a janela fecha sozinha em 60 s ou se o celular desistir, e nada é criado se o usuário recusar.

### Correções
- PC: fechar a janela do QR code **não cancela mais** o pareamento (o celular podia ainda estar pedindo a permissão do microfone e acabava recusado). O código expira em 10 minutos como antes.

### Testes
- 63 testes em Python (8 novos para o pareamento: concordância dos códigos, MITM com códigos diferentes, aprovar/recusar/expirar, desativado, chave inválida, limite, desistência do celular), mais teste de interoperabilidade Kotlin↔Python (mesmo código nos dois lados; a chave recebida conecta) e teste da janela real do PC com um celular simulado.

## 3.0.1 — 2026-11-23

### Correções
- Android: o botão de Configurações (menu ⋮) voltou. Ele tinha sumido na 3.0.0 porque o menu foi removido por engano da tela principal; sem ele não dava para ligar o modo legado nem gerenciar os PCs pareados.

## 3.0.0 — 2026-11-23

Versão principal: o protocolo mudou de forma incompatível com a 2.x (autenticação e criptografia obrigatórias por padrão).

### Segurança
- **Protocolo v3:** pareamento por QR code (chave de 256 bits), autenticação mútua por desafio-resposta (HMAC-SHA256) e tráfego em AES-256-GCM com contador por registro (sem repetição, reordenação ou adulteração).
- Limite de tentativas inválidas com bloqueio crescente por endereço; só aceita origens de rede local; no máximo 8 conexões ainda sem autenticar; limites de tamanho de registros e pacotes; PCM/Opus malformados encerram a sessão.
- Heartbeat nos dois sentidos, expiração de sessão (12 h), um celular por modo, revogação imediata de dispositivos.
- Descoberta UDP: resposta só para rede local, com limite de frequência e sem dados sensíveis (opção para desligar).
- Android: chaves de pareamento cifradas com o Android Keystore; `RECORD_AUDIO` só é pedida no modo microfone.
- Protocolos v1/v2 (sem proteção) **recusados por padrão**; podem ser ligados explicitamente nos dois lados. Não há volta automática para o modo inseguro.
- Atualizador: lista de hashes `SHA256SUMS` assinada com Ed25519 (chave pública embutida) + conferência do SHA-256 antes de qualquer instalação; só `https` e hosts do GitHub; limite de tamanho.
- VB-Cable: instalação automática só com SHA-256 conferido e extração protegida contra Zip Slip (sem hash configurado, abre a página oficial).
- Windows: regras de firewall criadas/removidas só pelo nome exato do PhoneBridge; mudar redes para Privada virou pergunta separada e opcional (desmarcado no instalador); a verificação não presume mais que a regra existe quando não consegue confirmar.

### Estabilidade e áudio
- Android: removido o uso de `InputStream.available()` como medida de atraso; o modo alto-falante usa uma fila (`JitterQueue`) limitada a 60 ms medidos pela duração dos blocos recebidos, com uma thread para ler e outra para tocar.
- Fechamento de conexões acorda as threads bloqueadas (shutdown do socket); sem threads ou sockets sobrando depois de desconectar (testado).
- Medidor de nível com pico e RMS, detecção de clipping e contadores (descartes, erros de decodificação, falhas de autenticação).
- Permissões do Android: código de pedido verificado, microfone só quando necessário, notificação negada não derruba o áudio.

### Novos recursos
- Gravação local em WAV (PC), diagnóstico de conexão (texto para copiar), atualizador pelo GitHub Releases, lista de celulares autorizados no PC e de PCs pareados no celular.
- APK de **release assinado** com chave guardada nos GitHub Secrets; `SHA256SUMS` e `SHA256SUMS.sig` em cada release.

### Mudanças para quem atualiza
- É preciso **parear** de novo (QR code) e atualizar **os dois lados**.
- O APK agora é assinado com a chave de release: desinstale a versão anterior (assinada com chave de depuração) uma vez antes de instalar.

### Testes
- 55 testes em Python (protocolo, autenticação, criptografia, repetição/adulteração, bloqueio, sessões simultâneas, heartbeat, vazamento de threads, atualizador, Zip Slip, gravação), 9 testes unitários em Kotlin com vetores gerados pelo Python, teste de interoperabilidade Kotlin↔Python e teste de áudio com PulseAudio real no Linux.

### Não incluído (avaliado)
- Migração para Jetpack Compose/MVVM, DataStore, histórico de conexões e favoritos; Oboe/AAudio nativo (sem ganho comprovado sobre o `AudioTrack` de baixa latência); gravação em FLAC/OGG/MP3; Bluetooth; reamostragem; TLS (o protocolo usa pareamento por chave compartilhada com AES-GCM). Veja o README, seção Limitações.

## 2.0.1
- Leitor de QR em pé, com moldura quadrada.

## 2.0.0
- Busca automática do PC e QR code; reconexão automática; Opus opcional; cancelamento de eco e supressão de ruído; serviço com botão Parar e isenção de bateria; PC: medidor de nível, iniciar com o sistema, Firewall e redes privadas, instalação guiada do VB-Cable, versão para Linux, instalador do Windows; protocolo v2 documentado.

## 1.x
- Modos microfone e alto-falante em PCM; interface escura, bandeja e silenciar no PC; uma janela só; menos atraso.
