"""Dims devices driven through OpenRGB, keeping the colors you set up there.

Talks to OpenRGB's SDK server (the OpenRGB Windows service listens on 127.0.0.1:6742).

How the look is kept: OpenRGB devices in Direct mode just hold whatever per-LED colors were last
sent. The bridge remembers a "base look" (the colors as you set them in OpenRGB) and, for every
brightness change, sends base x brightness. Set your colors in OpenRGB, then capture them once
(POST /capture, or the button on the test page); the base is saved to base_colors.json. Only
static colors / gradients survive this: an animated OpenRGB effect would be frozen.

Why two connections: OpenRGB 1.0's server ignores colour writes from protocol <= 5 clients
(which is all openrgb-python can speak); it addresses controllers by unique IDs that only
protocol 6 hands out, and those IDs change whenever OpenRGB re-detects hardware. So:
  * openrgb-python (protocol 3) only READS: device names, LED counts, current colors;
  * a small protocol-6 connection (_V6) WRITES the colors, looking the IDs up before every
    write. Device i in the old list is controller ids[i] in the new one (same order).

Writes happen on a worker thread and only the latest brightness is kept, so a slow or sleeping
device (a wireless mouse) or an OpenRGB restart never delays the HTTP API or the iCUE output.
Devices must be in a per-LED mode (Direct, or a per-LED Static): OpenRGB's default.
"""
import json
import logging
import socket
import struct
import threading
from pathlib import Path

from openrgb import OpenRGBClient
from openrgb.utils import ModeColors

log = logging.getLogger("bridge.openrgb")

RECONNECT_SECONDS = 10.0  # while OpenRGB is unreachable
RESCAN_SECONDS = 60.0     # while some wanted devices were not present (e.g. mouse asleep)

# SDK packet ids (OpenRGB Documentation/OpenRGBSDK.md)
_REQUEST_CONTROLLER_COUNT, _REQUEST_PROTOCOL_VERSION, _SET_CLIENT_NAME = 0, 40, 50
_RGBCONTROLLER_UPDATELEDS = 1050
_PROTOCOL = 6


class _V6:
    """Minimal protocol-6 SDK connection: list controller IDs and update LED colours."""

    def __init__(self, address: str, port: int, name: str = "icue-bridge"):
        self._sock = socket.create_connection((address, port), timeout=10)
        self._send(0, _SET_CLIENT_NAME, name.encode() + b"\0")
        self._send(0, _REQUEST_PROTOCOL_VERSION, struct.pack("<I", _PROTOCOL))
        server_version = struct.unpack("<I", self._expect(_REQUEST_PROTOCOL_VERSION))[0]
        if server_version < _PROTOCOL:
            raise RuntimeError(f"OpenRGB server speaks protocol {server_version}; need {_PROTOCOL}+")

    def _send(self, device: int, packet: int, payload: bytes = b""):
        self._sock.sendall(b"ORGB" + struct.pack("<III", device, packet, len(payload)) + payload)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("OpenRGB closed the connection")
            buf += chunk
        return buf

    def _expect(self, wanted: int) -> bytes:
        while True:  # the server also pushes unrelated packets (e.g. device list updates)
            magic, _, packet, size = struct.unpack("<4sIII", self._recv_exact(16))
            if magic != b"ORGB":
                raise ConnectionError("not an OpenRGB SDK stream")
            data = self._recv_exact(size)
            if packet == wanted:
                return data

    def ids(self) -> list[int]:
        self._send(0, _REQUEST_CONTROLLER_COUNT)
        data = self._expect(_REQUEST_CONTROLLER_COUNT)
        count = struct.unpack_from("<I", data, 0)[0]
        return list(struct.unpack_from(f"<{count}I", data, 4))

    def set_leds(self, controller_id: int, colors):
        body = struct.pack("<H", len(colors)) + b"".join(bytes((r, g, b, 0)) for r, g, b in colors)
        self._send(controller_id, _RGBCONTROLLER_UPDATELEDS, struct.pack("<I", len(body) + 4) + body)

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


