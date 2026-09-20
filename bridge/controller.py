"""Owns the iCUE SDK session and the current lighting state (power, brightness, preset).

The bridge never picks colors. You set your colors and effects in iCUE and OpenRGB; this only
scales their brightness:
  * iCUE devices (RAM, LINK hub): a transparent black layer on top of whatever iCUE shows.
    Alpha 0 = untouched, 255 = black. Colors, presets and animated effects stay as they are.
  * extra outputs (OpenRGB): see openrgb_output.py.
"""
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from cuesdk import CueSdk
from cuesdk import api as cue_api
from cuesdk.enums import CorsairDeviceType, CorsairEventId, CorsairSessionState
from cuesdk.structs import CorsairDeviceFilter, CorsairLedColor

log = logging.getLogger("bridge.controller")

BRIGHTNESS_MIN = 5  # below this it looks "off" while power still says on; use power for off
BRIGHTNESS_MAX = 100
# Re-checks for devices that report 0 LEDs: the first few are quick, then it polls slowly.
EMPTY_RETRY_FAST = 3.0
EMPTY_RETRY_FAST_COUNT = 10
EMPTY_RETRY_SLOW = 30.0


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    brightness: int  # percent


def load_presets(raw) -> list[Preset]:
    presets = [Preset(p["id"], p["name"],
                      min(max(int(p["brightness"]), BRIGHTNESS_MIN), BRIGHTNESS_MAX)) for p in raw]
    if not presets:
        raise ValueError("config.json defines no presets")
    return presets


def disconnect_sdk(sdk: CueSdk):
    # CueSdk.disconnect() drops the ctypes callback before the native call, and the DLL
    # then calls freed memory (crashes with 0xc000001d). Keep the handler referenced.
    cue_api.napi.CorsairDisconnect()


@dataclass
class _Device:
    id: str
    model: str
    led_ids: list


