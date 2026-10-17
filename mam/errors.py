"""Shared exception types."""


class MamError(Exception):
    """Base class for expected, user-reportable errors."""


class ReadOnlyViolation(MamError):
    """Raised when code tries to write below a folder that holds originals."""


class CorruptedMedia(MamError):
    """Raised when a media file cannot be probed or decoded."""


class BaserowError(MamError):
    """Raised when Baserow rejects a request or a write fails validation."""
