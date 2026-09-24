"""Sessões WebRTC (aiortc) de apresentador e espectador.

Protocolo idêntico ao app web: join / host-ready / host-stopped / offer / answer / ice.
O aiortc não faz trickle ICE: os candidatos locais já vão dentro do SDP; os candidatos
remotos recebidos do navegador são adicionados com addIceCandidate.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from typing import Callable

import aiortc.codecs.h264 as _h264
import aiortc.codecs.vpx as _vpx
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import candidate_from_sdp

from . import config
from .media import AudioPlayer, PcmTrack, ScreenTrack, open_audio_source
from .signaling import Room, Signal

log = logging.getLogger(__name__)

# Os padrões do aiortc (500 kbps–1.5 Mbps) deixam texto borrado em compartilhamento de tela.
_vpx.DEFAULT_BITRATE, _vpx.MIN_BITRATE, _vpx.MAX_BITRATE = 2_500_000, 500_000, 6_000_000
_h264.DEFAULT_BITRATE, _h264.MIN_BITRATE, _h264.MAX_BITRATE = 2_500_000, 500_000, 6_000_000

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def random_code(length: int = 6) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def rtc_config() -> RTCConfiguration:
    servers = [RTCIceServer(urls=["stun:stun.l.google.com:19302"])]
    if config.TURN_URL:
        servers.append(
            RTCIceServer(
                urls=[u.strip() for u in config.TURN_URL.split(",")],
                username=config.TURN_USERNAME or None,
                credential=config.TURN_CREDENTIAL or None,
            )
        )
    return RTCConfiguration(iceServers=servers)


async def add_ice(pc: RTCPeerConnection, init: dict) -> None:
    raw = (init or {}).get("candidate") or ""
    if not raw:
        return  # fim dos candidatos
    try:
        candidate = candidate_from_sdp(raw.split(":", 1)[1] if raw.startswith("candidate:") else raw)
        candidate.sdpMid = init.get("sdpMid")
        candidate.sdpMLineIndex = init.get("sdpMLineIndex")
        await pc.addIceCandidate(candidate)
    except Exception:  # noqa: BLE001 - candidato inválido/antigo
        log.debug("candidato ICE ignorado: %s", raw, exc_info=True)


async def close_pc(pc: RTCPeerConnection, timeout: float = 2) -> None:
    """Fecha a conexão sem travar a interface.

    No loop do qasync (Windows), o fechamento do socket UDP do aioice às vezes nunca
    avisa que terminou e ``pc.close()`` fica preso para sempre. Depois do limite,
    seguimos em frente e o fechamento continua em segundo plano.
    """
    try:
        await asyncio.wait_for(asyncio.shield(pc.close()), timeout)
    except asyncio.TimeoutError:
        log.warning("fechamento da conexão WebRTC demorou; seguindo em frente")
    except Exception:  # noqa: BLE001
        log.debug("erro ao fechar conexão WebRTC", exc_info=True)


def _sdp(desc: RTCSessionDescription) -> dict:
    return {"type": desc.type, "sdp": desc.sdp}


# ================================================================= apresentador


class HostSession:
    def __init__(
        self,
        code: str,
        video_source: tuple[str, int],
        audio_source: tuple[str, int],
        on_stats: Callable[[int, int], None],
        on_replaced: Callable[[], None] | None = None,
    ) -> None:
        """``video_source``: ("monitor", índice) | ("window", hwnd).
        ``audio_source``: ("none", 0) | ("system", 0) | ("app", pid).
        ``on_replaced``: outra pessoa começou a compartilhar na sala (só um por vez)."""
        self.code = code
        self.host_id = str(uuid.uuid4())
        self.started_at = 0.0
        self._on_replaced = on_replaced
        self.video_source = video_source
        self.audio_source = audio_source
        self._on_stats = on_stats
        self._viewers = 0
        self.video: ScreenTrack | None = None
        self.audio: PcmTrack | None = None
        self._relay = MediaRelay()
        self._peers: dict[str, RTCPeerConnection] = {}
        self._ice_queue: dict[str, list[dict]] = {}
        self._room: Room | None = None

    async def start(self) -> None:
        self.video = ScreenTrack(self.video_source)
        try:
            self.audio = open_audio_source(self.audio_source)
        except Exception:
            log.exception("não foi possível capturar o áudio %s", self.audio_source)
            self.audio = None
        try:
            self._room = Room(
                self.code,
                self.host_id,
                "host",
                on_signal=self._handle_signal,
                on_peers=self._handle_peers,
                on_peer_leave=lambda vid: asyncio.ensure_future(self._close_peer(vid)),
            )
            await self._room.join()
            self.started_at = time.time()
            # "at" desempata quando duas pessoas começam juntas (o app web não manda: conta como mais novo).
            await self._room.send({"type": "host-ready", "from": self.host_id, "at": self.started_at})
        except Exception:
            await self.stop(notify=False)
            raise

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    def set_audio_muted(self, muted: bool) -> None:
        if self.audio:
            self.audio.muted = muted

    def set_video_source(self, source: tuple[str, int]) -> None:
        self.video_source = source
        if self.video:
            self.video.set_source(source)

    async def stop(self, notify: bool = True) -> None:
        if self._room and notify:
            try:
                await self._room.send({"type": "host-stopped", "from": self.host_id})
            except Exception:  # noqa: BLE001
                pass
        for vid in list(self._peers):
            await self._close_peer(vid)
        if self._room:
            await self._room.leave()
            self._room = None
        for track in (self.video, self.audio):
            if track:
                track.stop()
        self.video = self.audio = None

    # --- internos ---

    def _emit_stats(self) -> None:
        connected = sum(1 for pc in self._peers.values() if pc.connectionState == "connected")
        self._on_stats(self._viewers, connected)

    def _handle_peers(self, peers: list[dict]) -> None:
        self._viewers = sum(1 for p in peers if p["role"] == "viewer")
        self._emit_stats()

    async def _close_peer(self, viewer_id: str) -> None:
        pc = self._peers.pop(viewer_id, None)
        self._ice_queue.pop(viewer_id, None)
        if pc:
            await close_pc(pc)
        self._emit_stats()

    async def _connect_viewer(self, viewer_id: str) -> None:
        if not self._room or not self.video:
            return
        await self._close_peer(viewer_id)
        pc = RTCPeerConnection(rtc_config())
        self._peers[viewer_id] = pc

        pc.addTrack(self._relay.subscribe(self.video, buffered=False))
        if self.audio:
            pc.addTrack(self._relay.subscribe(self.audio, buffered=False))

        @pc.on("connectionstatechange")
        async def _on_state() -> None:
            log.info("espectador %s: %s", viewer_id[:8], pc.connectionState)
            if pc.connectionState in ("failed", "closed") and self._peers.get(viewer_id) is pc:
                await self._close_peer(viewer_id)
            self._emit_stats()

        await pc.setLocalDescription(await pc.createOffer())
        await self._room.send(
            {"type": "offer", "from": self.host_id, "to": viewer_id, "sdp": _sdp(pc.localDescription)}
        )

    async def _handle_signal(self, signal: Signal) -> None:
        kind = signal.get("type")
        sender = signal.get("from", "")
        if kind == "join":
            await self._connect_viewer(sender)
        elif kind == "host-ready" and sender != self.host_id:
            if self._on_replaced and signal.get("at", float("inf")) >= self.started_at:
                self._on_replaced()
        elif kind == "answer":
            pc = self._peers.get(sender)
            if not pc:
                return
            await pc.setRemoteDescription(RTCSessionDescription(**signal["sdp"]))
            for cand in self._ice_queue.pop(sender, []):
                await add_ice(pc, cand)
        elif kind == "ice":
            pc = self._peers.get(sender)
            if pc and pc.remoteDescription:
                await add_ice(pc, signal.get("candidate"))
            else:
                self._ice_queue.setdefault(sender, []).append(signal.get("candidate"))


# ================================================================= espectador


class ViewerSession:
    """Status: connecting | waiting | negotiating | watching | ended | error"""

    def __init__(
        self,
        code: str,
        on_status: Callable[[str], None],
        on_frame: Callable[[object], None],
        on_audio: Callable[[bool], None],
    ) -> None:
        self.code = code
        self.my_id = str(uuid.uuid4())
        self._on_status = on_status
        self._on_frame = on_frame
        self._on_audio = on_audio
        self.status = "connecting"
        self.player: AudioPlayer | None = None
        self.volume = 1.0
        self.muted = False
        self._room: Room | None = None
        self._pc: RTCPeerConnection | None = None
        self._host_id: str | None = None
        # Quem anunciou por último que está compartilhando. Sinais de um apresentador
        # anterior (que acabou de perder a vez) são ignorados.
        self._latest_host: str | None = None
        self._host_online: bool | None = None  # pela presence; None = ainda não sabemos
        self._ice_queue: list[dict] = []
        self._tasks: list[asyncio.Task] = []
        self._closed = False

    def _set_status(self, status: str) -> None:
        self.status = status
        self._on_status(status)

    async def start(self) -> None:
        self._set_status("connecting")
        self._room = Room(
            self.code,
            self.my_id,
            "viewer",
            on_signal=self._handle_signal,
            on_peers=self._handle_peers,
            on_peer_leave=self._handle_leave,
        )
        await self._room.join()
        # A presence pode ter chegado durante o join: sem ninguém compartilhando, fica esperando.
        self._set_status("waiting" if self._host_online is False else "negotiating")
        await self._request_stream()

    async def close(self) -> None:
        self._closed = True
        await self._reset_peer()
        if self._room:
            await self._room.leave()
            self._room = None

    def set_volume(self, volume: float, muted: bool) -> None:
        self.volume, self.muted = volume, muted
        if self.player:
            self.player.volume, self.player.muted = volume, muted

    # --- internos ---

    async def _request_stream(self) -> None:
        if self._room:
            await self._room.send({"type": "join", "from": self.my_id})

    async def _reset_peer(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        pc, self._pc = self._pc, None
        self._host_id = None
        if pc:
            await close_pc(pc)
        if self.player:
            self.player.close()
            self.player = None
        self._on_audio(False)

    def _handle_peers(self, peers: list[dict]) -> None:
        host_online = any(p["role"] == "host" for p in peers)
        self._host_online = host_online
        if not host_online and self.status in ("connecting", "negotiating"):
            self._set_status("waiting")

    def _handle_leave(self, peer_id: str) -> None:
        if peer_id == self._host_id or (self._host_id is None and peer_id == self._latest_host):
            asyncio.ensure_future(self._reset_peer())
            self._set_status("ended")

    async def _handle_signal(self, signal: Signal) -> None:
        if self._closed:
            return
        kind = signal.get("type")
        sender = signal.get("from")
        if kind in ("host-stopped", "offer") and self._latest_host and sender != self._latest_host:
            return
        if kind == "host-ready":
            self._latest_host = sender
            await self._reset_peer()
            self._ice_queue.clear()
            self._set_status("negotiating")
            await self._request_stream()
        elif kind == "host-stopped":
            await self._reset_peer()
            self._ice_queue.clear()
            self._set_status("ended")
        elif kind == "offer":
            self._latest_host = sender
            await self._handle_offer(signal)
        elif kind == "ice":
            pc = self._pc
            if pc and pc.remoteDescription and self._host_id == signal.get("from"):
                await add_ice(pc, signal.get("candidate"))
            else:
                self._ice_queue.append(signal.get("candidate"))

    async def _handle_offer(self, signal: Signal) -> None:
        await self._reset_peer()
        self._set_status("negotiating")
        host_id = signal["from"]
        pc = RTCPeerConnection(rtc_config())
        self._pc = pc
        self._host_id = host_id

        @pc.on("track")
        def _on_track(track) -> None:
            if track.kind == "video":
                self._tasks.append(asyncio.ensure_future(self._consume_video(track)))
            elif track.kind == "audio":
                self._tasks.append(asyncio.ensure_future(self._consume_audio(track)))

        @pc.on("connectionstatechange")
        async def _on_state() -> None:
            if self._pc is not pc:
                return
            log.info("conexão: %s", pc.connectionState)
            if pc.connectionState == "connected":
                self._set_status("watching")
            elif pc.connectionState == "failed":
                await self._reset_peer()
                self._set_status("negotiating")
                await asyncio.sleep(1)
                await self._request_stream()

        await pc.setRemoteDescription(RTCSessionDescription(**signal["sdp"]))
        queued, self._ice_queue = self._ice_queue, []
        for cand in queued:
            await add_ice(pc, cand)
        await pc.setLocalDescription(await pc.createAnswer())
        if self._pc is pc and self._room:
            await self._room.send(
                {"type": "answer", "from": self.my_id, "to": host_id, "sdp": _sdp(pc.localDescription)}
            )

    async def _consume_video(self, track) -> None:
        try:
            while True:
                frame = await track.recv()
                self._on_frame(frame.to_ndarray(format="rgb24"))
        except (MediaStreamError, asyncio.CancelledError):
            pass

    async def _consume_audio(self, track) -> None:
        try:
            self.player = AudioPlayer()
            self.player.volume, self.player.muted = self.volume, self.muted
            self._on_audio(True)
            while True:
                frame = await track.recv()
                if self.player:
                    self.player.push(frame)
        except (MediaStreamError, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("falha ao reproduzir áudio")
