"""Camera thermal-state interpretation for viewfindrr.

Why most bodies report "unavailable" and why a duration-based estimate exists:

- Upstream libgphoto2 ``camlibs/ptp2/ptp.h`` defines
  ``PTP_DPC_SONY_DeviceOverheatStatus`` (``0xD251``), but
  ``camlibs/ptp2/config.c`` has no overheat entries (the widget table never
  exposes it), and the per-body property dumps for the a7m4/a7r5/a6700/a7c
  contain no ``0xd251``. Sony also exposes no per-degree-Celsius sensor
  temperature property.
- Therefore per-body camera temperature is usually UNAVAILABLE, while
  ``batterylevel`` (``0xd218``, widget name ``batterylevel``) IS exposed.
- Because the transport rarely yields a real temperature, this module pairs
  best-effort parsing of whatever the transport returns with a
  duration-based heat estimate so long live-view streams still warn.

This module is pure interpretation logic: no Qt, no I/O, no camera access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STATE_OK = "ok"
STATE_CAUTION = "caution"
STATE_CRITICAL = "critical"
STATE_UNAVAILABLE = "unavailable"

LIVE_VIEW_CAUTION_SECONDS = 20 * 60
LIVE_VIEW_CRITICAL_SECONDS = 45 * 60
RECORDING_CAUTION_SECONDS = 10 * 60
RECORDING_CRITICAL_SECONDS = 25 * 60

CAUTION_CELSIUS = 50.0
CRITICAL_CELSIUS = 60.0
PLAUSIBLE_CELSIUS_MIN = 0.0
PLAUSIBLE_CELSIUS_MAX = 120.0

_STATES_RANK = {STATE_OK: 0, STATE_CAUTION: 1, STATE_CRITICAL: 2}

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_INTEGER_RE = re.compile(r"[-+]?\d+")


@dataclass(frozen=True)
class ThermalReading:
    state: str
    celsius: float | None
    battery_percent: int | None
    source: str
    headline: str
    detail: str


def parse_celsius(raw: str | None) -> float | None:
    """Parse a best-effort temperature in degrees Celsius.

    Takes the first numeric token (``"Temperature: 42"`` -> ``42.0``) and
    rejects anything outside ``[PLAUSIBLE_CELSIUS_MIN, PLAUSIBLE_CELSIUS_MAX]``.
    Never raises: non-string input, empty text, and non-numeric text all
    return ``None``.
    """
    if not isinstance(raw, str):
        return None
    try:
        match = _NUMBER_RE.search(raw)
        if match is None:
            return None
        value = float(match.group(0))
    except (ValueError, OverflowError):
        return None
    if not PLAUSIBLE_CELSIUS_MIN <= value <= PLAUSIBLE_CELSIUS_MAX:
        return None
    return value


def parse_battery_percent(raw: str | None) -> int | None:
    """Parse a battery level as ``0..100``.

    Accepts ``"62"``, ``"62%"`` and ``"62 %"``; clamps out-of-range values
    into ``0..100``; maps Sony's negative "unknown" (``-1``) to ``None``.
    Never raises.
    """
    if not isinstance(raw, str):
        return None
    try:
        match = _NUMBER_RE.search(raw)
        if match is None:
            return None
        value = float(match.group(0))
    except (ValueError, OverflowError):
        return None
    if value < 0:
        return None
    return int(min(max(round(value), 0), 100))


def state_from_overheat(raw: str | None) -> str | None:
    """Map a raw overheat-status value to a thermal state.

    The D251 enum values are undocumented upstream, so only the ordering
    assumption is used: ``0`` is nominal, ``1`` is elevated, and ``>= 2``
    is severe. Anything else (non-numeric, negative, ``None``) is ``None``.
    """
    if not isinstance(raw, str):
        return None
    try:
        match = _INTEGER_RE.search(raw)
        if match is None:
            return None
        value = int(match.group(0))
    except (ValueError, OverflowError):
        return None
    if value == 0:
        return STATE_OK
    if value == 1:
        return STATE_CAUTION
    if value >= 2:
        return STATE_CRITICAL
    return None


def estimate_heat_state(
    *, streaming_seconds: float, recording: bool, source_kind: str
) -> tuple[str, str]:
    """Estimate heat state from continuous streaming duration.

    ``source_kind == "file"`` always reports ``STATE_OK`` because local file
    playback generates no camera heat. Returns ``(state, detail)`` where
    detail is one short sentence with the elapsed minutes.
    """
    try:
        elapsed = float(streaming_seconds)
    except (TypeError, ValueError):
        elapsed = 0.0
    if elapsed < 0:
        elapsed = 0.0
    if source_kind == "file":
        return (STATE_OK, "File playback generates no camera heat")
    if recording:
        caution_at, critical_at = RECORDING_CAUTION_SECONDS, RECORDING_CRITICAL_SECONDS
        kind = "recording"
    else:
        caution_at, critical_at = LIVE_VIEW_CAUTION_SECONDS, LIVE_VIEW_CRITICAL_SECONDS
        kind = "live view"
    minutes = int(elapsed // 60)
    detail = f"{minutes} min of continuous {kind}"
    if elapsed >= critical_at:
        return (STATE_CRITICAL, detail)
    if elapsed >= caution_at:
        return (STATE_CAUTION, detail)
    return (STATE_OK, detail)


def _worse(first: str, second: str) -> str:
    if _STATES_RANK.get(second, 0) > _STATES_RANK.get(first, 0):
        return second
    return first


def _cooldown_advice() -> str:
    return "Pausing the stream or letting idle-stop run reduces heat."


def evaluate_telemetry(
    *,
    temperature_raw: str | None = None,
    overheat_raw: str | None = None,
    battery_raw: str | None = None,
    streaming_seconds: float = 0.0,
    recording: bool = False,
    source_kind: str = "live_view",
) -> ThermalReading:
    """Combine camera telemetry and the duration estimate into one reading.

    Precedence, highest first: an explicit camera celsius at/above the
    thresholds wins; then the overheat flag when no celsius was parsed; then
    a low camera celsius (escalated when the duration estimate is worse);
    then the duration estimate alone; then ``STATE_UNAVAILABLE`` for file
    sources with no telemetry at all. ``battery_percent`` is always
    populated independently of state.
    """
    try:
        elapsed = float(streaming_seconds)
    except (TypeError, ValueError):
        elapsed = 0.0
    if elapsed < 0:
        elapsed = 0.0
    minutes = int(elapsed // 60)

    celsius = parse_celsius(temperature_raw)
    overheat_state = state_from_overheat(overheat_raw)
    battery = parse_battery_percent(battery_raw)
    estimate_state, estimate_detail = estimate_heat_state(
        streaming_seconds=elapsed, recording=recording, source_kind=source_kind
    )

    if celsius is not None:
        if celsius >= CRITICAL_CELSIUS:
            return ThermalReading(
                state=STATE_CRITICAL,
                celsius=celsius,
                battery_percent=battery,
                source="camera",
                headline=f"{celsius:g}°C — camera overheating",
                detail=f"Camera reports {celsius:g}°C. {_cooldown_advice()}",
            )
        if celsius >= CAUTION_CELSIUS:
            return ThermalReading(
                state=STATE_CAUTION,
                celsius=celsius,
                battery_percent=battery,
                source="camera",
                headline=f"{celsius:g}°C — camera warm",
                detail=f"Camera reports {celsius:g}°C. {_cooldown_advice()}",
            )
        escalated = _worse(STATE_OK, estimate_state)
        if escalated != STATE_OK:
            return ThermalReading(
                state=escalated,
                celsius=celsius,
                battery_percent=battery,
                source="camera",
                headline=f"{celsius:g}°C · {minutes} min stream",
                detail=f"{estimate_detail} despite a low camera reading. {_cooldown_advice()}",
            )
        return ThermalReading(
            state=STATE_OK,
            celsius=celsius,
            battery_percent=battery,
            source="camera",
            headline=f"{celsius:g}°C — temperature normal",
            detail=f"Camera reports {celsius:g}°C. {_cooldown_advice()}",
        )

    if overheat_state == STATE_CAUTION or overheat_state == STATE_CRITICAL:
        headline = (
            "Camera reports overheating"
            if overheat_state == STATE_CRITICAL
            else "Camera reports overheating risk"
        )
        return ThermalReading(
            state=overheat_state,
            celsius=None,
            battery_percent=battery,
            source="camera",
            headline=headline,
            detail=f"Camera overheat flag is set. {_cooldown_advice()}",
        )

    if overheat_state == STATE_OK:
        escalated = _worse(STATE_OK, estimate_state)
        if escalated != STATE_OK:
            return ThermalReading(
                state=escalated,
                celsius=None,
                battery_percent=battery,
                source="camera",
                headline=f"Camera nominal · {minutes} min stream",
                detail=f"{estimate_detail} despite a nominal camera flag. {_cooldown_advice()}",
            )
        return ThermalReading(
            state=STATE_OK,
            celsius=None,
            battery_percent=battery,
            source="camera",
            headline="Camera temperature normal",
            detail=f"Camera reports no overheating. {_cooldown_advice()}",
        )

    if source_kind == "file":
        return ThermalReading(
            state=STATE_UNAVAILABLE,
            celsius=None,
            battery_percent=battery,
            source="none",
            headline="Temperature unavailable",
            detail="File playback has no camera telemetry to report.",
        )

    return ThermalReading(
        state=estimate_state,
        celsius=None,
        battery_percent=battery,
        source="estimate",
        headline=f"No camera temperature · {minutes} min stream",
        detail=f"{estimate_detail.capitalize()} with no camera reading. {_cooldown_advice()}",
    )
