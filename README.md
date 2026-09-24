# Shared Screen — Desktop (Windows)

App desktop em Python que **apresenta e assiste** nas mesmas salas do app web
([weldermarques/shared-screen](https://github.com/weldermarques/shared-screen))
(um espectador no navegador assiste a quem apresenta pelo desktop e vice-versa).

| Parte | Tecnologia |
|---|---|
| Interface | PySide6 (Qt) + qasync |
| WebRTC | aiortc (VP8 + Opus) |
| Captura de tela | mss (monitor) ou PrintWindow (janela), + ponteiro do mouse desenhado |
| Áudio transmitido | WASAPI *process loopback* (PC inteiro menos o app, ou só um aplicativo); PyAudioWPatch como fallback |
| Voz da sala | microfone via sounddevice, malha WebRTC no canal `voice:<código>` |
| Reprodução de áudio | sounddevice |
| Sinalização | Supabase Realtime (`realtime`), mesmo protocolo do `src/lib/signaling.ts` do app web |
| Instalador | PyInstaller + Inno Setup (instala por usuário, sem admin) |
| Atualização | GitHub Releases, **obrigatória** |

## Download

**[Baixar o instalador (versão mais recente)](https://github.com/weldermarques/shared-screen-desktop/releases/latest/download/SharedScreen-Setup.exe)**

## Rodando do código-fonte

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python main.py
```

Copie `.env.example` para `.env.local` e preencha `SUPABASE_URL` / `SUPABASE_ANON_KEY`
(os mesmos do app web). Também aceita variáveis de ambiente. Rodando do código-fonte, a checagem de update fica desligada.

Autoteste (transmite para si mesmo e confere vídeo/áudio): `python main.py --selftest`
— o resultado vai para `%LOCALAPPDATA%\SharedScreen\logs\app.log`.

## Gerando o instalador localmente

Requer o Inno Setup 6 (`winget install JRSoftware.InnoSetup`).

```powershell
.\build.ps1 -Version 1.0.0          # dist-installer\SharedScreen-Setup-1.0.0.exe
.\build.ps1 -Version 1.0.0 -SkipInstaller   # só o executável em dist\SharedScreen
```

## Publicando uma versão (update obrigatório)

1. No GitHub: **Settings → Secrets and variables → Actions**, crie os secrets
   `SUPABASE_URL` e `SUPABASE_ANON_KEY` (opcionais: `TURN_URL`, `TURN_USERNAME`, `TURN_CREDENTIAL`).
2. Crie e envie uma tag: `git tag v1.0.0 && git push origin v1.0.0`.
3. O workflow `.github/workflows/release.yml` gera o instalador e publica a release.

### Como o update obrigatório funciona

- Ao abrir, o app consulta `api.github.com/repos/<repo>/releases/latest`.
- Se a tag da release for maior que a versão instalada, o app **não abre**: mostra a tela de
  atualização, baixa o `SharedScreen-Setup-X.Y.Z.exe`, roda em modo silencioso e se fecha;
  o instalador substitui os arquivos e reabre o app já atualizado.
- Com o app aberto, a checagem se repete a cada 30 min; se sair versão nova no meio do uso,
  a transmissão é encerrada e a atualização é aplicada.
- Sem internet ou com a API do GitHub fora do ar, o app abre normalmente e tenta de novo depois.
- O repositório precisa ser **público** (a API e o download são feitos sem autenticação).

## Sala única

O app não tem tela inicial: ao abrir, você já está na sala (código em `ROOM_CODE`, padrão
`SALA`), que existe sempre, sem dono. Ao entrar, você assiste a quem estiver compartilhando e
entra na voz com o microfone **mutado**. Qualquer um pode clicar em **Compartilhar minha tela**;
só uma pessoa compartilha por vez — quem começa por último assume, e a transmissão anterior
para sozinha. O 📎 ao lado do nome da sala copia o link para assistir pelo navegador.

## Voz da sala

Qualquer pessoa na sala (apresentador ou espectador, no app ou no navegador) pode clicar em
**Entrar na voz**. A voz é independente da transmissão: dá para conversar antes de começar a
compartilhar. Cada participante se conecta direto com os outros (malha), então funciona bem
para grupos pequenos (~2–6 pessoas).

## Áudio só de um aplicativo

Em **Áudio**, escolha "Só chrome.exe" (ou outro app) para transmitir apenas o som daquele
programa — o equivalente no desktop a "compartilhar o áudio da aba" do navegador (um app
desktop não consegue capturar uma aba isolada; captura o navegador inteiro). Ao escolher uma
janela em **O que compartilhar**, o áudio daquele aplicativo é selecionado automaticamente.
"Todo o PC" envia tudo **menos** o som do próprio Shared Screen, para a voz da sala não voltar
como eco na transmissão.

## Limitações

- Só Windows. Áudio por aplicativo exige Windows 10 2004 (build 19041) ou mais novo; em
  versões antigas só existe "Todo o PC" (e aí a voz da sala vai junto na transmissão).
- O app desktop não tem cancelamento de eco na voz: use fone de ouvido. (No navegador o
  cancelamento de eco é do próprio navegador.)
- A voz usa o microfone padrão do Windows. Se "Permitir que apps da área de trabalho acessem
  o microfone" estiver desligado nas configurações de privacidade, o microfone fica mudo.
- Captura de janela usa PrintWindow: não funciona com a janela minimizada (fica o último
  quadro) e vídeos com DRM (Netflix etc.) aparecem pretos.
- O vídeo é codificado em software, uma vez por espectador: bom para poucos espectadores
  (~3–5). A resolução é limitada a 1080p a 20 fps.
- O instalador não é assinado digitalmente, então o Windows SmartScreen mostra um aviso
  na primeira instalação ("Mais informações → Executar assim mesmo").
