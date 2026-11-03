class Blocked(Exception):
    """A required capability/measurement is not available. The run is recorded BLOCKED, never faked."""


class Refused(Exception):
    """A safety rule forbids the requested action (personal profile, existing registration, ...)."""
