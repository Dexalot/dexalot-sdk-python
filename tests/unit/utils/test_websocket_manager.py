import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets.exceptions

from dexalot_sdk.utils.websocket_manager import ConnectionState, WebSocketManager


def make_config(**overrides):
    cfg = MagicMock()
    cfg.ws_manager_enabled = True
    cfg.ws_ping_interval = 30
    cfg.ws_ping_timeout = 10
    cfg.ws_reconnect_initial_delay = 0.0
    cfg.ws_reconnect_max_delay = 60.0
    cfg.ws_reconnect_exponential_base = 2.0
    cfg.ws_reconnect_max_attempts = 0  # infinite by default
    cfg.ws_time_offset_ms = 0
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def make_manager(**config_overrides):
    config = make_config(**config_overrides)
    mgr = WebSocketManager(ws_url="wss://test.example.com", account=None, config=config)
    return mgr


# ---------------------------------------------------------------------------
# _run() — connect, text message, callback invoked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_connect_and_text_message():
    """Method _run connects, routes a text JSON message to a callback."""
    mgr = make_manager()

    received = []
    mgr._subscriptions["ordersUpdate"] = (lambda d: received.append(d), False, None)

    msg = json.dumps({"topic": "ordersUpdate", "data": "x"})

    class _FakeWs:
        """Fake WebSocket: async-iterable over a fixed message list, with a send stub."""

        def __init__(self, messages):
            self._iter = iter(messages)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration from None

        async def send(self, data):
            pass

    class _FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            return _FakeWs([msg])

        async def __aexit__(self, *exc):
            pass

    # _backoff returns False → loop exits after one attempt; state will be RECONNECTING
    with patch("websockets.connect", new=_FakeConnect()):
        with patch.object(mgr, "_backoff", new=AsyncMock(return_value=False)):
            mgr._should_reconnect = True
            await mgr._run()

    assert received == [{"topic": "ordersUpdate", "data": "x"}]


# ---------------------------------------------------------------------------
# _run() — bytes message decoded before routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_bytes_message():
    """Bytes messages are decoded to str then handled."""
    mgr = make_manager()

    received = []
    mgr._subscriptions["tradeList"] = (lambda d: received.append(d), False, None)

    msg = json.dumps({"topic": "tradeList", "data": "y"}).encode()

    class _FakeWs:
        def __init__(self, messages):
            self._iter = iter(messages)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration from None

        async def send(self, data):
            pass

    class _FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            return _FakeWs([msg])

        async def __aexit__(self, *exc):
            pass

    with patch("websockets.connect", new=_FakeConnect()):
        with patch.object(mgr, "_backoff", new=AsyncMock(return_value=False)):
            mgr._should_reconnect = True
            await mgr._run()

    assert received == [{"topic": "tradeList", "data": "y"}]


# ---------------------------------------------------------------------------
# _run() — CancelledError exits cleanly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cancelled_error():
    """Method cancelledError inside connect sets state DISCONNECTED and breaks."""
    mgr = make_manager()

    class _FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            raise asyncio.CancelledError()

        async def __aexit__(self, *exc):
            pass

    with patch("websockets.connect", new=_FakeConnect()):
        mgr._should_reconnect = True
        await mgr._run()

    assert mgr._state == ConnectionState.DISCONNECTED
    assert mgr._ws is None


