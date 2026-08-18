from __future__ import annotations

import unittest

from monitor_desktop.backends import GPhotoBackend, SonyRemoteApiBackend


class SonyRemoteApiBackendTests(unittest.TestCase):
    def test_normalises_camera_endpoint(self) -> None:
        self.assertEqual(
            SonyRemoteApiBackend._normalise_endpoint("192.168.122.1"),
            "http://192.168.122.1/sony/camera",
        )
        self.assertEqual(
            SonyRemoteApiBackend._normalise_endpoint("http://camera.local:10000/sony"),
            "http://camera.local:10000/sony/camera",
        )
        self.assertEqual(
            SonyRemoteApiBackend._normalise_endpoint("http://camera.local/sony/camera"),
            "http://camera.local/sony/camera",
        )

    def test_parses_ssdp_headers_case_insensitively(self) -> None:
        headers = SonyRemoteApiBackend._parse_ssdp_headers(
            "HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.1/dd.xml\r\nST: sony\r\n\r\n"
        )
        self.assertEqual(headers["location"], "http://192.168.1.1/dd.xml")
        self.assertEqual(headers["st"], "sony")

    def test_flattens_available_values(self) -> None:
        values = SonyRemoteApiBackend._flatten([["Auto", ["100", "200"]], "400"])
        self.assertEqual(values, ["Auto", "100", "200", "400"])

    def test_zoom_actions_use_sony_remote_api_act_zoom(self) -> None:
        backend = SonyRemoteApiBackend("192.168.122.1")
        calls: list[tuple[str, list[str]]] = []
        backend._call = lambda method, params: calls.append((method, params)) or {}  # type: ignore[method-assign]

        backend.action("zoom_in")
        backend.action("zoom_stop")

        self.assertEqual(calls, [("actZoom", ["in", "start"]), ("actZoom", ["in", "stop"])])


class GPhotoBackendTests(unittest.TestCase):
    def test_uses_camera_config_widget_names(self) -> None:
        self.assertEqual(GPhotoBackend._setting_widgets["iso"], "iso")
        self.assertEqual(GPhotoBackend._setting_widgets["shutter"], "shutterspeed")
        self.assertEqual(GPhotoBackend._setting_widgets["white_balance"], "whitebalance")


if __name__ == "__main__":
    unittest.main()
