# Monitor Desktop

Monitor Desktop is a Linux-first Qt application for using a larger desktop
display as a field monitor and control surface for Sony cameras. It also runs
on macOS and Windows when the listed dependencies and a supported camera
transport are available.

The app is intentionally local-first: video, LUT previews, scopes, monitor
recordings, and still-frame exports stay on the operator's computer.

## What is working

- Live monitoring from UVC/HDMI capture cards, webcams, local video files,
  RTSP streams, HTTP streams, and MJPEG streams that OpenCV can open.
- Monitor assists: zebra, false color, focus peaking, frame guides, mirroring,
  anamorphic desqueeze, `.cube` LUT preview, histogram, waveform, and
  vectorscope.
- Local monitor-feed recording and PNG frame export under `recordings/`.
- Sony control with a real transport selected per camera:

| Transport | Best for | Live view | Controls |
| --- | --- | --- | --- |
| UVC / HDMI capture | Any camera with clean HDMI | Yes | Camera-side only |
| Sony Wi-Fi Remote API | Older compatible Sony cameras on their Wi-Fi network | Yes | Focus, still, movie, exposure settings when supported by camera |
| `gphoto2` USB | Linux USB tethering and supported Sony Alpha cameras | Yes | Still capture, focus, settings, and model-dependent movie control |
| Camera Remote SDK server | Current Sony models supported by Sony's SDK | Yes | SDK-server capabilities |

The controls are deliberately disabled until a backend has connected. Camera
features vary by model and firmware; the application reports a transport error
instead of presenting a control as successfully applied when it was not.

## Install and run

```bash
./scripts/bootstrap.sh
make run
```

The bootstrap script creates a project-local `.venv` and installs all Python
dependencies. No global Python packages are needed.

To open a capture card, enter `0` for the first device. On Linux, a capture
card can also be entered as a device path such as `/dev/video0`. Use the source
selector to enter an RTSP URL or open a video file.

## Sony setup

### Sony Wi-Fi Remote API

1. Turn on the camera's remote-control application and join this computer to
   the camera's Wi-Fi network.
2. Choose `Sony Wi-Fi Remote API` in the app and press `Discover`.
3. If discovery is unavailable on the network, enter the camera IP address and
   press `Connect camera`.

This is Sony's earlier JSON-RPC remote API. It is useful for compatible older
models, but it is not Sony's current desktop SDK.

### gphoto2 USB on Linux

Install [libgphoto2 / gphoto2](https://github.com/gphoto/libgphoto2) using your
distribution's package manager, put the camera in its PC Remote USB mode, then
choose `gphoto2 USB` and press `Discover`. Monitor Desktop invokes the existing
`gphoto2` command-line client rather than reimplementing its USB/PTP driver.

For example, on Debian or Ubuntu:

```bash
sudo apt install gphoto2
```

Run `gphoto2 --auto-detect` in a terminal if discovery does not show the camera.
Not every Sony body exposes live view or movie recording through `gphoto2`.

### Current Sony Camera Remote SDK

Sony's current supported desktop-control path is the
[Camera Remote SDK](https://support.d-imaging.sony.co.jp/app/sdk/en/index.html).
Its binary and license are not bundled here. Start a compatible local REST
server backed by the SDK, select `Camera Remote SDK server`, enter its URL
(default `http://127.0.0.1:8080`), discover the camera, and connect.

The adapter expects these routes:

```text
GET  /api/cameras
POST /api/cameras/{id}/actions/live-view
POST /api/cameras/{id}/actions/half-press
POST /api/cameras/{id}/actions/release-half-press
POST /api/cameras/{id}/actions/shutter
POST /api/cameras/{id}/actions/movie-record
PUT  /api/cameras/{id}/properties/{name}
```

## Development

```bash
make check
```

This compiles the application and runs the unit tests. The GitHub Actions
workflow runs the same checks on supported Python versions.

## Project status

This is an early usable foundation, not a claim of feature parity with every
generation of Monitor+ or every Sony body. The next high-value work is tested
integration against the target camera model, click-to-focus coordinate mapping,
media browsing and transfer, audio capture, and packaging native installers.

## License

Monitor Desktop is released under the [MIT License](LICENSE). `gphoto2` and the
Sony Camera Remote SDK are separate projects with their own licenses.
