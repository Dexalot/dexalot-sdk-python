from decimal import Decimal

__all__ = []


class Utils:
    @staticmethod
    def to_bytes32(s: str) -> bytes:
        """Convert string to bytes32 padded with null bytes."""
        return s.encode("utf-8").ljust(32, b"\0")

    @staticmethod
    def from_bytes32(b: bytes) -> str:
        """Convert bytes32 to string stripping null bytes."""
        return b.decode("utf-8").rstrip("\0")

    @staticmethod
    def unit_conversion(amount, decimals, to_base=True):
        """
        Convert between human-readable and raw unit amounts.
        :param amount: The amount to convert (float or int)
        :param decimals: The number of decimals (int)
        :param to_base: If True, convert from Display (e.g. 1.5) to Base (e.g. 1500000000000000000).
                        If False, convert from Base to Display.
        :return: Converted amount (int for to_base=True, float for to_base=False)
        """
        if to_base:
            # Display -> Base (e.g. 1.5 AVAX -> 1.5 * 10^18 Wei)
            # Use Decimal for precision
            return int(Decimal(str(amount)) * (Decimal(10) ** decimals))
        else:
            # Base -> Display (e.g. 1500000000000000000 Wei -> 1.5 AVAX)
            return float(Decimal(str(amount)) / (Decimal(10) ** decimals))
