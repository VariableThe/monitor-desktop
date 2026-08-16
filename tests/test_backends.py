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


class GPhotoBackendTests(unittest.TestCase):
    def test_builds_port_scoped_command(self) -> None:
        backend = GPhotoBackend()
        backend.port = "usb:001,004"
        self.assertEqual(
            backend._command(["--summary"]),
            ["gphoto2", "--port", "usb:001,004", "--summary"],
        )


if __name__ == "__main__":
    unittest.main()
