"""Unit tests for the CLOB order-type domain model."""

import json
import os

import pytest

from dexalot_sdk.core.base import DexalotBaseClient
from dexalot_sdk.core.order_types import (
    ORDER_STATUS_NAMES,
    ORDER_TYPE_NAMES,
    SIDE_NAMES,
    STP_NAMES,
    TIME_IN_FORCE_NAMES,
    OrderType,
    SelfTradePrevention,
    Side,
    TimeInForce,
    enum_int_to_name,
    parse_order_type,
    parse_side,
    parse_stp,
    parse_time_in_force,
    validate_order_combo,
)
from dexalot_sdk.utils.error_sanitizer import sanitize_error_message


class TestEnumValues:
    """On-chain integer values must never drift."""

    def test_side_values(self):
        assert (Side.BUY, Side.SELL) == (0, 1)

    def test_write_enum_excludes_stop_but_read_map_includes(self):
        # The write-side enum places MARKET/LIMIT only (SDK never originates a
        # stop order), but the read map mirrors the full contract Type1 enum.
        assert {m.name: m.value for m in OrderType} == {"MARKET": 0, "LIMIT": 1}
        assert "STOP" not in OrderType.__members__
        assert "STOPLIMIT" not in OrderType.__members__
        assert ORDER_TYPE_NAMES[2] == "STOP"
        assert ORDER_TYPE_NAMES[3] == "STOPLIMIT"

    def test_time_in_force_values(self):
        assert {m.name: m.value for m in TimeInForce} == {
            "GTC": 0,
            "FOK": 1,
            "IOC": 2,
            "PO": 3,
        }

    def test_stp_values(self):
        assert {m.name: m.value for m in SelfTradePrevention} == {
            "CANCEL_TAKER": 0,
            "CANCEL_MAKER": 1,
            "CANCEL_BOTH": 2,
            "CANCEL_NONE": 3,
        }


class TestEnumIntToName:
    def test_known_values_map_to_labels(self):
        assert enum_int_to_name(0, SIDE_NAMES) == "BUY"
        assert enum_int_to_name(1, ORDER_TYPE_NAMES) == "LIMIT"
        # STOP/STOPLIMIT are real contract Type1 members on the read side.
        assert enum_int_to_name(2, ORDER_TYPE_NAMES) == "STOP"
        assert enum_int_to_name(3, ORDER_TYPE_NAMES) == "STOPLIMIT"
        assert enum_int_to_name(3, TIME_IN_FORCE_NAMES) == "PO"
        assert enum_int_to_name(6, ORDER_STATUS_NAMES) == "KILLED"
        assert enum_int_to_name(2, STP_NAMES) == "CANCEL_BOTH"

    def test_unknown_int_maps_to_sentinel(self):
        # Genuinely-unknown integers map to a visible sentinel, not a guess.
        assert enum_int_to_name(7, ORDER_TYPE_NAMES) == "UNKNOWN(7)"
        assert enum_int_to_name(99, TIME_IN_FORCE_NAMES) == "UNKNOWN(99)"

    def test_non_int_passthrough(self):
        assert enum_int_to_name("LIMIT", ORDER_TYPE_NAMES) == "LIMIT"
        assert enum_int_to_name(None, ORDER_TYPE_NAMES) is None

    def test_bool_passthrough(self):
        assert enum_int_to_name(True, ORDER_TYPE_NAMES) is True


class TestParsers:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("BUY", 0),
            ("sell", 1),
            ("b", 0),
            ("S", 1),
            (Side.BUY, 0),
            (1, 1),
        ],
    )
    def test_parse_side(self, value, expected):
        res = parse_side(value)
        assert res.success and res.data == expected

    @pytest.mark.parametrize(
        "value,expected",
        [("MARKET", 0), ("limit", 1), (OrderType.MARKET, 0), (1, 1)],
    )
    def test_parse_order_type(self, value, expected):
        res = parse_order_type(value)
        assert res.success and res.data == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("GTC", 0),
            ("fok", 1),
            ("IOC", 2),
            ("PO", 3),
            ("POST_ONLY", 3),
            ("post_only", 3),
            ("FILL_OR_KILL", 1),
            ("IMMEDIATE_OR_CANCEL", 2),
            ("GOOD_TILL_CANCEL", 0),
            (TimeInForce.IOC, 2),
            (3, 3),
        ],
    )
    def test_parse_time_in_force_aliases(self, value, expected):
        res = parse_time_in_force(value)
        assert res.success and res.data == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("CANCEL_TAKER", 0),
            ("CANCEL_NEWEST", 0),
            ("CANCEL_OLDEST", 1),
            ("DO_NOT_CANCEL", 3),
            ("NONE", 3),
            # contract spellings (no underscores)
            ("CANCELTAKER", 0),
            ("CANCELMAKER", 1),
            ("CANCELBOTH", 2),
            (SelfTradePrevention.CANCEL_BOTH, 2),
            (1, 1),
        ],
    )
    def test_parse_stp_aliases(self, value, expected):
        res = parse_stp(value)
        assert res.success and res.data == expected

    def test_parse_rejects_unknown_name(self):
        assert not parse_time_in_force("FAST").success
        assert not parse_order_type("STOP").success
        assert not parse_side("HOLD").success

    def test_parse_rejects_out_of_range_int(self):
        assert not parse_order_type(2).success
        assert not parse_time_in_force(9).success

    def test_parse_rejects_bool_and_wrong_type(self):
        assert not parse_side(True).success
        assert not parse_time_in_force(1.5).success


class TestValidateOrderCombo:
    def test_limit_requires_price(self):
        assert validate_order_combo(OrderType.LIMIT, TimeInForce.GTC, has_price=True).success
        assert not validate_order_combo(OrderType.LIMIT, TimeInForce.GTC, has_price=False).success

    def test_market_is_permissive(self):
        # Contract ignores type2/price for MARKET (no revert); SDK matches that.
        assert validate_order_combo(OrderType.MARKET, TimeInForce.GTC, has_price=False).success
        assert validate_order_combo(OrderType.MARKET, TimeInForce.PO, has_price=False).success
        assert validate_order_combo(OrderType.MARKET, TimeInForce.IOC, has_price=True).success

    def test_limit_post_only_allowed(self):
        assert validate_order_combo(OrderType.LIMIT, TimeInForce.PO, has_price=True).success


class TestOrderTypeRevertCodes:
    """Order-type reverts surface a friendly description with the code intact."""

    @pytest.fixture(scope="class")
    def error_codes(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "src",
            "dexalot_sdk",
            "core",
            "errors.json",
        )
        with open(path) as f:
            return json.load(f)

    @pytest.mark.parametrize(
        "code",
        ["T-POOA-01", "T-T2PO-01", "T-STPR-01", "T-FOKF-01"],
    )
    def test_codes_present_with_descriptions(self, error_codes, code):
        assert code in error_codes
        assert error_codes[code].strip()

    def test_parse_revert_reason_maps_code(self, error_codes):
        # _parse_revert_reason only reads self.error_codes; call it on a stub.
        stub = type("Stub", (), {"error_codes": error_codes})()
        raw = "execution reverted: T-FOKF-01"
        parsed = DexalotBaseClient._parse_revert_reason(stub, raw)
        assert parsed.startswith("T-FOKF-01:")
        assert "FOK" in parsed

    def test_sanitizer_preserves_code(self):
        # Sanitization strips paths/URLs but must keep the T-XXXX-NN code.
        msg = "T-STPR-01: TradePairs: order cancled due to self trade prevention"
        out = sanitize_error_message(msg, "placing order")
        assert "T-STPR-01" in out
