"""Unit tests for pure camera thermal-state interpretation."""

from __future__ import annotations

import unittest

from monitor_desktop.thermal import (
    CAUTION_CELSIUS,
    CRITICAL_CELSIUS,
    STATE_CAUTION,
    STATE_CRITICAL,
    STATE_OK,
    STATE_UNAVAILABLE,
    estimate_heat_state,
    evaluate_telemetry,
    parse_battery_percent,
    parse_celsius,
    state_from_overheat,
)


class ParseCelsiusTests(unittest.TestCase):
    def test_accepts_plain_and_decorated_readings(self) -> None:
        self.assertEqual(parse_celsius("42"), 42.0)
        self.assertEqual(parse_celsius("42.5"), 42.5)
        self.assertEqual(parse_celsius("42 C"), 42.0)
        self.assertEqual(parse_celsius("42°C"), 42.0)
        self.assertEqual(parse_celsius("Temperature: 42"), 42.0)

    def test_rejects_missing_or_non_numeric(self) -> None:
        self.assertIsNone(parse_celsius(None))
        self.assertIsNone(parse_celsius(""))
        self.assertIsNone(parse_celsius("   "))
        self.assertIsNone(parse_celsius("no sensor here"))

    def test_rejects_out_of_range_values(self) -> None:
        self.assertIsNone(parse_celsius("-5"))
        self.assertIsNone(parse_celsius("121"))
        self.assertIsNone(parse_celsius("999"))

    def test_never_raises_for_non_string_input(self) -> None:
        self.assertIsNone(parse_celsius(42))  # type: ignore[arg-type]
        self.assertIsNone(parse_celsius(42.5))  # type: ignore[arg-type]
        self.assertIsNone(parse_celsius(object()))  # type: ignore[arg-type]
        self.assertIsNone(parse_celsius(["42"]))  # type: ignore[arg-type]

    def test_takes_first_numeric_token(self) -> None:
        self.assertEqual(parse_celsius("42 then 99"), 42.0)


class ParseBatteryPercentTests(unittest.TestCase):
    def test_accepts_plain_and_percent_forms(self) -> None:
        self.assertEqual(parse_battery_percent("62"), 62)
        self.assertEqual(parse_battery_percent("62%"), 62)
        self.assertEqual(parse_battery_percent("62 %"), 62)

    def test_negative_means_unknown(self) -> None:
        self.assertIsNone(parse_battery_percent("-1"))
        self.assertIsNone(parse_battery_percent("-5"))

    def test_clamps_large_values(self) -> None:
        self.assertEqual(parse_battery_percent("150"), 100)
        self.assertEqual(parse_battery_percent("1000%"), 100)

    def test_rejects_missing_or_non_numeric(self) -> None:
        self.assertIsNone(parse_battery_percent(None))
        self.assertIsNone(parse_battery_percent(""))
        self.assertIsNone(parse_battery_percent("unknown"))

    def test_never_raises_for_non_string_input(self) -> None:
        self.assertIsNone(parse_battery_percent(62))  # type: ignore[arg-type]
        self.assertIsNone(parse_battery_percent(object()))  # type: ignore[arg-type]


class StateFromOverheatTests(unittest.TestCase):
    def test_maps_documented_ordering(self) -> None:
        self.assertEqual(state_from_overheat("0"), STATE_OK)
        self.assertEqual(state_from_overheat("1"), STATE_CAUTION)
        self.assertEqual(state_from_overheat("2"), STATE_CRITICAL)
        self.assertEqual(state_from_overheat("3"), STATE_CRITICAL)

    def test_rejects_unknown_values(self) -> None:
        self.assertIsNone(state_from_overheat(None))
        self.assertIsNone(state_from_overheat(""))
        self.assertIsNone(state_from_overheat(" nominal "))
        self.assertIsNone(state_from_overheat("-1"))


