# Contribuindo

## Ambiente

- **PC (Python 3.10+):** `cd pc && pip install -r requirements.txt pytest && python -m pytest tests -q`. Testes com Opus precisam da biblioteca `libopus` instalada (`libopus0` no Linux); sem ela são pulados.
- **Android:** JDK 17, Android SDK 34, Gradle 8.7. `cd android && gradle testDebugUnitTest assembleDebug`.
- Linux (áudio real): `pulseaudio-utils`. Interface: `python3-tk`.

## Regras do projeto

- Trabalhe direto na branch `main` com commits pequenos e claros (mensagens em português).
- Todo código novo de protocolo precisa de teste. Mudou o protocolo? Atualize `PROTOCOL.md`, os vetores de teste do Kotlin (`pc/tests/test_security.py::test_print_vectors_for_android` imprime com `PB_PRINT_VECTORS=1`) e a versão do protocolo.
- Não implementar mesa de áudio/mixer nem seleção de dispositivos de entrada ou saída (decisão do projeto).
- Não commitar senhas, keystores nem chaves. O CI lê tudo de GitHub Secrets.
- Não declare como testado o que não foi executado.

## Lançar uma versão

1. Atualize `pc/version.py`, `versionName`/`versionCode` em `android/app/build.gradle.kts`, `installer/phonebridge.iss` e o `CHANGELOG.md` (seção `## X.Y.Z`).
2. Faça push na `main` e espere o CI ficar verde.
3. Rode o workflow **Build** (Actions > Run workflow) informando `vX.Y.Z`. Ele compila, testa, assina o APK, gera `SHA256SUMS` + `SHA256SUMS.sig`, **verifica** e publica.

Segredos necessários (Settings > Secrets and variables > Actions): `ANDROID_KEYSTORE_B64` (keystore em base64), `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`, `UPDATE_SIGNING_KEY` (semente Ed25519 de 32 bytes em hex). Sem eles o APK de release não é gerado e a release não é publicada.

## VB-Cable

O hash do pacote oficial fica em `pc/sysutil.py` (`VBCABLE_SHA256`). Ele foi calculado pelo CI ao baixar de `download.vb-audio.com` (o VB-Audio não publica um hash, então a confiança é na primeira leitura). O job `quality` do CI avisa se o arquivo publicado pelo VB-Audio mudar; atualize a constante depois de conferir o novo pacote.
