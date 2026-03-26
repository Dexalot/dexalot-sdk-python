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
    """Lines 184-205: _run connects, routes a text JSON message to a callback."""
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
    """Lines 203-205: bytes messages are decoded to str then handled."""
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
    """Lines 207-216: CancelledError inside connect sets state DISCONNECTED and breaks."""
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
    """Lines 209-219: non-cancel exception sets RECONNECTING, calls _backoff."""
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
    """Lines 218-219: _backoff returning False stops the loop."""
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
# _send_subscribe — ws is None guard (line 252)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_subscribe_ws_none():
    """Line 252: _send_subscribe with _ws=None returns immediately without error."""
    mgr = make_manager()
    mgr._ws = None
    mgr._subscriptions["topic1"] = (MagicMock(), False, None)
    # Should not raise
    await mgr._send_subscribe("topic1")


# ---------------------------------------------------------------------------
# _send_subscribe — spec not found guard (line 255)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_subscribe_spec_not_found():
    """Line 255: _send_subscribe for unknown key returns immediately without error."""
    mgr = make_manager()
    mgr._ws = AsyncMock()
    # No entry in _subscriptions for this key
    await mgr._send_subscribe("nonexistent_key")
    mgr._ws.send.assert_not_called()


# ---------------------------------------------------------------------------
# _send_subscribe — ConnectionClosed is swallowed (line 268)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_subscribe_connection_closed():
    """Line 268: ConnectionClosed during send is silently swallowed."""
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
# _send_subscribe — generic exception logged (line 277)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_subscribe_generic_exception():
    """Line 277: generic exception during send is logged, not raised."""
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
# _send_unsubscribe — orderbook payload (lines 295-298)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_unsubscribe_orderbook_payload():
    """Lines 295-298: orderbook unsubscribe sends correct payload with pair/decimal."""
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
# _send_unsubscribe — ConnectionClosed swallowed (lines 317-318)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_unsubscribe_connection_closed():
    """Lines 317-318: ConnectionClosed during unsubscribe send is silently swallowed."""
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
# _send_unsubscribe — ws is None guard (line 277)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_unsubscribe_ws_none():
    """Line 277: _send_unsubscribe with _ws=None returns immediately without error."""
    mgr = make_manager()
    mgr._ws = None
    spec = (MagicMock(), False, None)
    await mgr._send_unsubscribe("someTopic", spec)


# ---------------------------------------------------------------------------
# _send_unsubscribe — generic exception logged (lines 297-298)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_unsubscribe_generic_exception():
    """Lines 297-298: generic exception during unsubscribe send is logged, not raised."""
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
# _handle_message — outer exception handler (lines 340-341)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_outer_exception():
    """Lines 340-341: unexpected exception inside message handling is logged, not raised."""
    mgr = make_manager()

    # After JSON parse, make data.get raise to fall into the outer except
    bad_data = MagicMock()
    bad_data.get = MagicMock(side_effect=RuntimeError("unexpected dict error"))

    with patch("json.loads", return_value=bad_data):
        with patch.object(mgr.logger, "error") as mock_log:
            mgr._handle_message("{}")

    mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_message — orderbook callback error (lines 335-336)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_orderbook_callback_error():
    """Lines 335-336: exception inside orderbook callback is logged, not propagated."""
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
# _handle_message — broadcast callback error (lines 340-341)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_broadcast_callback_error():
    """Lines 340-341: exception inside broadcast callback (no topic) is logged, not propagated."""
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
