"""
WebSocket orderbook integration test.

**Test strategy:**
Subscribe to the AVAX/USDC order book and stay subscribed for up to 30 seconds,
printing every update as it arrives. The test passes if at least one ``orderBooks``
message is received within the first 30 seconds. Press Ctrl+C at any time to stop
early; the connection is closed cleanly and the test is marked skipped.

**Protocol:** follows ``docs/websocket.md`` — pair subscribe / ``orderBooks`` payloads.
``subscribe_to_events("OrderBook/AVAX/USDC")`` maps to a wire subscribe message
``{"type":"subscribe","data":"AVAX/USDC","pair":"AVAX/USDC","decimal":<N>}``.

**Ctrl+C:** pytest-asyncio cancels the test task on SIGINT (``asyncio.CancelledError``
at the next ``await``). We catch it, run cleanup, call ``Task.uncancel()`` to clear
cancel state (Python 3.11+ requirement when suppressing cancellation), and
``pytest.skip``.

**Fixture setup** (``client``): runs before this body; Ctrl+C there is handled by
pytest / the default handler, not this test.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

TOPIC_AVAX_USDC = "OrderBook/AVAX/USDC"
PAIR_AVAX_USDC = "AVAX/USDC"

CONNECT_WAIT_S = 30.0
STREAM_DURATION_S = 30.0   # Stay subscribed for this long (or until Ctrl+C)
FIRST_UPDATE_WAIT_S = 30.0  # Fail if no update arrives within this window
POLL_S = 0.25

# Protocol control / non-data types (docs/websocket.md)
_WS_CONTROL_TYPES = frozenset(
    {"subscribe", "unsubscribe", "pong", "subscribed", "ping"}
)


def _clear_suppressed_cancellation() -> None:
    """Match Python docs: if we caught ``CancelledError`` and continue, clear cancel state."""
    t = asyncio.current_task()
    if t is None:
        return
    uncancel = getattr(t, "uncancel", None)
    cancelling = getattr(t, "cancelling", None)
    if not callable(uncancel) or not callable(cancelling):
        return
    try:
        while cancelling():
            uncancel()
    except TypeError:
        pass


def _is_orderbook_stream_message(msg: dict) -> bool:
    """True for WsRawOrderbookData-style payloads for AVAX/USDC (docs/websocket.md)."""
    if not isinstance(msg, dict):
        return False
    if msg.get("type") in _WS_CONTROL_TYPES:
        return False
    if msg.get("type") == "orderBooks" and msg.get("pair") == PAIR_AVAX_USDC:
        inner = msg.get("data")
        if isinstance(inner, dict) and ("buyBook" in inner or "sellBook" in inner):
            return True
        return True
    if msg.get("pair") == PAIR_AVAX_USDC and ("bids" in msg or "asks" in msg):
        return True
    data = msg.get("data")
    if (
        msg.get("pair") == PAIR_AVAX_USDC
        and isinstance(data, dict)
        and ("bids" in data or "asks" in data)
    ):
        return True
    return False


async def _wait_until_connected(manager: object, timeout_s: float) -> bool:
    """Return True when ``manager.is_connected``; False on timeout. ``CancelledError`` propagates."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if getattr(manager, "is_connected", False):
            return True
        await asyncio.sleep(POLL_S)
    return False


class TestWebSocketOrderbook:
    @pytest.mark.asyncio
    async def test_07_websocket_orderbook(self, client):
        """Subscribe to AVAX/USDC for 30s (or Ctrl+C), printing every update.

        Outcome:
        - ``update``    — at least one orderbook message received; test passes.
        - ``timeout``   — no message in ``FIRST_UPDATE_WAIT_S``; test fails.
        - ``interrupt`` — Ctrl+C; connection closed cleanly; test skipped.
        """
        loop = asyncio.get_running_loop()

        client.config.ws_manager_enabled = True

        first_update = asyncio.Event()
        updates: list[dict] = []

        def on_message(message: object) -> None:
            if not isinstance(message, dict):
                return
            if not _is_orderbook_stream_message(message):
                return
            updates.append(message)
            print(
                f"\n[test_07] orderbook update #{len(updates)}:\n"
                f"{json.dumps(message, indent=2, default=str)}\n",
                flush=True,
            )
            if not first_update.is_set():
                loop.call_soon_threadsafe(first_update.set)

        outcome = "error"
        try:
            await client.subscribe_to_events(
                TOPIC_AVAX_USDC, on_message, is_private=False
            )

            manager = getattr(client, "_ws_manager", None)
            assert manager is not None

            if not await _wait_until_connected(manager, CONNECT_WAIT_S):
                pytest.fail(
                    "WebSocket did not connect within "
                    f"{CONNECT_WAIT_S:.0f}s (e.g. handshake errors such as HTTP 502)."
                )

            # Wait for the first update before starting the stream window.
            try:
                await asyncio.wait_for(first_update.wait(), timeout=FIRST_UPDATE_WAIT_S)
            except TimeoutError:
                outcome = "timeout"
            else:
                # First update received — stay subscribed for the rest of the window.
                outcome = "update"
                print(
                    f"[test_07] First update received. "
                    f"Staying subscribed for {STREAM_DURATION_S:.0f}s total "
                    f"(Ctrl+C to stop early)...",
                    flush=True,
                )
                elapsed = 0.0
                while elapsed < STREAM_DURATION_S:
                    await asyncio.sleep(POLL_S)
                    elapsed += POLL_S

                print(
                    f"[test_07] Stream complete. "
                    f"Received {len(updates)} update(s) over {STREAM_DURATION_S:.0f}s.",
                    flush=True,
                )

        except asyncio.CancelledError:
            outcome = "interrupt"
            print(
                "\n[test_07] Task cancelled (Ctrl+C / default asyncio cancellation).",
                flush=True,
            )
        finally:
            try:
                client.unsubscribe_from_events(TOPIC_AVAX_USDC)
            except Exception:
                pass
            # After CancelledError, do not await a multi-second grace wait: disconnect() can
            # block inside the WS stack; polling only delays pytest teardown and a second
            # Ctrl+C becomes KeyboardInterrupt. Daemon thread still runs disconnect().
            await client.close_websocket(
                grace_s=0.0 if outcome == "interrupt" else 3.0
            )

        if outcome == "interrupt":
            print("[test_07] Teardown complete; exiting test.", flush=True)
            _clear_suppressed_cancellation()
            pytest.skip("Stopped by user (SIGINT / Ctrl+C); WebSocket closed.")

        if outcome == "timeout":
            pytest.fail(
                f"No orderbook update for {PAIR_AVAX_USDC} within {FIRST_UPDATE_WAIT_S:.0f}s "
                "after connect."
            )

        assert outcome == "update"
        assert len(updates) >= 1
        assert all(_is_orderbook_stream_message(u) for u in updates)
