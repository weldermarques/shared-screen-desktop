"""Chat de voz da sala: malha de conexões WebRTC só de áudio entre todos no canal voice:<código>.

Protocolo (igual ao do app web, src/lib/voice.ts):
- Estar no canal (presence) = estar na voz.
- Para cada par, quem tem o id MENOR manda o offer (evita os dois ofertarem ao mesmo tempo).
- Sinais: offer / answer / ice, com from/to, como no canal da transmissão.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Callable

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamError

from .media import AudioPlayer, MicrophoneTrack
from .rtc import _sdp, add_ice, rtc_config
from .signaling import Room, Signal

log = logging.getLogger(__name__)

RECONNECT_DELAY = 2


class VoiceSession:
    def __init__(self, code: str, on_change: Callable[[int, int], None]) -> None:
        """``on_change(conectados, pessoas_na_voz)`` — contagens sem incluir você."""
        self.code = code
        self.my_id = str(uuid.uuid4())
        self._on_change = on_change
        self.mic: MicrophoneTrack | None = None
        self.mic_muted = False
        self.volume = 1.0
        self.muted = False
        self._relay = MediaRelay()
        self._room: Room | None = None
        self._present: set[str] = set()
        self._peers: dict[str, RTCPeerConnection] = {}
        self._players: dict[str, AudioPlayer] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._ice_queue: dict[str, list[dict]] = {}
        self._closed = False

    async def start(self) -> None:
        self.mic = MicrophoneTrack()
        self.mic.muted = self.mic_muted
        try:
            self._room = Room(
                self.code,
                self.my_id,
                "voice",
                on_signal=self._handle_signal,
                on_peers=self._handle_peers,
                on_peer_leave=lambda pid: asyncio.ensure_future(self._close_peer(pid)),
                topic="voice",
            )
            await self._room.join()
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        self._closed = True
        for pid in list(self._peers):
            await self._close_peer(pid)
        if self._room:
            await self._room.leave()
            self._room = None
        if self.mic:
            self.mic.stop()
            self.mic = None

    def set_mic_muted(self, muted: bool) -> None:
        self.mic_muted = muted
        if self.mic:
            self.mic.muted = muted

    def set_volume(self, volume: float, muted: bool) -> None:
        self.volume, self.muted = volume, muted
        for player in self._players.values():
            player.volume, player.muted = volume, muted

    # --- internos ---

    def _emit(self) -> None:
        connected = sum(1 for pc in self._peers.values() if pc.connectionState == "connected")
        self._on_change(connected, len(self._present))

    def _handle_peers(self, peers: list[dict]) -> None:
        self._present = {p["id"] for p in peers}
        for pid in list(self._peers):
            if pid not in self._present:
                asyncio.ensure_future(self._close_peer(pid))
        for pid in self._present:
            if pid > self.my_id and pid not in self._peers:
                self._offer(pid)
        self._emit()

    async def _close_peer(self, pid: str) -> None:
        pc = self._peers.pop(pid, None)
        self._ice_queue.pop(pid, None)
        task = self._tasks.pop(pid, None)
        if task:
            task.cancel()
        player = self._players.pop(pid, None)
        if player:
            player.close()
        if pc:
            await pc.close()
        self._emit()

    def _new_peer(self, pid: str) -> RTCPeerConnection:
        pc = RTCPeerConnection(rtc_config())
        self._peers[pid] = pc
        if self.mic:
            pc.addTrack(self._relay.subscribe(self.mic, buffered=False))

        @pc.on("track")
        def _on_track(track) -> None:
            if track.kind == "audio":
                self._tasks[pid] = asyncio.ensure_future(self._consume(pid, track))

        @pc.on("connectionstatechange")
        async def _on_state() -> None:
            if self._peers.get(pid) is not pc:
                return
            log.info("voz %s: %s", pid[:8], pc.connectionState)
            self._emit()
            if pc.connectionState == "failed":
                await self._close_peer(pid)
                # Quem oferta tenta de novo; o outro lado espera o novo offer.
                await asyncio.sleep(RECONNECT_DELAY)
                if not self._closed and pid in self._present and pid > self.my_id and pid not in self._peers:
                    self._offer(pid)

        return pc

    def _offer(self, pid: str) -> None:
        # Registra a conexão já (síncrono) para um segundo "sync" não ofertar de novo.
        if self._room and not self._closed:
            asyncio.ensure_future(self._send_offer(pid, self._new_peer(pid)))

    async def _send_offer(self, pid: str, pc: RTCPeerConnection) -> None:
        await pc.setLocalDescription(await pc.createOffer())
        if self._peers.get(pid) is pc and self._room:
            await self._room.send({"type": "offer", "from": self.my_id, "to": pid, "sdp": _sdp(pc.localDescription)})

    async def _handle_signal(self, signal: Signal) -> None:
        if self._closed:
            return
        kind = signal.get("type")
        sender = signal.get("from", "")
        if kind == "offer":
            queued = self._ice_queue.pop(sender, [])
            await self._close_peer(sender)
            pc = self._new_peer(sender)
            await pc.setRemoteDescription(RTCSessionDescription(**signal["sdp"]))
            for cand in queued:
                await add_ice(pc, cand)
            await pc.setLocalDescription(await pc.createAnswer())
            if self._peers.get(sender) is pc and self._room:
                await self._room.send(
                    {"type": "answer", "from": self.my_id, "to": sender, "sdp": _sdp(pc.localDescription)}
                )
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

    async def _consume(self, pid: str, track) -> None:
        try:
            player = AudioPlayer()
            player.volume, player.muted = self.volume, self.muted
            self._players[pid] = player
            while True:
                frame = await track.recv()
                player.push(frame)
        except (MediaStreamError, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("falha ao reproduzir a voz de %s", pid[:8])
