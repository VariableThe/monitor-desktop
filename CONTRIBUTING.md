# Contributing

## Development setup

Run `./scripts/bootstrap.sh`, then use `make run` to launch the application and
`make check` before opening a pull request.

## Changes

- Keep camera transport code isolated in `monitor_desktop/backends.py`.
- Add a focused test for protocol parsing and image processing changes.
- Do not claim a camera feature is supported unless the active backend exposes it.
- Keep Sony SDK binaries out of the repository. They are separately licensed by Sony.

## Pull requests

Use a concise title, explain the camera model and transport used for testing,
and include a screenshot for user-interface changes.