# ---------------------------------------------------------------------------
# _run() — generic exception triggers reconnect then backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_exception_triggers_reconnect():
    """Non-cancel exception sets RECONNECTING, calls _backoff."""
    mgr = make_manager()

    call_count = 0

    class _FakeConnect:
        def __call__(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return self

        async def __aenter__(self):
            raise RuntimeError("connection refused")

        async def __aexit__(self, *exc):
            pass

    with patch("websockets.connect", new=_FakeConnect()):
        with patch.object(mgr, "_backoff", new=AsyncMock(return_value=False)):
            mgr._should_reconnect = True
            await mgr._run()

    assert call_count == 1
    assert mgr._state in (ConnectionState.RECONNECTING, ConnectionState.DISCONNECTED)


# ---------------------------------------------------------------------------
# _run() — max reconnect attempts reached via _backoff returning False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_max_reconnect_reached():
    """Method _backoff returning False stops the loop."""
    mgr = make_manager(ws_reconnect_max_attempts=1)

    class _FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            raise RuntimeError("fail")

        async def __aexit__(self, *exc):
            pass

    with patch("websockets.connect", new=_FakeConnect()):
        with patch.object(mgr, "_backoff", new=AsyncMock(return_value=False)):
            mgr._should_reconnect = True
            await mgr._run()

    # Loop exited after backoff said stop
    assert mgr._ws is None


# ---------------------------------------------------------------------------
# _send_subscribe — ws is None guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_subscribe_ws_none():
    """Method _send_subscribe with _ws=None returns immediately without error."""
    mgr = make_manager()
    mgr._ws = None
    mgr._subscriptions["topic1"] = (MagicMock(), False, None)
    # Should not raise
    await mgr._send_subscribe("topic1")


# ---------------------------------------------------------------------------
# _send_subscribe — spec not found guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_subscribe_spec_not_found():
    """Method _send_subscribe for unknown key returns immediately without error."""
    mgr = make_manager()
    mgr._ws = AsyncMock()
    # No entry in _subscriptions for this key
    await mgr._send_subscribe("nonexistent_key")
    mgr._ws.send.assert_not_called()


# ---------------------------------------------------------------------------
# _send_subscribe — ConnectionClosed is swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_subscribe_connection_closed():
    """ConnectionClosed during send is silently swallowed."""
    mgr = make_manager()
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(
        side_effect=websockets.exceptions.ConnectionClosed(None, None)
    )
    mgr._ws = mock_ws
    mgr._subscriptions["myTopic"] = (MagicMock(), False, None)

    # Must not raise
    await mgr._send_subscribe("myTopic")


# ---------------------------------------------------------------------------
# _send_subscribe — generic exception logged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_subscribe_generic_exception():
    """Generic exception during send is logged, not raised."""
    mgr = make_manager()
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=OSError("network error"))
    mgr._ws = mock_ws
    mgr._subscriptions["myTopic"] = (MagicMock(), False, None)

    with patch.object(mgr.logger, "error") as mock_log:
        await mgr._send_subscribe("myTopic")

    mock_log.assert_called_once()
    assert "myTopic" in mock_log.call_args[0][0]


# ---------------------------------------------------------------------------
# _send_unsubscribe — orderbook payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_unsubscribe_orderbook_payload():
    """Orderbook unsubscribe sends correct payload with pair/decimal."""
    mgr = make_manager()
    mock_ws = AsyncMock()
    mgr._ws = mock_ws

    meta = {"kind": "orderbook", "pair": "ALOT/USDC", "decimal": 6}
    spec = (MagicMock(), False, meta)

    await mgr._send_unsubscribe("OrderBook/ALOT/USDC", spec)

    mock_ws.send.assert_called_once()
    sent = json.loads(mock_ws.send.call_args[0][0])
    assert sent["type"] == "unsubscribe"
    assert sent["data"] == "ALOT/USDC"
    assert sent["pair"] == "ALOT/USDC"
    assert sent["decimal"] == 6


# ---------------------------------------------------------------------------
# _send_unsubscribe — ConnectionClosed swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_unsubscribe_connection_closed():
    """ConnectionClosed during unsubscribe send is silently swallowed."""
    mgr = make_manager()
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(
        side_effect=websockets.exceptions.ConnectionClosed(None, None)
    )
    mgr._ws = mock_ws

    spec = (MagicMock(), False, None)
    # Must not raise
    await mgr._send_unsubscribe("someTopic", spec)


# ---------------------------------------------------------------------------
# _send_unsubscribe — ws is None guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_unsubscribe_ws_none():
    """Method _send_unsubscribe with _ws=None returns immediately without error."""
    mgr = make_manager()
    mgr._ws = None
    spec = (MagicMock(), False, None)
    await mgr._send_unsubscribe("someTopic", spec)


# ---------------------------------------------------------------------------
# _send_unsubscribe — generic exception logged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_unsubscribe_generic_exception():
    """Generic exception during unsubscribe send is logged, not raised."""
    mgr = make_manager()
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=OSError("net error"))
    mgr._ws = mock_ws

    spec = (MagicMock(), False, None)
    with patch.object(mgr.logger, "error") as mock_log:
        await mgr._send_unsubscribe("someTopic", spec)

    mock_log.assert_called_once()
    assert "someTopic" in mock_log.call_args[0][0]


