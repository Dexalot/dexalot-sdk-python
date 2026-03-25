import json
import logging
import threading
import time
from collections.abc import Callable
from enum import Enum
from typing import Any

import websocket

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
    """

    def __init__(
        self,
        ws_url: str,
        account: Any | None,
        config: Any,
        logger: logging.Logger | None = None,
    ):
        """
        Initialize WebSocketManager.

        Args:
            ws_url: WebSocket URL to connect to
            account: Optional Account instance for private topic authentication
            config: DexalotConfig instance with WebSocket settings
            logger: Optional logger instance
        """
        self.ws_url = ws_url
        self.account = account
        self.config = config
        self.logger = logger or get_logger(__name__)

        # Connection state
        self._state = ConnectionState.DISCONNECTED
        self._state_lock = threading.Lock()
        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None

        # Subscriptions: key -> (callback, is_private, meta)
        # meta is None (legacy topics) or {"kind": "orderbook", "pair", "decimal"} per docs/websocket.md
        self._subscriptions: dict[str, tuple[Callable, bool, dict[str, Any] | None]] = {}
        self._subscriptions_lock = threading.Lock()

        # Reconnection state
        self._reconnect_attempts = 0
        self._reconnect_delay = config.ws_reconnect_initial_delay
        self._should_reconnect = False

        # Ping/pong state
        self._last_pong_time: float | None = None
        self._ping_thread: threading.Thread | None = None
        self._stop_ping = threading.Event()

    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        with self._state_lock:
            return self._state

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self.state == ConnectionState.CONNECTED

    def connect(self) -> None:
        """
        Establish WebSocket connection.
        Raises RuntimeError if WebSocket is disabled in config.
        """
        if not self.config.ws_manager_enabled:
            raise RuntimeError(
                "WebSocket Manager is disabled. Set ws_manager_enabled=True in config."
            )

        with self._state_lock:
            if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
                self.logger.debug("WebSocket already connecting or connected")
                return
            self._state = ConnectionState.CONNECTING

        try:
            self._ws = websocket.WebSocketApp(
                self.ws_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            self._should_reconnect = True
            self._ws_thread = threading.Thread(target=self._run_forever, daemon=True)
            self._ws_thread.start()
        except Exception as e:
            with self._state_lock:
                self._state = ConnectionState.DISCONNECTED
            self.logger.error(f"Failed to create WebSocket connection: {e}")
            raise

    def _run_forever(self) -> None:
        """Run WebSocket in a separate thread."""
        ws = self._ws
        if ws is None:
            return
        try:
            ws.run_forever(
                ping_interval=self.config.ws_ping_interval,
                ping_timeout=self.config.ws_ping_timeout,
            )
        except Exception as e:
            self.logger.error(f"WebSocket run_forever error: {e}")

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        """Handle WebSocket connection opened."""
        self.logger.info("WebSocket connection opened")
        with self._state_lock:
            self._state = ConnectionState.CONNECTED
            self._reconnect_attempts = 0
            self._reconnect_delay = self.config.ws_reconnect_initial_delay

        # Start ping thread
        self._start_ping_thread()

        # Re-subscribe to all active topics (copy keys to avoid holding
        # _subscriptions_lock while _subscribe_topic re-acquires it).
        with self._subscriptions_lock:
            keys = list(self._subscriptions.keys())
        for subscription_key in keys:
            self._subscribe_topic(ws, subscription_key)

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            self.logger.debug(f"WebSocket message received: {data}")

            # Handle pong messages
            if isinstance(data, dict) and data.get("type") == "pong":
                self._last_pong_time = time.time()
                return

            # Dexalot order book stream: type "orderBooks", pair "BASE/QUOTE" (docs/websocket.md)
            if data.get("type") == "orderBooks" and data.get("pair"):
                pair = data["pair"]
                with self._subscriptions_lock:
                    specs = list(self._subscriptions.items())
                for _sub_key, (callback, _priv, meta) in specs:
                    if meta and meta.get("kind") == "orderbook" and meta.get("pair") == pair:
                        try:
                            callback(data)
                        except Exception as e:
                            self.logger.error(f"Error in orderbook callback for {pair}: {e}")
                return

            # Legacy: messages with a "topic" field
            topic = data.get("topic") if isinstance(data, dict) else None

            if topic:
                with self._subscriptions_lock:
                    if topic in self._subscriptions:
                        callback, _, _meta = self._subscriptions[topic]
                        try:
                            callback(data)
                        except Exception as e:
                            self.logger.error(f"Error in callback for topic {topic}: {e}")
            else:
                # Broadcast to all callbacks if no topic specified
                with self._subscriptions_lock:
                    for callback, _, _meta in self._subscriptions.values():
                        try:
                            callback(data)
                        except Exception as e:
                            self.logger.error(f"Error in callback: {e}")

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse WebSocket message: {e}")
        except Exception as e:
            self.logger.error(f"Error handling WebSocket message: {e}")

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        """Handle WebSocket error."""
        self.logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws: websocket.WebSocketApp, close_status_code: int, close_msg: str) -> None:
        """Handle WebSocket connection closed."""
        self.logger.info(f"WebSocket closed: {close_status_code} {close_msg}")
        self._stop_ping_thread()

        with self._state_lock:
            if self._should_reconnect and self._state != ConnectionState.DISCONNECTED:
                self._state = ConnectionState.RECONNECTING
                self._schedule_reconnect()
            else:
                self._state = ConnectionState.DISCONNECTED

    def _schedule_reconnect(self) -> None:
        """Schedule reconnection attempt with exponential backoff."""
        if not self._should_reconnect:
            return

        max_attempts = self.config.ws_reconnect_max_attempts
        if max_attempts > 0 and self._reconnect_attempts >= max_attempts:
            self.logger.error(f"Max reconnection attempts ({max_attempts}) reached")
            with self._state_lock:
                self._state = ConnectionState.DISCONNECTED
            return

        self._reconnect_attempts += 1
        delay = min(
            self._reconnect_delay,
            self.config.ws_reconnect_max_delay,
        )
        self.logger.info(f"Scheduling reconnect in {delay}s (attempt {self._reconnect_attempts})")

        def reconnect():
            time.sleep(delay)
            if self._should_reconnect:
                try:
                    self.connect()
                except Exception as e:
                    self.logger.error(f"Reconnection failed: {e}")
                    # Exponential backoff
                    self._reconnect_delay *= self.config.ws_reconnect_exponential_base
                    self._schedule_reconnect()

        threading.Thread(target=reconnect, daemon=True).start()
        # Increase delay for next attempt
        self._reconnect_delay *= self.config.ws_reconnect_exponential_base

    def _start_ping_thread(self) -> None:
        """Start ping thread for heartbeat."""
        self._stop_ping.clear()
        if self._ping_thread and self._ping_thread.is_alive():
            return

        def ping_loop():
            while not self._stop_ping.is_set() and self.is_connected:
                time.sleep(self.config.ws_ping_interval)
                if not self._stop_ping.is_set() and self._ws:
                    try:
                        # Check if we got a pong recently
                        if (
                            self._last_pong_time
                            and time.time() - self._last_pong_time > self.config.ws_ping_timeout * 2
                        ):
                            self.logger.warning("No pong received, connection may be dead")
                            if self._ws:
                                self._ws.close()
                        else:
                            # Send ping (websocket-client handles this automatically via ping_interval)
                            pass
                    except Exception as e:
                        self.logger.error(f"Error in ping loop: {e}")

        self._ping_thread = threading.Thread(target=ping_loop, daemon=True)
        self._ping_thread.start()

    def _stop_ping_thread(self) -> None:
        """Stop ping thread."""
        self._stop_ping.set()
        if self._ping_thread:
            self._ping_thread.join(timeout=0.35)

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
        Subscribe with a callback.

        Public order books use the pair payload from docs/websocket.md. Pass orderbook_pair /
        orderbook_decimal when known; otherwise a key starting with "OrderBook/" is mapped
        to a pair subscribe with default decimal 8.
        """
        if not self.config.ws_manager_enabled:
            raise RuntimeError(
                "WebSocket Manager is disabled. Set ws_manager_enabled=True in config."
            )

        pair = orderbook_pair
        dec = orderbook_decimal
        if not is_private and pair is None and subscription_key.startswith("OrderBook/"):
            pair = subscription_key[len("OrderBook/") :]
        if pair is not None and dec is None:
            dec = 8

        meta: dict[str, Any] | None
        if pair is not None:
            orderbook_dec = dec if dec is not None else 8
            meta = {"kind": "orderbook", "pair": pair, "decimal": int(orderbook_dec)}
        else:
            meta = None

        with self._subscriptions_lock:
            self._subscriptions[subscription_key] = (callback, is_private, meta)

        # If connected, send subscription immediately
        if self.is_connected and self._ws:
            self._subscribe_topic(self._ws, subscription_key)
        elif not self.is_connected:
            # Auto-connect if not connected
            self.connect()

    def _subscribe_topic(self, ws: websocket.WebSocketApp, subscription_key: str) -> None:
        """Send subscription message for a subscription key (see docs/websocket.md)."""
        with self._subscriptions_lock:
            spec = self._subscriptions.get(subscription_key)
        if not spec:
            return
        _callback, is_private, meta = spec

        if meta and meta.get("kind") == "orderbook":
            pair = meta["pair"]
            dec = meta["decimal"]
            payload: dict[str, Any] = {
                "type": "subscribe",
                "data": pair,
                "pair": pair,
                "decimal": dec,
            }
            addr = getattr(self.account, "address", None) if self.account else None
            if addr:
                payload["traderaddress"] = addr
        elif is_private:
            if not self.account:
                self.logger.warning(
                    f"Cannot subscribe to private topic {subscription_key} without account"
                )
                return

            payload = {"type": "subscribe", "topics": [subscription_key]}

            # Generate authentication signature (legacy private topics)
            ts = int(time.time() * 1000)
            msg_to_sign = f"{self.account.address}{ts}"

            from eth_account.messages import encode_defunct

            message_hash = encode_defunct(text=msg_to_sign)
            signed_message = self.account.sign_message(message_hash)
            signature = signed_message.signature.hex()

            payload["address"] = self.account.address
            payload["signature"] = signature
            payload["timestamp"] = ts
        else:
            payload = {"type": "subscribe", "topics": [subscription_key]}

        try:
            ws.send(json.dumps(payload))
            self.logger.info(f"Subscribed: {subscription_key}")
        except Exception as e:
            self.logger.error(f"Failed to subscribe {subscription_key}: {e}")

    def unsubscribe(self, subscription_key: str) -> None:
        """
        Unsubscribe from a subscription key (pair or legacy topic).
        """
        with self._subscriptions_lock:
            spec = self._subscriptions.pop(subscription_key, None)
        if spec is None:
            return
        _callback, is_private, meta = spec
        self.logger.info(f"Unsubscribed from: {subscription_key}")

        if self.is_connected and self._ws:
            try:
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
                self._ws.send(json.dumps(payload))
            except Exception as e:
                self.logger.error(f"Failed to unsubscribe {subscription_key}: {e}")

    def disconnect(self) -> None:
        """Close WebSocket connection and cleanup."""
        self._should_reconnect = False
        self._stop_ping_thread()

        if self._ws:
            try:
                self._ws.keep_running = False
            except Exception:
                pass
            try:
                self._ws.close()
            except Exception:
                pass

        if self._ws_thread and self._ws_thread.is_alive():
            # Short timeout: thread is daemon; long joins block asyncio if disconnect() is
            # awaited from async code without asyncio.to_thread (see close_websocket).
            self._ws_thread.join(timeout=0.5)
            if self._ws_thread.is_alive():
                self.logger.debug(
                    "WebSocket thread did not exit within join timeout (daemon; will stop with process)"
                )

        with self._state_lock:
            self._state = ConnectionState.DISCONNECTED

        with self._subscriptions_lock:
            self._subscriptions.clear()

        self.logger.info("WebSocket disconnected and cleaned up")
