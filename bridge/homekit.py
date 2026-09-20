"""HomeKit front end: one light plus one switch per brightness preset (LightController)."""
import logging
from pathlib import Path

from pyhap.accessory import Accessory, Bridge
from pyhap.accessory_driver import AccessoryDriver
from pyhap.const import CATEGORY_LIGHTBULB, CATEGORY_SWITCH

from controller import BRIGHTNESS_MIN, LightController

log = logging.getLogger("bridge.homekit")

LIGHT_AID = 2
# Preset i gets aid PRESET_AID_BASE + i, so presets added at the END of the "presets" list in
# config.json keep an existing pairing intact. (Reordering or removing presets shifts them;
# re-pair if you do.)
PRESET_AID_BASE = 100


class LightAccessory(Accessory):
    category = CATEGORY_LIGHTBULB

    def __init__(self, driver, controller: LightController):
        super().__init__(driver, "iCUE Lights", aid=LIGHT_AID)
        self.set_info_service(manufacturer="iCUE bridge", model="Lights",
                              serial_number="icue-lights")
        self._controller = controller
        service = self.add_preload_service("Lightbulb", chars=["On", "Brightness"])
        self._on = service.configure_char("On", setter_callback=self._set_on)
        self._brightness = service.configure_char(
            "Brightness", properties={"minValue": BRIGHTNESS_MIN},
            setter_callback=self._set_brightness)

    def _set_on(self, value):
        self._controller.set_power(bool(value))

    def _set_brightness(self, value):
        self._controller.set_brightness(value=int(value))

    def update(self, state: dict):
        self._on.set_value(state["power"])
        self._brightness.set_value(state["brightness"])


class PresetAccessory(Accessory):
    """A switch that is on while its brightness preset is the active one."""
    category = CATEGORY_SWITCH

    def __init__(self, driver, controller: LightController, preset: dict, aid: int):
        super().__init__(driver, preset["name"], aid=aid)
        self.set_info_service(manufacturer="iCUE bridge", model="Preset",
                              serial_number=f"icue-preset-{preset['id']}")
        self._controller = controller
        self._preset_id = preset["id"]
        self._on = self.add_preload_service("Switch").configure_char(
            "On", setter_callback=self._set_on)

    def _set_on(self, value):
        if value:
            self._controller.set_preset(self._preset_id)
            return
        if self._controller.get_state()["preset"] == self._preset_id:
            self._controller.set_power(False)  # turning the active preset off = lights off

    def update(self, state: dict):
        self._on.set_value(state["preset"] == self._preset_id)


def run_homekit(controller: LightController, *, pin: str, port: int, address: str,
                persist_file: Path):
    """Blocks (runs the HomeKit event loop) until interrupted."""
    driver = AccessoryDriver(address=address, port=port, persist_file=str(persist_file),
                             pincode=pin.encode())
    bridge = Bridge(driver, "iCUE Bridge")

    accessories = [LightAccessory(driver, controller)]
    for i, preset in enumerate(controller.get_state()["presets"]):
        accessories.append(PresetAccessory(driver, controller, preset, PRESET_AID_BASE + i))
    for acc in accessories:
        bridge.add_accessory(acc)
    driver.add_accessory(accessory=bridge)

    def sync(state):  # thread-safe: set_value publishes through the driver's loop
        for acc in accessories:
            acc.update(state)

    sync(controller.get_state())
    controller.add_listener(sync)

    log.info("HomeKit: %d accessories on %s:%d", len(accessories), address, port)
    driver.start()