class LightController:
    def __init__(self, presets, state_path: Path, refresh_seconds: float = 0, outputs=()):
        # outputs: extra back ends (e.g. OpenRGB) with start(), stop() and apply(scale);
        # they run alongside the iCUE SDK output. An output may also offer capture().
        self._outputs = list(outputs)
        self._presets = {p.id: p for p in presets}
        self._preset_order = [p.id for p in presets]
        self._state_path = state_path
        self._refresh_seconds = refresh_seconds

        self._lock = threading.RLock()
        self._resync = threading.Event()
        self._stop = threading.Event()
        self._connected = False
        self._subscribed = False
        self._listeners: list = []
        self._empty_retries = 0
        self._last_summary = None
        self._devices: list[_Device] = []
        self._sdk: CueSdk | None = None
        self._thread: threading.Thread | None = None

        self._power = True
        self._brightness = BRIGHTNESS_MAX
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
        for output in self._outputs:
            output.start()
        self._apply_outputs()  # they don't depend on iCUE, so bring them up to date now

    def stop(self):
        self._stop.set()
        self._resync.set()
        for output in self._outputs:
            output.stop()
        if self._thread:
            self._thread.join(timeout=3)
        if self._sdk:
            disconnect_sdk(self._sdk)
            self._sdk = None

    # ----- public state API ------------------------------------------------------------

    def get_state(self) -> dict:
        with self._lock:
            return {
                "power": self._power,
                "brightness": self._brightness,
                "preset": self._active_preset(),
                "presets": [{"id": p.id, "name": p.name, "brightness": p.brightness}
                            for p in (self._presets[i] for i in self._preset_order)],
                "connected": self._connected,
                "deviceCount": len(self._devices),
            }

    def _active_preset(self):
        if not self._power:
            return None
        return next((p.id for p in self._presets.values() if p.brightness == self._brightness),
                    None)

    def add_listener(self, listener):
        """listener(state) is called after every change made through set_*; keep it quick."""
        self._listeners.append(listener)

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

    def set_preset(self, preset_id: str) -> dict:
        if preset_id not in self._presets:
            raise KeyError(preset_id)
        return self.set_brightness(value=self._presets[preset_id].brightness)

    def capture(self) -> list[str]:
        """Remember the look you have set up in OpenRGB (see openrgb_output.py). Returns what
        was captured, one line per output."""
        with self._lock:
            results = []
            for output in self._outputs:
                if hasattr(output, "capture"):
                    results.append(output.capture())
            return results

    # ----- SDK plumbing ----------------------------------------------------------------

    def _on_session_state(self, evt):
        # Runs on an SDK thread: only flag work, never call the SDK from here.
        # While iCUE is closed the SDK flips Connecting/Timeout every second; that is noise.
        noisy = evt.state in (CorsairSessionState.CSS_Connecting, CorsairSessionState.CSS_Timeout)
        log.log(logging.DEBUG if noisy else logging.INFO, "iCUE session: %s", evt.state)
        self._connected = evt.state == CorsairSessionState.CSS_Connected
        if self._connected:
            self._empty_retries = 0
            self._resync.set()

    def _on_device_event(self, evt):
        if evt.id == CorsairEventId.CEI_DeviceConnectionStatusChangedEvent:
            log.info("device %s %s", evt.data.device_id,
                     "connected" if evt.data.is_connected else "disconnected")
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
                    # not the outputs: re-sending OpenRGB colors here (the LCD-cap re-check runs
                    # every 30 s) would overwrite colors you just set in OpenRGB
            except Exception:
                log.exception("supervisor step failed; will retry on next event")

    def _setup(self):
        """(Re)discover the iCUE devices. Control stays shared: our layer sits on top of iCUE's."""
        devices, err = self._sdk.get_devices(CorsairDeviceFilter(CorsairDeviceType.CDT_All))
        if err != 0:
            log.error("get_devices failed: %s", err)
            return
        found = []
        if any(d.led_count == 0 for d in devices):
            # A device can report 0 LEDs while iCUE is still initialising it (some, like the
            # Nautilus LCD cap, really have none). Look again soon, then settle to a slow poll.
            delay = EMPTY_RETRY_FAST if self._empty_retries < EMPTY_RETRY_FAST_COUNT else EMPTY_RETRY_SLOW
            self._empty_retries += 1
            timer = threading.Timer(delay, self._resync.set)
            timer.daemon = True
            timer.start()
        for d in devices:
            if d.led_count == 0:
                continue
            leds, err = self._sdk.get_led_positions(d.device_id)
            if err != 0 or not leds:
                log.warning("no LED positions for %s: %s", d.model, err)
                continue
            found.append(_Device(d.device_id, d.model, [l.id for l in leds]))
        self._devices = found
        summary = ", ".join(d.model for d in found)
        if summary != self._last_summary:  # the 0-LED re-checks would otherwise repeat this
            log.info("dimming %d iCUE device(s): %s", len(found), summary)
            self._last_summary = summary
        if not self._subscribed:
            self._subscribed = self._sdk.subscribe_for_events(self._on_device_event) == 0

    def _commit(self):
        self._save_state()
        if self._connected:
            self._apply()
        self._apply_outputs()
        state = self.get_state()
        for listener in self._listeners:
            try:
                listener(state)
            except Exception:
                log.exception("state listener failed")

    def _scale(self) -> float:
        return self._brightness / 100 if self._power else 0.0

    def _apply_outputs(self):
        scale = self._scale()
        for output in self._outputs:
            try:
                output.apply(scale)
            except Exception:
                log.exception("output failed")

    def _apply(self):
        """Dim the iCUE devices: a black layer whose opacity is the brightness we take away."""
        alpha = round(255 * (1 - self._scale()))
        for dev in self._devices:
            colors = [CorsairLedColor(led_id, 0, 0, 0, alpha) for led_id in dev.led_ids]
            err = self._sdk.set_led_colors(dev.id, colors)
            if err != 0:
                log.warning("set_led_colors on %s failed: %s", dev.model, err)

    # ----- persistence -----------------------------------------------------------------

    def _load_state(self):
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._power = bool(data.get("power", self._power))
        self._brightness = min(max(int(data.get("brightness", self._brightness)),
                                   BRIGHTNESS_MIN), BRIGHTNESS_MAX)

    def _save_state(self):
        data = {"power": self._power, "brightness": self._brightness}
        try:
            self._state_path.write_text(json.dumps(data), encoding="utf-8")
        except OSError as e:
            log.warning("could not save state: %s", e)
