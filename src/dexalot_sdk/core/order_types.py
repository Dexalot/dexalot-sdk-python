"""CLOB order-type domain model.

Single source of truth for the on-chain order enums (``side``, ``type1``,
``type2``, ``stp``, order ``status``) and the rules governing valid order-type
combinations.  Both the write paths (placing orders) and the read paths
(formatting orders returned by the contract / REST API) route through this
module so the integer<->label mapping cannot drift between them.

On-chain enum values (from the ``TradePairs`` contract) are authoritative:

* ``side``    -- 0 BUY, 1 SELL
* ``type1``   -- 0 MARKET, 1 LIMIT  (the contract enum has no STOP/STOPLIMIT
  members and the ``NewOrder`` struct carries no trigger-price field, so stop
  orders are neither defined nor encodable -- see the SDK docs)
* ``type2``   -- 0 GTC, 1 FOK, 2 IOC, 3 PO  (time-in-force)
* ``stp``     -- self-trade prevention (see :class:`SelfTradePrevention`)
* ``status``  -- 0 NEW .. 6 KILLED

.. note::
   The ``stp`` integer<->name mapping and the valid ``type1`` x ``type2``
   matrix encode the SDK's working specification.  They are intentionally
   centralised here so a single edit corrects every call site if the contract
   team confirms different semantics.
"""

from __future__ import annotations

from enum import IntEnum

from ..utils.result import Result


class Side(IntEnum):
    """Order side (``side`` field)."""

    BUY = 0
    SELL = 1


class OrderType(IntEnum):
    """Order type (``type1`` field) the SDK can *place*.

    The contract ``Type1`` enum is ``{MARKET, LIMIT, STOP, STOPLIMIT}``, but
    ``STOP``/``STOPLIMIT`` are reserved/unused on-chain (no trigger-price field
    in ``NewOrder``, never added to a pair's allowed order types).  This
    write-side enum therefore intentionally omits them so the SDK never
    originates a stop order.  Read-side labelling still recognises them — see
    :data:`ORDER_TYPE_NAMES`.
    """

    MARKET = 0
    LIMIT = 1


class TimeInForce(IntEnum):
    """Time-in-force / execution modifier (``type2`` field)."""

    GTC = 0  # Good-Till-Cancelled
    FOK = 1  # Fill-Or-Kill
    IOC = 2  # Immediate-Or-Cancel
    PO = 3  # Post-Only (maker-only; rejected if it would cross)


class SelfTradePrevention(IntEnum):
    """Self-trade prevention mode (``stp`` field).

    Names encode the SDK's working assumption; confirm against the contract
    before relying on the distinction between maker/taker cancellation.
    """

    CANCEL_TAKER = 0  # cancel the incoming (newest) order
    CANCEL_MAKER = 1  # cancel the resting (oldest) order
    CANCEL_BOTH = 2
    CANCEL_NONE = 3  # do not cancel; allow the self-trade


class OrderStatus(IntEnum):
    """Order status as reported by the contract / API."""

    NEW = 0
    REJECTED = 1
    PARTIAL = 2
    FILLED = 3
    CANCELED = 4
    EXPIRED = 5
    KILLED = 6


# --- int -> canonical label maps (read paths) ------------------------------

SIDE_NAMES: dict[int, str] = {m.value: m.name for m in Side}
# Read-side type1 labels mirror the full contract Type1 enum, including the
# reserved STOP/STOPLIMIT members. The SDK never *places* those (the write-side
# OrderType enum omits them), but a read should faithfully reflect any value the
# contract could report rather than mislabel it as UNKNOWN.
ORDER_TYPE_NAMES: dict[int, str] = {0: "MARKET", 1: "LIMIT", 2: "STOP", 3: "STOPLIMIT"}
TIME_IN_FORCE_NAMES: dict[int, str] = {m.value: m.name for m in TimeInForce}
STP_NAMES: dict[int, str] = {m.value: m.name for m in SelfTradePrevention}
ORDER_STATUS_NAMES: dict[int, str] = {m.value: m.name for m in OrderStatus}


# --- name/alias -> int maps (write paths) ----------------------------------

_SIDE_ALIASES: dict[str, int] = {"B": Side.BUY, "S": Side.SELL}

_ORDER_TYPE_ALIASES: dict[str, int] = {}

_TIME_IN_FORCE_ALIASES: dict[str, int] = {
    "GOOD_TILL_CANCEL": TimeInForce.GTC,
    "GOOD_TILL_CANCELLED": TimeInForce.GTC,
    "GOOD_TILL_CANCELED": TimeInForce.GTC,
    "FILL_OR_KILL": TimeInForce.FOK,
    "IMMEDIATE_OR_CANCEL": TimeInForce.IOC,
    "POST_ONLY": TimeInForce.PO,
    "POSTONLY": TimeInForce.PO,
}

