# Shared Screen — Desktop (Windows)

App desktop em Python que **apresenta e assiste** nas mesmas salas do app web
([weldermarques/shared-screen](https://github.com/weldermarques/shared-screen))
(um espectador no navegador assiste a quem apresenta pelo desktop e vice-versa).

| Parte | Tecnologia |
|---|---|
| Interface | PySide6 (Qt) + qasync |
| WebRTC | aiortc (VP8 + Opus) |
| Captura de tela | mss (+ ponteiro do mouse desenhado) |
| Áudio do PC | WASAPI loopback (PyAudioWPatch) |
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

## Limitações

- Só Windows (captura de áudio via WASAPI).
- O áudio transmitido é o do PC inteiro; use "Mutar áudio" para cortar na hora.
- O vídeo é codificado em software, uma vez por espectador: bom para poucos espectadores
  (~3–5). A resolução é limitada a 1080p a 20 fps.
- O instalador não é assinado digitalmente, então o Windows SmartScreen mostra um aviso
  na primeira instalação ("Mais informações → Executar assim mesmo").
