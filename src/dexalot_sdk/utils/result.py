"""Result type for standardized error handling across the SDK."""

from dataclasses import dataclass


@dataclass
class Result[T]:
    """Standardized result type for SDK methods.

    This type provides a consistent way to handle success and operational
    failures across the async SDK methods that return ``Result``. Some helper
    methods still return raw values or raise on programmer/configuration errors.

    Attributes:
        success: True if the operation succeeded, False otherwise
        data: The result data on success (None on error)
        error: The error message on failure (None on success)

    Example:
        >>> result = await client.add_order(...)
        >>> if result.success:
        ...     print(f"Order placed: {result.data['tx_hash']}")
        ... else:
        ...     print(f"Error: {result.error}")
    """

    success: bool
    data: T | None = None
    error: str | None = None

    @classmethod
    def ok(cls, data: T) -> "Result[T]":
        """Create a successful result with data.

        Args:
            data: The result data

        Returns:
            Result with success=True and data set
        """
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error_msg: str) -> "Result[T]":
        """Create an error result with message.

        Args:
            error_msg: The error message

        Returns:
            Result with success=False and error set
        """
        return cls(success=False, error=error_msg)

    def __bool__(self) -> bool:
        """Allow Result to be used in boolean contexts."""
        return self.success