_STP_ALIASES: dict[str, int] = {
    "CANCEL_NEWEST": SelfTradePrevention.CANCEL_TAKER,
    "CANCEL_OLDEST": SelfTradePrevention.CANCEL_MAKER,
    "DO_NOT_CANCEL": SelfTradePrevention.CANCEL_NONE,
    "NONE": SelfTradePrevention.CANCEL_NONE,
}


def enum_int_to_name(value: object, names: dict[int, str]) -> object:
    """Normalize an enum integer from a contract/API read into a string label.

    Integers present in ``names`` map to their canonical label.  Integers
    absent from ``names`` map to an explicit ``"UNKNOWN(<n>)"`` sentinel rather
    than a fabricated label, so an unexpected on-chain value is visible (and
    signals the SDK needs updating) instead of being silently mislabelled.
    Non-integer values (e.g. labels already normalized upstream) pass through
    unchanged.
    """
    if isinstance(value, bool):  # bool is an int subclass; treat as passthrough
        return value
    if isinstance(value, int):
        label = names.get(value)
        return label if label is not None else f"UNKNOWN({value})"
    return value


def _parse_enum(
    value: object,
    enum_cls: type[IntEnum],
    aliases: dict[str, int],
    field: str,
) -> Result[int]:
    """Resolve ``value`` (enum member, int, or case-insensitive name/alias) to int."""
    if isinstance(value, bool):
        return Result.fail(f"Invalid {field}: {value!r}")
    if isinstance(value, enum_cls):
        return Result.ok(int(value))
    if isinstance(value, int):
        try:
            return Result.ok(int(enum_cls(value)))
        except ValueError:
            valid = ", ".join(str(int(m)) for m in enum_cls)
            return Result.fail(f"Invalid {field} {value!r}. Must be one of: {valid}.")
    if isinstance(value, str):
        key = value.strip().upper()
        if key in enum_cls.__members__:
            return Result.ok(int(enum_cls[key]))
        if key in aliases:
            return Result.ok(int(aliases[key]))
        valid = ", ".join(enum_cls.__members__)
        return Result.fail(f"Invalid {field} '{value}'. Must be one of: {valid}.")
    return Result.fail(f"Invalid {field}: expected name or int, got {type(value).__name__}.")


def parse_side(value: object) -> Result[int]:
    """Resolve a side (``"BUY"``/``"SELL"``, alias, int, or :class:`Side`) to int."""
    return _parse_enum(value, Side, _SIDE_ALIASES, "side")


def parse_order_type(value: object) -> Result[int]:
    """Resolve an order type (``"MARKET"``/``"LIMIT"``, int, or :class:`OrderType`) to int."""
    return _parse_enum(value, OrderType, _ORDER_TYPE_ALIASES, "order type")


def parse_time_in_force(value: object) -> Result[int]:
    """Resolve a time-in-force (``"GTC"``/``"FOK"``/``"IOC"``/``"PO"``, alias, int) to int."""
    return _parse_enum(value, TimeInForce, _TIME_IN_FORCE_ALIASES, "time_in_force")


def parse_stp(value: object) -> Result[int]:
    """Resolve a self-trade-prevention mode (name, alias, int, or enum) to int."""
    return _parse_enum(value, SelfTradePrevention, _STP_ALIASES, "stp")


def validate_order_combo(type1: int, type2: int, has_price: bool) -> Result[None]:
    """Validate a (``type1``, ``type2``, price-presence) combination client-side.

    Encodes the SDK's working order-type matrix so invalid combinations are
    rejected before a transaction is sent rather than reverting on-chain:

    * MARKET orders must be IOC or FOK and must not carry a price.
    * LIMIT orders require a price.
    * Post-Only (PO) is maker-only and therefore LIMIT-only.

    Per-pair enabled order types are enforced by the contract; combinations a
    pair has disabled still surface as on-chain reverts.
    """
    if type1 == OrderType.MARKET:
        if has_price:
            return Result.fail("MARKET orders must not specify a price.")
        if type2 not in (TimeInForce.IOC, TimeInForce.FOK):
            return Result.fail("MARKET orders must use IOC or FOK time-in-force.")
    elif type1 == OrderType.LIMIT:
        if not has_price:
            return Result.fail("LIMIT orders require a price.")
        # Post-Only (PO) is maker-only and therefore LIMIT-only; the MARKET
        # branch above already rejects MARKET+PO, so no extra check is needed.
    return Result.ok(None)
