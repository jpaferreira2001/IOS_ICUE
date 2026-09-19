"""Owns the iCUE SDK session and the current lighting state (power, brightness, scene)."""
import json
import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path

from cuesdk import CueSdk
from cuesdk import api as cue_api
from cuesdk.enums import (CorsairAccessLevel, CorsairDeviceType,
                          CorsairEventId, CorsairSessionState)
from cuesdk.structs import CorsairDeviceFilter, CorsairLedColor

log = logging.getLogger("bridge.controller")

BRIGHTNESS_MIN = 5  # below this it looks "off" while power still says on; use power for off
BRIGHTNESS_MAX = 100


def hex_to_rgb(value: str):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(rgb)


@dataclass(frozen=True)
class Scene:
    id: str
    name: str
    colors: tuple  # one color = solid; several = gradient across each device
    angle: float = 0.0  # gradient direction in degrees, 0 = left to right

    def color_at(self, t: float):
        """Color at position t (0..1) along the gradient."""
        if len(self.colors) == 1:
            return self.colors[0]
        pos = min(max(t, 0.0), 1.0) * (len(self.colors) - 1)
        i = min(int(pos), len(self.colors) - 2)
        f = pos - i
        a, b = self.colors[i], self.colors[i + 1]
        return tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3))


def load_scenes(path: Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    scenes = [
        Scene(s["id"], s["name"], tuple(hex_to_rgb(c) for c in s["colors"]),
              float(s.get("angle", 0)))
        for s in raw
    ]
    if not scenes:
        raise ValueError(f"{path} defines no scenes")
    return scenes


def disconnect_sdk(sdk: CueSdk):
    # CueSdk.disconnect() drops the ctypes callback before the native call, and the DLL
    # then calls freed memory (crashes with 0xc000001d). Keep the handler referenced.
    cue_api.napi.CorsairDisconnect()


@dataclass
class _Device:
    id: str
    model: str
    led_ids: list
    positions: list  # (x, y) per led, same order as led_ids


class LightController:
    def __init__(self, scenes, state_path: Path, refresh_seconds: float = 0):
        self._scenes = {s.id: s for s in scenes}
        self._scene_order = [s.id for s in scenes]
        self._state_path = state_path
        self._refresh_seconds = refresh_seconds

        self._lock = threading.RLock()
        self._resync = threading.Event()
        self._stop = threading.Event()
        self._connected = False
        self._subscribed = False
        self._controlled: set[str] = set()  # device ids we hold exclusive lighting control of
        self._devices: list[_Device] = []
        self._sdk: CueSdk | None = None
        self._thread: threading.Thread | None = None

        self._power = True
        self._brightness = BRIGHTNESS_MAX
        self._scene_id = self._scene_order[0]
        self._load_state()

    # ----- lifecycle -------------------------------------------------------------------

    def start(self):
        self._sdk = CueSdk()
        err = self._sdk.connect(self._on_session_state)
        if err != 0:
            raise RuntimeError(f"iCUE connect() failed: {err}")
        self._thread = threading.Thread(target=self._supervise, name="icue-supervisor",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._resync.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._sdk:
            disconnect_sdk(self._sdk)
            self._sdk = None

    # ----- public state API ------------------------------------------------------------

    def get_state(self) -> dict:
        with self._lock:
            scene = self._scenes[self._scene_id]
            return {
                "power": self._power,
                "brightness": self._brightness,
                "scene": scene.id,
                "sceneName": scene.name,
                "connected": self._connected,
                "deviceCount": len(self._devices),
                "scenes": [{
                    "id": s.id,
                    "name": s.name,
                    "colors": [rgb_to_hex(c) for c in s.colors],
                } for s in (self._scenes[i] for i in self._scene_order)],
            }

    def set_power(self, on: bool | None = None) -> dict:
        """on=None toggles."""
        with self._lock:
            self._power = (not self._power) if on is None else bool(on)
            self._commit()
            return self.get_state()

    def set_brightness(self, value: int | None = None, delta: int | None = None) -> dict:
        if (value is None) == (delta is None):
            raise ValueError("give exactly one of value or delta")
        with self._lock:
            target = value if value is not None else self._brightness + delta
            self._brightness = min(max(int(target), BRIGHTNESS_MIN), BRIGHTNESS_MAX)
            self._power = True  # adjusting brightness implies you want the lights on
            self._commit()
            return self.get_state()

    def set_scene(self, scene_id: str) -> dict:
        if scene_id not in self._scenes:
            raise KeyError(scene_id)
        with self._lock:
            self._scene_id = scene_id
            self._power = True
            self._commit()
            return self.get_state()

    # ----- SDK plumbing ----------------------------------------------------------------

    def _on_session_state(self, evt):
        # Runs on an SDK thread: only flag work, never call the SDK from here.
        log.info("iCUE session: %s", evt.state)
        self._connected = evt.state == CorsairSessionState.CSS_Connected
        if not self._connected:
            self._controlled.clear()  # control is lost with the session
        else:
            self._resync.set()

    def _on_device_event(self, evt):
        if evt.id == CorsairEventId.CEI_DeviceConnectionStatusChangedEvent:
            log.info("device %s %s", evt.data.device_id,
                     "connected" if evt.data.is_connected else "disconnected")
            if not evt.data.is_connected:
                self._controlled.discard(evt.data.device_id)
            self._resync.set()

    def _supervise(self):
        timeout = self._refresh_seconds or None
        while not self._stop.is_set():
            resynced = self._resync.wait(timeout)
            if self._stop.is_set():
                break
            if not self._connected:
                continue
            try:
                with self._lock:
                    if resynced:
                        self._resync.clear()
                        self._setup()
                    self._apply()  # also serves as the optional periodic refresh
            except Exception:
                log.exception("supervisor step failed; will retry on next event")

    def _setup(self):
        """(Re)discover devices and take exclusive control of their lighting."""
        devices, err = self._sdk.get_devices(CorsairDeviceFilter(CorsairDeviceType.CDT_All))
        if err != 0:
            log.error("get_devices failed: %s", err)
            return
        found = []
        for d in devices:
            if d.led_count == 0:
                continue
            leds, err = self._sdk.get_led_positions(d.device_id)
            if err != 0 or not leds:
                log.warning("no LED positions for %s: %s", d.model, err)
                continue
            found.append(_Device(d.device_id, d.model, [l.id for l in leds],
                                 [(l.cx, l.cy) for l in leds]))
        self._devices = found
        for dev in found:
            if dev.id in self._controlled:  # re-requesting held control returns CE_NoControl
                continue
            err = self._sdk.request_control(dev.id, CorsairAccessLevel.CAL_ExclusiveLightingControl)
            if err == 0:
                self._controlled.add(dev.id)
            else:
                log.error("request_control failed for %s: %s", dev.model, err)
        log.info("controlling %d device(s): %s", len(found), ", ".join(d.model for d in found))
        if not self._subscribed:
            self._subscribed = self._sdk.subscribe_for_events(self._on_device_event) == 0

    def _commit(self):
        self._save_state()
        if self._connected:
            self._apply()

    def _apply(self):
        scene = self._scenes[self._scene_id]
        scale = self._brightness / 100 if self._power else 0.0
        for dev in self._devices:
            colors = [
                CorsairLedColor(led_id, *(round(c * scale) for c in rgb), 255)
                for led_id, rgb in zip(dev.led_ids, self._device_colors(scene, dev))
            ]
            err = self._sdk.set_led_colors(dev.id, colors)
            if err != 0:
                log.warning("set_led_colors on %s failed: %s", dev.model, err)

    @staticmethod
    def _device_colors(scene: Scene, dev: _Device):
        if len(scene.colors) == 1:
            return [scene.colors[0]] * len(dev.led_ids)
        dx, dy = math.cos(math.radians(scene.angle)), math.sin(math.radians(scene.angle))
        proj = [x * dx + y * dy for x, y in dev.positions]
        lo, hi = min(proj), max(proj)
        span = hi - lo
        return [scene.color_at((p - lo) / span if span else 0.5) for p in proj]

    # ----- persistence -----------------------------------------------------------------

    def _load_state(self):
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._power = bool(data.get("power", self._power))
        self._brightness = min(max(int(data.get("brightness", self._brightness)),
                                   BRIGHTNESS_MIN), BRIGHTNESS_MAX)
        if data.get("scene") in self._scenes:
            self._scene_id = data["scene"]

    def _save_state(self):
        data = {"power": self._power, "brightness": self._brightness, "scene": self._scene_id}
        try:
            self._state_path.write_text(json.dumps(data), encoding="utf-8")
        except OSError as e:
            log.warning("could not save state: %s", e)
