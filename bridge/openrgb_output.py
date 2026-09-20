"""Mirrors the current scene, brightness and power onto devices driven through OpenRGB.

Talks to OpenRGB's SDK server (the OpenRGB Windows service listens on 127.0.0.1:6742).

Why two connections: OpenRGB 1.0's server ignores colour writes from protocol <= 5 clients
(which is all openrgb-python can speak); it addresses controllers by unique IDs that only
protocol 6 hands out, and those IDs change whenever OpenRGB re-detects hardware. So:
  * openrgb-python (protocol 3) only READS device details: names, LED counts, key layout;
  * a small protocol-6 connection (_V6) WRITES the colours, looking the IDs up before every
    write. Device i in the old list is controller ids[i] in the new one (same order).

Writes happen on a worker thread and only the latest state is kept, so a slow or sleeping
device (a wireless mouse) or an OpenRGB restart never delays the HTTP API or the iCUE output.
Devices must be in a per-LED mode (Direct, or a per-LED Static): OpenRGB's default.
"""
import logging
import socket
import struct
import threading
import time

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
    """One OpenRGB device we control: its position in the server's list plus, per LED, a
    left-to-right position (0..1) used to spread gradient scenes."""

    def __init__(self, index: int, device):
        self.index = index
        self.name = device.name
        self.led_count = len(device.leds)
        self.positions = self._positions(device)
        mode = device.modes[device.active_mode]
        self.per_led = mode.color_mode == ModeColors.PER_LED
        self.mode_name = mode.name

    @staticmethod
    def _positions(device):
        """Uses the key matrix where the device has one, otherwise the LED order in its zone."""
        positions = [0.5] * len(device.leds)
        offset = 0
        for zone in device.zones:
            count = len(zone.leds)
            matrix = getattr(zone, "matrix_map", None)
            if matrix and matrix[0] and len(matrix[0]) > 1:
                width = len(matrix[0])
                for row in matrix:
                    for col, idx in enumerate(row):
                        if idx is not None and 0 <= idx < count:
                            positions[offset + idx] = col / (width - 1)
            elif count > 1:
                for i in range(count):
                    positions[offset + i] = i / (count - 1)
            offset += count
        return positions


class OpenRgbOutput:
    def __init__(self, address: str, port: int, device_names):
        self._address = address
        self._port = port
        self._wanted = {name.lower() for name in device_names}

        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pending = None  # (scene, scale): only the newest state matters
        self._conn: _V6 | None = None
        self._ids: list[int] = []
        self._targets: list[_Target] = []
        self._missing: set[str] = set()
        self._connected_at = 0.0
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

    def apply(self, scene, scale: float):
        """Called by the controller on every change; returns immediately."""
        with self._lock:
            self._pending = (scene, scale)
        self._wake.set()

    # ----- worker ----------------------------------------------------------------------

    def _run(self):
        while not self._stop.is_set():
            # Sleep until there is news; if disconnected or missing devices, wake up
            # regularly and retry with the latest state.
            timeout = None
            if self._conn is None:
                timeout = RECONNECT_SECONDS
            elif self._missing:
                timeout = RESCAN_SECONDS
            self._wake.wait(timeout)
            self._wake.clear()
            if self._stop.is_set():
                break
            with self._lock:
                pending = self._pending
            if pending is None:
                continue
            try:
                if self._conn is not None and self._missing \
                        and time.monotonic() - self._connected_at >= RESCAN_SECONDS:
                    self._drop()  # look for the missing devices again
                if self._conn is None:
                    self._connect()
                self._write(*pending)
                self._warned = False
            except Exception as e:
                if not self._warned:
                    log.warning("OpenRGB output failed (%s: %s); will retry", type(e).__name__, e)
                    self._warned = True
                self._drop()

    def _connect(self):
        self._conn = _V6(self._address, self._port)
        self._refresh_targets()

    def _refresh_targets(self):
        """(Re)read device details. Called on connect and whenever the controller IDs change."""
        ids = self._conn.ids()
        # protocol 3: older protocols have no plugin list to wait for, which otherwise costs a
        # 10 s timeout per connection on OpenRGB 1.0
        reader = OpenRGBClient(address=self._address, port=self._port, name="icue-bridge-read",
                               protocol_version=3)
        try:
            devices = list(reader.devices)
            if len(devices) != len(ids):
                raise RuntimeError(f"device list ({len(devices)}) and controller IDs ({len(ids)}) disagree")
            targets = [_Target(i, d) for i, d in enumerate(devices)
                       if d.name.lower() in self._wanted and d.leds]
        finally:
            try:
                reader.disconnect()
            except Exception:
                pass
        for t in targets:
            if not t.per_led:
                log.warning("OpenRGB: %s is in mode '%s' which is not per-LED; set it to Direct "
                            "in OpenRGB or colours will be ignored", t.name, t.mode_name)
        found = {t.name.lower() for t in targets}
        self._missing = self._wanted - found
        self._ids, self._targets = ids, targets
        self._connected_at = time.monotonic()
        log.info("OpenRGB: controlling %s", ", ".join(t.name for t in targets) or "nothing")
        if self._missing:
            log.info("OpenRGB: not present right now: %s", ", ".join(sorted(self._missing)))

    def _drop(self):
        conn, self._conn, self._targets, self._ids = self._conn, None, [], []
        if conn is not None:
            conn.close()

    def _write(self, scene, scale: float):
        ids = self._conn.ids()  # cheap, and the IDs change whenever OpenRGB re-detects hardware
        if ids != self._ids:
            log.info("OpenRGB: controller IDs changed; re-reading devices")
            self._refresh_targets()
            ids = self._ids
        for target in self._targets:
            colors = []
            for t in target.positions:
                rgb = scene.color_at(t)
                colors.append(tuple(round(c * scale) for c in rgb))
            self._conn.set_leds(ids[target.index], colors)