class EstimateHeatStateTests(unittest.TestCase):
    def test_live_view_thresholds(self) -> None:
        state, detail = estimate_heat_state(
            streaming_seconds=5 * 60, recording=False, source_kind="live_view"
        )
        self.assertEqual(state, STATE_OK)
        self.assertIn("5 min", detail)
        state, _ = estimate_heat_state(
            streaming_seconds=22 * 60, recording=False, source_kind="live_view"
        )
        self.assertEqual(state, STATE_CAUTION)
        state, _ = estimate_heat_state(
            streaming_seconds=50 * 60, recording=False, source_kind="live_view"
        )
        self.assertEqual(state, STATE_CRITICAL)

    def test_recording_uses_shorter_thresholds(self) -> None:
        state, detail = estimate_heat_state(
            streaming_seconds=12 * 60, recording=True, source_kind="live_view"
        )
        self.assertEqual(state, STATE_CAUTION)
        self.assertIn("12 min", detail)
        state, _ = estimate_heat_state(
            streaming_seconds=30 * 60, recording=True, source_kind="live_view"
        )
        self.assertEqual(state, STATE_CRITICAL)

    def test_file_sources_never_warn(self) -> None:
        state, _ = estimate_heat_state(
            streaming_seconds=10 * 60 * 60, recording=True, source_kind="file"
        )
        self.assertEqual(state, STATE_OK)

    def test_negative_elapsed_is_treated_as_zero(self) -> None:
        state, detail = estimate_heat_state(
            streaming_seconds=-60, recording=False, source_kind="live_view"
        )
        self.assertEqual(state, STATE_OK)
        self.assertIn("0 min", detail)


class EvaluateTelemetryTests(unittest.TestCase):
    def test_camera_critical_beats_duration_estimate(self) -> None:
        reading = evaluate_telemetry(
            temperature_raw=f"{CRITICAL_CELSIUS + 5:g}",
            streaming_seconds=60,
            source_kind="live_view",
        )
        self.assertEqual(reading.state, STATE_CRITICAL)
        self.assertEqual(reading.source, "camera")
        self.assertIsNotNone(reading.celsius)

    def test_camera_caution_reading_wins(self) -> None:
        reading = evaluate_telemetry(
            temperature_raw=f"{CAUTION_CELSIUS + 1:g}",
            streaming_seconds=60,
            source_kind="live_view",
        )
        self.assertEqual(reading.state, STATE_CAUTION)
        self.assertEqual(reading.source, "camera")

    def test_low_reading_with_long_stream_escalates(self) -> None:
        reading = evaluate_telemetry(
            temperature_raw="30",
            streaming_seconds=50 * 60,
            source_kind="live_view",
        )
        self.assertEqual(reading.state, STATE_CRITICAL)
        self.assertEqual(reading.source, "camera")
        self.assertEqual(reading.celsius, 30.0)

    def test_no_telemetry_with_live_view_reports_estimate(self) -> None:
        reading = evaluate_telemetry(streaming_seconds=22 * 60, source_kind="live_view")
        self.assertEqual(reading.state, STATE_CAUTION)
        self.assertEqual(reading.source, "estimate")
        self.assertIn("22 min", reading.headline)

    def test_file_without_telemetry_is_unavailable(self) -> None:
        reading = evaluate_telemetry(streaming_seconds=0, source_kind="file")
        self.assertEqual(reading.state, STATE_UNAVAILABLE)
        self.assertEqual(reading.source, "none")
        self.assertEqual(reading.headline, "Temperature unavailable")

    def test_battery_is_populated_independently_of_state(self) -> None:
        reading = evaluate_telemetry(battery_raw="62%", source_kind="file")
        self.assertEqual(reading.battery_percent, 62)
        reading = evaluate_telemetry(
            temperature_raw="70", battery_raw="62%", source_kind="live_view"
        )
        self.assertEqual(reading.battery_percent, 62)
        self.assertEqual(reading.state, STATE_CRITICAL)

    def test_overheat_flag_drives_state_without_celsius(self) -> None:
        reading = evaluate_telemetry(overheat_raw="2", source_kind="live_view")
        self.assertEqual(reading.state, STATE_CRITICAL)
        self.assertEqual(reading.source, "camera")
        self.assertIsNone(reading.celsius)


if __name__ == "__main__":
    unittest.main()
