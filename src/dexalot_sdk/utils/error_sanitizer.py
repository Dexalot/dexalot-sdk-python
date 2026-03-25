"""Error sanitization utilities to prevent sensitive information leaks."""

import re


def sanitize_error_message(error: Exception | str, context: str = "") -> str:
    r"""Sanitize an error message to remove sensitive information.

    This function removes:
    - File paths (absolute paths like /Users/..., /home/..., C:\...)
    - URLs (http://, https://, ws://, wss://)
    - Stack trace lines (lines starting with File, Traceback, etc.)
    - Internal state information

    Args:
        error: The exception or error string to sanitize
        context: Optional context string to include in the error message

    Returns:
        A sanitized, user-safe error message
    """
    if isinstance(error, Exception):
        error_str = str(error)
        error_type = type(error).__name__
    else:
        error_str = error
        error_type = "Error"

    # Remove file paths (absolute paths)
    error_str = re.sub(r"/[^\s]+/(?:Users|home|tmp|var|opt|usr|mnt|C:)[^\s]*", "[path]", error_str)
    error_str = re.sub(r"C:\\[^\s]+", "[path]", error_str)

    # Remove URLs (http, https, ws, wss)
    error_str = re.sub(r"https?://[^\s]+", "[url]", error_str)
    error_str = re.sub(r"wss?://[^\s]+", "[url]", error_str)

    # Remove stack trace lines
    lines = error_str.split("\n")
    sanitized_lines = []
    in_traceback = False
    for line in lines:
        stripped = line.strip()
        # Skip traceback header
        if stripped.startswith("Traceback"):
            in_traceback = True
            continue
        # Skip file lines in traceback
        if in_traceback and (
            stripped.startswith("File") or stripped.startswith('"') or stripped.startswith("'")
        ):
            continue
        # Reset traceback flag when we hit non-traceback content
        if in_traceback and not (
            stripped.startswith("File") or stripped.startswith('"') or stripped.startswith("'")
        ):
            in_traceback = False
        sanitized_lines.append(line)
    error_str = "\n".join(sanitized_lines)

    # Remove any remaining file path patterns
    error_str = re.sub(r'File "[^"]+", line \d+', "[file]", error_str)

    # Map common exception types to user-friendly messages
    if error_type == "ConnectionError":
        base_message = "Network connection failed"
    elif error_type == "TimeoutError":
        base_message = "Request timed out"
    elif error_type == "ValueError":
        base_message = "Invalid input parameter"
    elif error_type == "KeyError":
        base_message = "Required data not found"
    elif error_type == "AttributeError":
        base_message = "Invalid operation"
    elif error_type == "TypeError":
        base_message = "Type mismatch"
    else:
        # Use the sanitized error string, but limit length
        base_message = error_str[:200] if len(error_str) > 200 else error_str

    # Clean up the message
    base_message = base_message.strip()
    if not base_message:
        base_message = "An unexpected error occurred"

    # Add context if provided
    if context:
        return f"{context}: {base_message}"
    return base_message


def extract_user_message(error: Exception) -> str:
    """Extract a user-friendly message from an exception.

    This is a simpler version that just extracts the main error message
    without full sanitization. Useful for known exception types.

    Args:
        error: The exception to extract message from

    Returns:
        A user-friendly error message
    """
    error_type = type(error).__name__
    error_str = str(error)

    # For specific exception types, provide cleaner messages
    if error_type == "ConnectionError":
        return "Network connection failed"
    elif error_type == "TimeoutError":
        return "Request timed out"
    elif error_type == "ValueError":
        return f"Invalid value: {error_str}"
    elif error_type == "KeyError":
        return f"Missing required key: {error_str}"
    else:
        # Return first line of error message (usually the most relevant)
        return error_str.split("\n")[0].strip()
