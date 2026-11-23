# Política de segurança

## Versões com suporte

Só a versão mais recente recebe correções de segurança. A 2.x e anteriores **não têm** autenticação nem criptografia e não devem ser usadas em redes que não sejam de confiança.

## Como reportar uma vulnerabilidade

Abra um [aviso de segurança privado](../../security/advisories/new) no GitHub (Security > Report a vulnerability). Se isso não estiver disponível, abra uma issue **sem detalhes de exploração** pedindo um contato privado. Responderemos assim que possível; este é um projeto mantido por uma pessoa.

## Modelo de ameaças

**Protegido (v3):**
- Aparelhos da rede que não foram pareados não conseguem conectar, ouvir o áudio nem usar o microfone/alto-falante do PC (autenticação mútua).
- Quem escuta a rede não consegue decifrar nem alterar o áudio (AES-256-GCM); repetir ou reordenar registros encerra a conexão.
- Tentativas repetidas de adivinhar a chave são bloqueadas; a chave tem 256 bits.
- Conexões de endereços fora da rede local são recusadas; mensagens malformadas ou gigantes encerram a sessão.
- Atualizações só são instaladas se a assinatura Ed25519 do `SHA256SUMS` e o SHA-256 do arquivo conferirem.

**Não protegido:**
- Quem vê o QR code de pareamento enquanto ele está na tela (vale 10 minutos e um aparelho).
- Um PC ou celular já comprometido (as chaves ficam no Android Keystore e em `devices.json`; no Windows este arquivo depende da proteção do perfil do usuário).
- **Sigilo futuro:** a chave de pareamento é de longa duração. Se vazar, remova o dispositivo e pareie de novo.
- A descoberta automática revela na rede local o nome e a porta do PC (pode ser desligada).
- Negação de serviço na rede local (alguém pode encher as conexões ainda sem autenticar).
- O modo legado (v1/v2), quando ligado, não tem proteção alguma.

## Chaves do projeto

- A chave de assinatura do APK (keystore) e a chave de assinatura das atualizações (Ed25519) ficam nos GitHub Secrets do repositório. A chave pública das atualizações está em `pc/updater.py`; o certificado esperado do APK está no workflow. **Mantenha um backup** do keystore: sem ele não é possível publicar atualizações que o Android aceite por cima da versão instalada.
- Para trocar uma chave: gere outra, atualize o segredo e a constante pública correspondente e publique uma versão nova (quem usa o atualizador precisará instalar essa versão manualmente uma vez).