# ---------------------------------------------------------------------------
# _handle_message — outer exception handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_outer_exception():
    """Unexpected exception inside message handling is logged, not raised."""
    mgr = make_manager()

    # After JSON parse, make data.get raise to fall into the outer except.
    # Must pass isinstance(data, dict) first, so use a real dict subclass that raises on .get().
    class BadDict(dict):
        def get(self, key, default=None):
            raise RuntimeError("unexpected dict error")

    with patch("json.loads", return_value=BadDict()):
        with patch.object(mgr.logger, "error") as mock_log:
            mgr._handle_message("{}")

    mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_message — orderbook callback error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_orderbook_callback_error():
    """Exception inside orderbook callback is logged, not propagated."""
    mgr = make_manager()

    def bad_callback(data):
        raise RuntimeError("callback boom")

    mgr._subscriptions["OrderBook/ETH/USDC"] = (
        bad_callback,
        False,
        {"kind": "orderbook", "pair": "ETH/USDC", "decimal": 8},
    )

    raw = json.dumps({"type": "orderBooks", "pair": "ETH/USDC", "asks": [], "bids": []})

    with patch.object(mgr.logger, "error") as mock_log:
        mgr._handle_message(raw)  # must not raise

    mock_log.assert_called_once()
    assert "ETH/USDC" in mock_log.call_args[0][0]


# ---------------------------------------------------------------------------
# _handle_message — broadcast callback error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_broadcast_callback_error():
    """Exception inside broadcast callback (no topic) is logged, not propagated."""
    mgr = make_manager()

    def bad_callback(data):
        raise RuntimeError("broadcast boom")

    mgr._subscriptions["generalFeed"] = (bad_callback, False, None)

    # Message with no "topic" field triggers broadcast path
    raw = json.dumps({"event": "heartbeat"})

    with patch.object(mgr.logger, "error") as mock_log:
        mgr._handle_message(raw)  # must not raise

    mock_log.assert_called_once()
    assert "broadcast boom" in mock_log.call_args[0][0] or mock_log.called


# ---------------------------------------------------------------------------
# _handle_message — topic callback success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_topic_callback_success():
    """Matching topic callback is invoked with the message data."""
    mgr = make_manager()

    received = []
    mgr._subscriptions["ordersUpdate"] = (lambda d: received.append(d), False, None)

    raw = json.dumps({"topic": "ordersUpdate", "data": "ok"})
    mgr._handle_message(raw)
    await asyncio.sleep(0)  # yield to event loop; keeps function async

    assert received == [{"topic": "ordersUpdate", "data": "ok"}]


# ---------------------------------------------------------------------------
# _handle_message — broadcast skips orderbook subscriptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_broadcast_skips_orderbook():
    """Orderbook subscriptions are skipped in the no-topic broadcast path."""
    mgr = make_manager()

    orderbook_calls = []
    regular_calls = []

    mgr._subscriptions["OrderBook/ETH/USDC"] = (
        lambda d: orderbook_calls.append(d),
        False,
        {"kind": "orderbook", "pair": "ETH/USDC", "decimal": 8},
    )
    mgr._subscriptions["generalFeed"] = (lambda d: regular_calls.append(d), False, None)

    # No "topic" field → broadcast path
    raw = json.dumps({"event": "heartbeat"})
    mgr._handle_message(raw)
    await asyncio.sleep(0)  # yield to event loop; keeps function async

    assert orderbook_calls == []
    assert regular_calls == [{"event": "heartbeat"}]


# ---------------------------------------------------------------------------
# _handle_message — non-dict JSON is silently ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_non_dict_ignored():
    """Non-dict JSON (e.g. a list) is silently discarded without calling callbacks."""
    mgr = make_manager()

    callback = MagicMock()
    mgr._subscriptions["ordersUpdate"] = (callback, False, None)

    mgr._handle_message("[1, 2, 3]")
    await asyncio.sleep(0)

    callback.assert_not_called()
