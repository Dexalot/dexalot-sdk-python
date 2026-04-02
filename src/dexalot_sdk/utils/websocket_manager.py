import asyncio
import json
import logging
import time
from collections.abc import Callable
from enum import Enum
from typing import Any

import websockets
import websockets.exceptions

from ..utils.observability import get_logger


class ConnectionState(Enum):
    """WebSocket connection states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class WebSocketManager:
    """
    Manages a persistent WebSocket connection with support for multiple topic subscriptions,
    automatic reconnection, and heartbeat/ping-pong.

    Uses the ``websockets`` async library; all I/O runs on the asyncio event loop.
    No threading is used.  ``connect()`` and ``subscribe()``/``unsubscribe()`` are
    synchronous entry points that schedule work on the running event loop via
    ``loop.create_task()``.  ``disconnect()`` is ``async def`` and can be awaited to
    cleanly cancel the background task.
    """

    def __init__(
        self,
        ws_url: str,
        account: Any | None,
        config: Any,
        logger: logging.Logger | None = None,
    ):
        self.ws_url = ws_url
        self.account = account
        self.config = config
        self.logger = logger or get_logger(__name__)

        # Store the running event loop at construction time.  The SDK always
        # instantiates WebSocketManager from async contexts (DexalotBaseClient
        # methods are all async), so the loop is always available here.
        self._loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()

        # Connection state
        self._state = ConnectionState.DISCONNECTED
        self._ws: websockets.ClientConnection | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._should_reconnect = False

        # Subscriptions: key -> (callback, is_private, meta)
        # meta is None (topic-list subscription) or {"kind": "orderbook", "pair", "decimal"}
        self._subscriptions: dict[str, tuple[Callable, bool, dict[str, Any] | None]] = {}

        # Reconnection state
        self._reconnect_attempts = 0
        self._reconnect_delay = config.ws_reconnect_initial_delay

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._state == ConnectionState.CONNECTED

    # ------------------------------------------------------------------
    # Sync public API  (schedule work on the event loop)
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """
        Start the WebSocket background task.

        This is a synchronous entry point that schedules the async ``_run``
        coroutine on the event loop.  Raises ``RuntimeError`` if the WebSocket
        manager is disabled in config.  Idempotent when already connecting/connected.
        """
        if not self.config.ws_manager_enabled:
            raise RuntimeError(
                "WebSocket Manager is disabled. Set ws_manager_enabled=True in config."
            )
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            self.logger.debug("WebSocket already connecting or connected")
            return

        self._state = ConnectionState.CONNECTING
        self._should_reconnect = True
        self._run_task = self._loop.create_task(self._run())

    def subscribe(
        self,
        subscription_key: str,
        callback: Callable,
        is_private: bool = False,
        *,
        orderbook_pair: str | None = None,
        orderbook_decimal: int | None = None,
    ) -> None:
        """
        Register a subscription callback.

        Synchronous.  Stores the subscription locally and, if already connected,
        schedules the wire subscribe message on the event loop.  Auto-connects if
        not yet connected.
        """
        if not self.config.ws_manager_enabled:
            raise RuntimeError(
                "WebSocket Manager is disabled. Set ws_manager_enabled=True in config."
            )

        meta = _build_meta(subscription_key, is_private, orderbook_pair, orderbook_decimal)
        self._subscriptions[subscription_key] = (callback, is_private, meta)

        if self.is_connected and self._ws:
            self._loop.create_task(self._send_subscribe(subscription_key))
        elif not self.is_connected:
            self.connect()

    def unsubscribe(self, subscription_key: str) -> None:
        """Remove a subscription and send the wire unsubscribe message if connected."""
        spec = self._subscriptions.pop(subscription_key, None)
        if spec is None:
            return
        self.logger.info(f"Unsubscribed from: {subscription_key}")
        if self.is_connected and self._ws:
            self._loop.create_task(self._send_unsubscribe(subscription_key, spec))

    # ------------------------------------------------------------------
    # Async public API
    # ------------------------------------------------------------------

    async def disconnect(self) -> None:
        """
        Close the WebSocket connection and cancel the background task.

        Safe to call even if not connected.  After this returns the manager is
        in ``DISCONNECTED`` state and all subscriptions are cleared.
        """
        self._should_reconnect = False

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            try:
                await self._run_task
            except (asyncio.CancelledError, Exception):
                pass

        self._state = ConnectionState.DISCONNECTED
        self._subscriptions.clear()
        self.logger.info("WebSocket disconnected and cleaned up")

    # ------------------------------------------------------------------
    # Background coroutine
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """
        Background coroutine: connect → re-subscribe → message loop → reconnect.

        Loops while ``_should_reconnect`` is True, applying exponential backoff
        between attempts.  Exits cleanly on ``asyncio.CancelledError``.
        """
        while self._should_reconnect:
            cancelled = False
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=self.config.ws_ping_interval,
                    ping_timeout=self.config.ws_ping_timeout,
                ) as ws:
                    self._ws = ws
                    self._state = ConnectionState.CONNECTED
                    self._reconnect_attempts = 0
                    self._reconnect_delay = self.config.ws_reconnect_initial_delay
                    self.logger.info("WebSocket connection opened")

                    # Re-subscribe all registered topics (handles reconnect scenario)
                    for key in list(self._subscriptions):
                        await self._send_subscribe(key)

                    async for raw in ws:
                        if isinstance(raw, bytes):
                            raw = raw.decode()
                        self._handle_message(raw)

            except asyncio.CancelledError:
                cancelled = True
            except Exception as e:
                self.logger.error(f"WebSocket error: {e}")
            finally:
                self._ws = None

            if cancelled or not self._should_reconnect:
                self._state = ConnectionState.DISCONNECTED
                break
            self._state = ConnectionState.RECONNECTING
            if not await self._backoff():
                break

    async def _backoff(self) -> bool:
        """
        Sleep for the current reconnect delay, then update state for next attempt.

        Returns False when max reconnect attempts is reached (caller should stop),
        True otherwise.
        """
        max_attempts = self.config.ws_reconnect_max_attempts
        if max_attempts > 0 and self._reconnect_attempts >= max_attempts:
            self.logger.error(f"Max reconnection attempts ({max_attempts}) reached")
            self._should_reconnect = False
            self._state = ConnectionState.DISCONNECTED
            return False

        self._reconnect_attempts += 1
        delay = min(self._reconnect_delay, self.config.ws_reconnect_max_delay)
        self.logger.debug(f"Scheduling reconnect in {delay}s (attempt {self._reconnect_attempts})")
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return False
        self._reconnect_delay *= self.config.ws_reconnect_exponential_base
        return True

    # ------------------------------------------------------------------
    # Wire protocol helpers
    # ------------------------------------------------------------------

    async def _send_subscribe(self, subscription_key: str) -> None:
        """Build and send the subscribe wire message for a subscription key."""
        if self._ws is None:
            return
        spec = self._subscriptions.get(subscription_key)
        if not spec:
            return
        _callback, is_private, meta = spec

        payload = _build_subscribe_payload(
            subscription_key, is_private, meta, self.account, self.config
        )
        if payload is None:
            return

        try:
            await self._ws.send(json.dumps(payload))
            self.logger.info(f"Subscribed: {subscription_key}")
        except websockets.exceptions.ConnectionClosed:
            pass  # Connection closed during teardown — expected
        except Exception as e:
            self.logger.error(f"Failed to subscribe {subscription_key}: {e}")

    async def _send_unsubscribe(
        self, subscription_key: str, spec: tuple[Callable, bool, dict[str, Any] | None]
    ) -> None:
        """Build and send the unsubscribe wire message."""
        if self._ws is None:
            return
        _callback, _is_private, meta = spec
        if meta and meta.get("kind") == "orderbook":
            pair = meta["pair"]
            payload: dict[str, Any] = {
                "type": "unsubscribe",
                "data": pair,
                "pair": pair,
                "decimal": meta["decimal"],
            }
            addr = getattr(self.account, "address", None) if self.account else None
            if addr:
                payload["traderaddress"] = addr
        else:
            payload = {"type": "unsubscribe", "topics": [subscription_key]}

        try:
            await self._ws.send(json.dumps(payload))
        except websockets.exceptions.ConnectionClosed:
            pass  # Connection closed during teardown — expected
        except Exception as e:
            self.logger.error(f"Failed to unsubscribe {subscription_key}: {e}")

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    def _handle_message(self, raw: str) -> None:
        """Parse and route an incoming WebSocket message to registered callbacks."""
        try:
            data = json.loads(raw)
            self.logger.debug(f"WebSocket message received: {data}")

            if not isinstance(data, dict):
                return

            # Dexalot order book stream: type "orderBooks", pair "BASE/QUOTE"
            if data.get("type") == "orderBooks" and data.get("pair"):
                pair = data["pair"]
                for _sub_key, (callback, _priv, meta) in list(self._subscriptions.items()):
                    if meta and meta.get("kind") == "orderbook" and meta.get("pair") == pair:
                        try:
                            callback(data)
                        except Exception as e:
                            self.logger.error(f"Error in orderbook callback for {pair}: {e}")
                return

            topic = data.get("topic")
            if topic:
                if topic in self._subscriptions:
                    callback, _, _meta = self._subscriptions[topic]
                    try:
                        callback(data)
                    except Exception as e:
                        self.logger.error(f"Error in callback for topic {topic}: {e}")
            else:
                for callback, _, _meta in list(self._subscriptions.values()):
                    if _meta and _meta.get("kind") == "orderbook":
                        continue
                    try:
                        callback(data)
                    except Exception as e:
                        self.logger.error(f"Error in callback: {e}")

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse WebSocket message: {e}")
        except Exception as e:
            self.logger.error(f"Error handling WebSocket message: {e}")


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _build_meta(
    subscription_key: str,
    is_private: bool,
    orderbook_pair: str | None,
    orderbook_decimal: int | None,
) -> dict[str, Any] | None:
    """Build the subscription meta dict (orderbook info or None for topic-list subscriptions)."""
    pair = orderbook_pair
    dec = orderbook_decimal
    if not is_private and pair is None and subscription_key.startswith("OrderBook/"):
        pair = subscription_key[len("OrderBook/") :]
    if pair is not None and dec is None:
        dec = 8

    if pair is not None:
        return {"kind": "orderbook", "pair": pair, "decimal": int(dec if dec is not None else 8)}
    return None


def _build_subscribe_payload(
    subscription_key: str,
    is_private: bool,
    meta: dict[str, Any] | None,
    account: Any | None,
    config: Any,
) -> dict[str, Any] | None:
    """
    Build the wire-protocol subscribe payload (docs/websocket.md).

    Returns None if the payload cannot be built (e.g. private topic without account).
    """
    if meta and meta.get("kind") == "orderbook":
        pair = meta["pair"]
        dec = meta["decimal"]
        payload: dict[str, Any] = {
            "type": "subscribe",
            "data": pair,
            "pair": pair,
            "decimal": dec,
        }
        addr = getattr(account, "address", None) if account else None
        if addr:
            payload["traderaddress"] = addr
        return payload

    if is_private:
        if not account:
            return None  # caller logs the warning

        payload = {"type": "subscribe", "topics": [subscription_key]}

        # The Dexalot backend accepts private-topic signatures whose timestamp is
        # within ±30 000 ms of server time.  If the local clock is skewed, set
        # config.ws_time_offset_ms (env: DEXALOT_WS_TIME_OFFSET_MS) to compensate.
        time_offset_ms = getattr(config, "ws_time_offset_ms", 0)
        ts = int(time.time() * 1000) + time_offset_ms
        msg_to_sign = f"{account.address}{ts}"

        from eth_account.messages import encode_defunct

        message_hash = encode_defunct(text=msg_to_sign)
        signed_message = account.sign_message(message_hash)
        signature = signed_message.signature.hex()

        payload["address"] = account.address
        payload["signature"] = signature
        payload["timestamp"] = ts
        return payload

    return {"type": "subscribe", "topics": [subscription_key]}
