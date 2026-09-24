"""Sinalização via Supabase Realtime — mesmo protocolo do app web (src/lib/signaling.ts)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from realtime import AsyncRealtimeClient, RealtimeSubscribeStates

from . import config

log = logging.getLogger(__name__)

Signal = dict[str, Any]


class Room:
    def __init__(
        self,
        code: str,
        peer_id: str,
        role: str,
        on_signal: Callable[[Signal], Awaitable[None]],
        on_peers: Callable[[list[dict[str, str]]], None] | None = None,
        on_peer_leave: Callable[[str], None] | None = None,
    ) -> None:
        self.code = code
        self.peer_id = peer_id
        self.role = role
        self._on_signal = on_signal
        self._on_peers = on_peers
        self._on_peer_leave = on_peer_leave
        self._client: AsyncRealtimeClient | None = None
        self._channel = None
        self._tasks: set[asyncio.Task] = set()

    async def join(self, timeout: float = 15) -> None:
        if not config.SUPABASE_URL or not config.SUPABASE_ANON_KEY:
            raise RuntimeError("Supabase não configurado (SUPABASE_URL / SUPABASE_ANON_KEY).")

        self._client = AsyncRealtimeClient(f"{config.SUPABASE_URL}/realtime/v1", config.SUPABASE_ANON_KEY)
        channel = self._client.channel(
            f"screen:{self.code}",
            {
                "config": {
                    "broadcast": {"self": False, "ack": False},
                    "presence": {"key": self.peer_id, "enabled": True},
                    "private": False,
                }
            },
        )
        channel.on_broadcast("signal", self._handle_broadcast)
        channel.on_presence_sync(self._handle_sync)
        channel.on_presence_leave(self._handle_leave)
        self._channel = channel

        subscribed: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        def on_status(status: RealtimeSubscribeStates, err: Exception | None) -> None:
            if subscribed.done():
                return
            if status == RealtimeSubscribeStates.SUBSCRIBED:
                subscribed.set_result(None)
            elif status in (RealtimeSubscribeStates.CHANNEL_ERROR, RealtimeSubscribeStates.TIMED_OUT):
                subscribed.set_exception(err or RuntimeError(f"Falha ao entrar na sala ({status.value})"))

        await channel.subscribe(on_status)
        await asyncio.wait_for(subscribed, timeout)
        await channel.track({"role": self.role})

    async def send(self, signal: Signal) -> None:
        if self._channel is not None:
            await self._channel.send_broadcast("signal", signal)

    def send_soon(self, signal: Signal) -> None:
        self._spawn(self.send(signal))

    async def leave(self) -> None:
        client, channel = self._client, self._channel
        self._client = self._channel = None
        try:
            if client and channel:
                await client.remove_channel(channel)
            if client:
                await client.close()
        except Exception:  # noqa: BLE001 - saindo de qualquer jeito
            log.debug("erro ao sair da sala", exc_info=True)

    # --- callbacks do realtime (síncronos) ---

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("erro tratando sinal", exc_info=task.exception())

    def _handle_broadcast(self, message: dict[str, Any]) -> None:
        signal = message.get("payload") or {}
        if "to" in signal and signal["to"] != self.peer_id:
            return
        self._spawn(self._on_signal(signal))

    def _handle_sync(self) -> None:
        if not self._on_peers or self._channel is None:
            return
        state = self._channel.presence_state()
        peers = [
            {"id": key, "role": (metas[0].get("role") if metas else None) or "viewer"}
            for key, metas in state.items()
            if key != self.peer_id
        ]
        self._on_peers(peers)

    def _handle_leave(self, key: str, current: list, left: list) -> None:
        if key != self.peer_id and not current and self._on_peer_leave:
            self._on_peer_leave(key)