class _Target:
    """One OpenRGB device we dim: its position in the server's list and its LED count."""

    def __init__(self, index: int, device):
        self.index = index
        self.name = device.name
        self.key = device.name.lower()
        self.led_count = len(device.leds)
        mode = device.modes[device.active_mode]
        self.per_led = mode.color_mode == ModeColors.PER_LED
        self.mode_name = mode.name


def _colors_of(device) -> list[tuple]:
    return [(c.red, c.green, c.blue) for c in device.colors]


class OpenRgbOutput:
    def __init__(self, address: str, port: int, device_names, base_path: Path):
        self._address = address
        self._port = port
        self._wanted = {name.lower() for name in device_names}
        self._base_path = base_path

        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()  # guards _scale, _base and _last_written
        self._scale: float | None = None  # newest brightness 0..1; None until first apply
        self._base: dict[str, list[tuple]] = self._load_base()
        self._last_written: dict[str, list[tuple]] = {}
        self._snapshot: dict[str, list[tuple]] = {}  # colors seen when we last read the devices
        self._conn: _V6 | None = None
        self._ids: list[int] = []
        self._targets: list[_Target] = []
        self._missing: set[str] = set()
        self._last_summary = None
        self._warned = False
        self._thread: threading.Thread | None = None

    # ----- lifecycle -------------------------------------------------------------------

    def start(self):
        self._thread = threading.Thread(target=self._run, name="openrgb", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._drop()

    def apply(self, scale: float):
        """Called by the controller on every change; returns immediately."""
        with self._lock:
            self._scale = scale
        self._wake.set()

    def capture(self) -> str:
        """Adopt the colors currently shown by the OpenRGB devices as the base look."""
        current = self._read_colors()
        if not current:
            return "OpenRGB: no matching devices found"
        notes = []
        with self._lock:
            for key, (name, colors) in current.items():
                if self._last_written.get(key) == colors:
                    notes.append(f"{name}: unchanged since the bridge last wrote it "
                                 "(set the colors in OpenRGB first)")
                    continue
                self._base[key] = colors
                self._last_written.pop(key, None)
                notes.append(f"{name}: captured")
            self._save_base()
        self._wake.set()  # re-apply the current brightness on top of the new base
        return "OpenRGB: " + "; ".join(notes)

    # ----- worker ----------------------------------------------------------------------

    def _run(self):
        while not self._stop.is_set():
            # Sleep until there is news (a brightness change, a capture). While disconnected,
            # or while some wanted device is missing, also wake up now and then to look again.
            timeout = None
            if self._conn is None:
                timeout = RECONNECT_SECONDS
            elif self._missing:
                timeout = RESCAN_SECONDS
            notified = self._wake.wait(timeout)
            self._wake.clear()
            if self._stop.is_set():
                break
            with self._lock:
                scale = self._scale
            if scale is None:
                continue
            try:
                if self._conn is None:
                    self._connect()
                    self._write(scale)  # fresh connection: bring the devices up to date
                elif notified:
                    self._write(scale)
                else:
                    self._rescan(scale)  # timed out: only look for devices that were missing
                self._warned = False
            except Exception as e:
                if not self._warned:
                    log.warning("OpenRGB output failed (%s: %s); will retry", type(e).__name__, e)
                    self._warned = True
                self._drop()

    def _connect(self):
        self._conn = _V6(self._address, self._port)
        self._refresh_targets()

    def _reader(self) -> OpenRGBClient:
        # protocol 3: older protocols have no plugin list to wait for, which otherwise costs a
        # 10 s timeout per connection on OpenRGB 1.0
        return OpenRGBClient(address=self._address, port=self._port, name="icue-bridge-read",
                             protocol_version=3)

    def _read_colors(self) -> dict:
        """{key: (name, [(r,g,b)...])} for the wanted devices that are present right now."""
        reader = self._reader()
        try:
            return {d.name.lower(): (d.name, _colors_of(d)) for d in reader.devices
                    if d.name.lower() in self._wanted and d.leds}
        finally:
            try:
                reader.disconnect()
            except Exception:
                pass

    def _refresh_targets(self):
        """(Re)read device details. Called on connect and whenever the controller IDs change."""
        ids = self._conn.ids()
        reader = self._reader()
        try:
            devices = list(reader.devices)
            if len(devices) != len(ids):
                raise RuntimeError(f"device list ({len(devices)}) and controller IDs ({len(ids)}) disagree")
            targets = [_Target(i, d) for i, d in enumerate(devices)
                       if d.name.lower() in self._wanted and d.leds]
            self._snapshot = {d.name.lower(): _colors_of(d) for d in devices
                              if d.name.lower() in self._wanted and d.leds}
        finally:
            try:
                reader.disconnect()
            except Exception:
                pass
        for t in targets:
            if not t.per_led:
                log.warning("OpenRGB: %s is in mode '%s' which is not per-LED; set it to Direct "
                            "in OpenRGB or colours will be ignored", t.name, t.mode_name)
        found = {t.key for t in targets}
        self._missing = self._wanted - found
        self._ids, self._targets = ids, targets
        summary = (", ".join(t.name for t in targets) or "nothing",
                   ", ".join(sorted(self._missing)))
        if summary != self._last_summary:  # the periodic rescan would otherwise repeat this
            log.info("OpenRGB: dimming %s", summary[0])
            if summary[1]:
                log.info("OpenRGB: not present right now: %s", summary[1])
            self._last_summary = summary

    def _drop(self):
        conn, self._conn, self._targets, self._ids = self._conn, None, [], []
        if conn is not None:
            conn.close()

    def _rescan(self, scale: float):
        """Look for wanted devices that were missing. Never touches devices that are already
        working: rewriting them here would overwrite colors you just set in OpenRGB."""
        before = {t.key for t in self._targets}
        self._refresh_targets()
        arrived = {t.key for t in self._targets} - before
        if arrived:
            log.info("OpenRGB: now present: %s", ", ".join(sorted(arrived)))
            self._write(scale, only=arrived)

    def _adopt_edits(self):
        """If a device no longer shows what we last wrote, you changed it in OpenRGB: take its
        colors as the new look (assumed to be set at full brightness), so brightness changes
        keep your colors instead of restoring the old look."""
        with self._lock:
            if not self._last_written:
                return
            last = dict(self._last_written)
        current = self._read_colors()
        with self._lock:
            for key, (name, colors) in current.items():
                if key in last and colors != last[key]:
                    self._base[key] = colors
                    self._last_written.pop(key, None)
                    log.info("OpenRGB: %s was changed in OpenRGB; using its colors as the new look", name)
            self._save_base()

    def _write(self, scale: float, only=None):
        ids = self._conn.ids()  # cheap, and the IDs change whenever OpenRGB re-detects hardware
        if ids != self._ids:
            log.info("OpenRGB: controller IDs changed; re-reading devices")
            self._refresh_targets()
            ids = self._ids
            with self._lock:
                # a re-detection resets device colors: that is not an edit of yours
                self._last_written.clear()
        elif only is None:
            self._adopt_edits()
        for target in self._targets:
            if only is not None and target.key not in only:
                continue
            with self._lock:
                base = self._base.get(target.key)
                if base is None or len(base) != target.led_count:
                    # first run for this device: take the look it has right now
                    base = self._snapshot.get(target.key)
                    if base is None:
                        continue
                    self._base[target.key] = base
                    self._save_base()
                    log.info("OpenRGB: no saved look for %s; using its current colors", target.name)
            colors = [tuple(round(c * scale) for c in rgb) for rgb in base]
            self._conn.set_leds(ids[target.index], colors)
            with self._lock:
                self._last_written[target.key] = colors

    # ----- persistence -----------------------------------------------------------------

    def _load_base(self) -> dict:
        try:
            raw = json.loads(self._base_path.read_text(encoding="utf-8-sig"))
            return {k: [tuple(c) for c in v] for k, v in raw.items()}
        except (OSError, ValueError):
            return {}

    def _save_base(self):
        try:
            self._base_path.write_text(json.dumps(self._base), encoding="utf-8")
        except OSError as e:
            log.warning("could not save %s: %s", self._base_path.name, e)
