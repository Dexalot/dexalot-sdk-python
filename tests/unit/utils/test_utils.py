from dexalot_sdk.utils import Utils


class TestUtils:
    def test_to_bytes32_basic(self):
        """Test basic string to bytes32 conversion."""
        res = Utils.to_bytes32("AVAX")
        assert len(res) == 32
        assert res.startswith(b"AVAX")
        assert res.endswith(b"\0")
        assert res == b"AVAX" + b"\0" * 28

    def test_to_bytes32_empty(self):
        """Test empty string conversion."""
        res = Utils.to_bytes32("")
        assert len(res) == 32
        assert res == b"\0" * 32

    def test_to_bytes32_long(self):
        """Test string longer than 32 bytes (current implementation does not truncate)."""
        long_str = "A" * 33
        res = Utils.to_bytes32(long_str)
        assert len(res) == 33
        assert res == b"A" * 33
        # Note: If implementation changes to truncate/error, this test should be updated.

    def test_from_bytes32_basic(self):
        """Test basic bytes32 to string conversion."""
        b = b"AVAX" + b"\0" * 28
        res = Utils.from_bytes32(b)
        assert res == "AVAX"

    def test_from_bytes32_empty(self):
        """Test empty bytes32 conversion."""
        b = b"\0" * 32
        res = Utils.from_bytes32(b)
        assert res == ""

    def test_from_bytes32_no_padding(self):
        """Test bytes without null padding."""
        b = b"AVAX"
        res = Utils.from_bytes32(b)
        assert res == "AVAX"

    def test_unit_conversion_to_base(self):
        """Test Display -> Base conversion."""
        # 1.5 AVAX -> 1.5 * 10^18 Wei
        res = Utils.unit_conversion(1.5, 18, to_base=True)
        assert res == 1500000000000000000
        assert isinstance(res, int)

    def test_unit_conversion_to_display(self):
        """Test Base -> Display conversion."""
        # 1.5 * 10^18 Wei -> 1.5 AVAX
        res = Utils.unit_conversion(1500000000000000000, 18, to_base=False)
        assert res == 1.5
        assert isinstance(res, float)

    def test_unit_conversion_zero(self):
        """Test zero conversion."""
        assert Utils.unit_conversion(0, 18, to_base=True) == 0
        assert Utils.unit_conversion(0, 18, to_base=False) == 0.0

    def test_unit_conversion_decimals(self):
        """Test different decimals (e.g. USDC 6 decimals)."""
        # 100 USDC -> 100 * 10^6
        res = Utils.unit_conversion(100, 6, to_base=True)
        assert res == 100000000

        # 100000000 -> 100.0
        res = Utils.unit_conversion(100000000, 6, to_base=False)
        assert res == 100.0

    def test_unit_conversion_precision(self):
        """Test precision handling."""
        # 1.123456789012345678 -> Wei
        val = 1.123456789012345678
        Utils.unit_conversion(val, 18, to_base=True)
        # Float precision might lose data, but Decimal should handle string input better.
        # Utils uses str(amount) which converts float to string.
        # Python float has ~15-17 digits precision.
        # 1.123456789012345678 might be truncated when passed as float.
        # Let's pass as string to verify Decimal handling if Utils supported it directly,
        # but Utils type hint says float/int.
        # However, `Decimal(str(amount))` is used.

        # If we pass string "1.123456789012345678", it should be exact.
        res_str = Utils.unit_conversion("1.123456789012345678", 18, to_base=True)
        assert res_str == 1123456789012345678
