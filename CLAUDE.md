# Shared Screen — Desktop (Windows)

App desktop em Python (PySide6 + qasync + aiortc) que apresenta e assiste nas **mesmas salas**
do app web. O app web mora em outro repositório: `weldermarques/shared-screen`
(localmente em `d:\repos\shared-screen`, React + Vite + supabase-js). Código, UI e mensagens
em português do Brasil.

## Comandos

```powershell
.venv\Scripts\python main.py              # roda o app (lê .env.local; update desligado fora do build)
.venv\Scripts\python main.py --selftest   # transmite para si mesmo via Supabase; resultado em %LOCALAPPDATA%\SharedScreen\logs\app.log
.\build.ps1 -Version 1.2.3 [-SkipInstaller]   # PyInstaller + Inno Setup
```

Não há suíte de testes: valide com `--selftest` e scripts avulsos que usam `HostSession`,
`ViewerSession` e `VoiceSession` direto (precisam do Supabase do `.env.local`).
Release = tag `vX.Y.Z` → `.github/workflows/release.yml`. **Update é obrigatório**: o app
se recusa a abrir numa versão antiga.

## Arquitetura

| Arquivo | Papel |
|---|---|
| `app/signaling.py` | `Room`: canal Supabase Realtime (broadcast `signal` + presence com `{role}`) |
| `app/rtc.py` | `HostSession` (1 RTCPeerConnection por espectador, estrela) e `ViewerSession` |
| `app/voice.py` | `VoiceSession`: chat de voz, malha só de áudio entre todos |
| `app/media.py` | Tracks aiortc: `ScreenTrack` (monitor/janela), `PcmTrack` + subclasses de áudio, `AudioPlayer` |
| `app/winaudio.py` | WASAPI process loopback via ctypes (áudio só de um app / tudo menos um app) |
| `app/winwindows.py` | Lista janelas/processos e captura janela com PrintWindow (ctypes) |
| `app/ui/` | Páginas Qt: `home`, `host`, `viewer`, `voice` (widget `VoiceControls`) |
| `app/updater.py` | Checagem de release no GitHub e instalação silenciosa |

## Protocolo compartilhado com o app web (não quebrar)

Qualquer mudança aqui precisa ser espelhada no web (`src/lib/signaling.ts`, `src/lib/voice.ts`),
e versões antigas dos dois lados continuam existindo por um tempo.

- Canal `screen:<código>` — transmissão. Sinais: `join`, `host-ready`, `host-stopped`,
  `offer`/`answer`/`ice` (com `from`/`to`). Presence `role`: `host` | `viewer`.
- Canal `voice:<código>` — voz. Estar no canal = estar na voz (presence `role: voice`).
  Em cada par, **quem tem o id menor (comparação de string do UUID) manda o offer**. Sinais
  `offer`/`answer`/`ice` iguais aos da transmissão.
- aiortc **não faz trickle ICE**: candidatos do Python vão dentro do SDP; os do navegador
  chegam como `ice` e entram por `add_ice`. ICE que chega antes do offer/answer é enfileirado.
- No web, sinais e presence podem chegar **antes** do `joinRoom` resolver — `voice.ts`
  guarda esses sinais e processa depois. Não remover.

## Áudio: decisões que não são óbvias

- "Todo o PC" usa process loopback em modo **EXCLUDE do próprio PID**, para a voz da sala
  (tocada pelo app) não ser retransmitida. PyAudioWPatch (loopback do dispositivo inteiro)
  é só fallback para Windows < build 19041.
- "Só um app" usa modo **INCLUDE da árvore de processos**; por isso `winwindows._root_pid`
  sobe até o processo principal do mesmo executável (o áudio do Chrome/Brave sai de um
  processo filho).
- Process loopback não suporta `GetMixFormat`: o formato é fixo (PCM 48 kHz, 16 bits, estéreo)
  com `AUTOCONVERTPCM`, e exige modo por evento.
- O callback COM de `ActivateAudioInterfaceAsync` precisa responder a `IAgileObject` no
  `QueryInterface`, senão a ativação falha.
- Não há cancelamento de eco na voz do desktop; a UI recomenda fone.
- Cada fonte de voz remota tem seu próprio `AudioPlayer` (um `OutputStream`); o Windows mixa.

## Convenções

- Tudo que é async roda no loop do qasync; callbacks síncronos (Realtime, Qt) usam
  `asyncio.ensure_future`.
- Threads de captura só escrevem em buffers protegidos por lock; o aiortc lê em `recv()`.
- Módulos `win*.py` usam `ctypes.windll` direto — o app é só Windows. Declare `argtypes`
  para funções que recebem HWND/HANDLE (64 bits).
- Mudou dependência nativa? Confira as flags `--collect-*` do `build.ps1`.
