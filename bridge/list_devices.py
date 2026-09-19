"""List every iCUE device the SDK can see. Run this first to check the connection."""
import sys
import threading

from cuesdk import CueSdk
from cuesdk.enums import CorsairDeviceType, CorsairSessionState
from cuesdk.structs import CorsairDeviceFilter

from controller import disconnect_sdk


def main() -> int:
    connected = threading.Event()
    last_state = {"value": None}

    def on_state_changed(evt):
        last_state["value"] = evt.state
        print(f"session state: {evt.state}")
        if evt.state == CorsairSessionState.CSS_Connected:
            connected.set()

    sdk = CueSdk()
    err = sdk.connect(on_state_changed)
    if err != 0:
        print(f"connect() failed: {err}")
        return 1

    if not connected.wait(timeout=10):
        print("Timed out waiting for iCUE. Is iCUE running, and is 'Enable SDK' ticked "
              "in iCUE Settings > Software and Games?")
        return 1

    devices, err = sdk.get_devices(CorsairDeviceFilter(CorsairDeviceType.CDT_All))
    if err != 0:
        print(f"get_devices() failed: {err}")
        return 1

    print(f"\n{len(devices)} device(s):")
    for d in devices:
        print(f"  {str(d.type):40s} {d.model:30s} leds={d.led_count:<4d} id={d.device_id}")

    disconnect_sdk(sdk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
