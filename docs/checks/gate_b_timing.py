"""Gate B timing rules (OTP-12), pure functions over supplied integer seconds.

Minute 0 is the controller-clock time at which the phone's `scheduled` log line for a push
scheduled with a 1200-second delay was read; the runbook records the offset method. Nothing
here reads a clock or sleeps: every timestamp is an argument, so a test can place an arrival
at minute 18:54 or minute 21:01 without waiting.

Stdlib only; Python 3.8 compatible.
"""
from __future__ import annotations

DELAY_SECONDS = 1200
MINUTE = 60
EARLIEST_ARRIVAL_MINUTE = 19
LATEST_ARRIVAL_MINUTE = 21
MINIMUM_LOCK_MINUTES = 15
PULL_AFTER_MINUTE = 15
BACKGROUND_MINUTES = 10
BACKGROUND_EVENT_MINUTE = 7


def _is_seconds(value):
    """A supplied time or duration: an integer, and not a bool standing in for one."""
    return isinstance(value, int) and not isinstance(value, bool)


def twenty_minute_ok(
    minute0,
    arrival,
    task_removal,
    unlock,
    pull,
    delay_seconds,
    foreground_present,
):
    """Whether a scheduled push satisfies the locked-phone 20-minute rule.

    The push must have been scheduled with a 1200-second delay, arrive within minute 19 to
    minute 21 inclusive of minute 0, be locked at task removal (which cannot precede minute 0)
    and stay locked until at least 15 minutes after it, carry the foreground indication, and be
    followed by a connector pull strictly after minute 15. A pull at minute 15 itself cannot
    observe a push that has not arrived yet.
    """
    if not all(
        _is_seconds(value)
        for value in (minute0, arrival, task_removal, unlock, pull, delay_seconds)
    ):
        return False
    if delay_seconds != DELAY_SECONDS:
        return False
    if foreground_present is not True:
        return False
    if arrival < minute0 + EARLIEST_ARRIVAL_MINUTE * MINUTE:
        return False
    if arrival > minute0 + LATEST_ARRIVAL_MINUTE * MINUTE:
        return False
    if task_removal < minute0:
        return False
    if unlock < task_removal + MINIMUM_LOCK_MINUTES * MINUTE:
        return False
    if pull <= minute0 + PULL_AFTER_MINUTE * MINUTE:
        return False
    return True


def background_ok(start, end, event, copy, other_tab_focused, exemption_active):
    """Whether a background-delivery observation satisfies the ten-minute rule.

    The interval from `start` to `end` must be at least ten continuous minutes with another
    ordinary tab focused and no audio, WebRTC or timer-throttling exemption active. The
    protected event and its offscreen copy must both fall at or after the seven-minute mark of
    the interval and inside it, with the copy at or after the event it copies.
    """
    if not all(_is_seconds(value) for value in (start, end, event, copy)):
        return False
    if other_tab_focused is not True:
        return False
    if exemption_active is not False:
        return False
    if end - start < BACKGROUND_MINUTES * MINUTE:
        return False
    earliest = start + BACKGROUND_EVENT_MINUTE * MINUTE
    if event < earliest or event > end:
        return False
    if copy < event or copy > end:
        return False
    return True
