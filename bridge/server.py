"""HTTP API and HomeKit accessory in front of the iCUE controller. Run: python server.py"""
import atexit
import hmac
import json
import logging
import logging.handlers
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

from controller import LightController, load_presets

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.json"
DEFAULT_CONFIG = {
    "host": "0.0.0.0", "port": 8765, "refresh_seconds": 0,
    "homekit": False, "homekit_port": 51826,  # HomeKit is optional; see PROJECT.md
    # Brightness presets for the phone widget (percent). Colors are never set by the bridge:
    # you choose them in iCUE and OpenRGB.
    "presets": [
        {"id": "day", "name": "Day", "brightness": 50},
        {"id": "night", "name": "Night", "brightness": 20},
        {"id": "movie", "name": "Movie", "brightness": 5},
    ],
    # Devices dimmed through OpenRGB (its Windows service serves the SDK on 6742). Names must
    # match OpenRGB's device names exactly (case-insensitive); absent ones are skipped.
    # iCUE dims the Corsair RAM and LINK hub, which OpenRGB cannot see / should not touch.
    "openrgb": {
        "enabled": True, "address": "127.0.0.1", "port": 6742,
        "devices": ["Gigabyte GeForce RTX 5070 Eagle OC ICE", "Razer Huntsman V2",
                    "Razer Cobra Pro (Wireless)", "Razer Cobra Pro (Wired)"],
    },
}


def new_homekit_pin() -> str:
    """Random 'xxx-xx-xxx' setup code, avoiding the codes HomeKit rejects as too easy."""
    while True:
        digits = "%08d" % secrets.randbelow(10**8)
        if len(set(digits)) > 1 and digits not in ("12345678", "87654321"):
            return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def load_config() -> dict:
    """Read config.json, creating it (fresh random token and PIN) or filling in new keys."""
    # utf-8-sig: Windows editors (and PowerShell 5.1) like to prepend a BOM
    stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")) if CONFIG_PATH.exists() else {}
    cfg = {**DEFAULT_CONFIG, **stored}
    cfg.setdefault("token", secrets.token_urlsafe(24))
    cfg.setdefault("homekit_pin", new_homekit_pin())
    if cfg != stored:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        logging.info("updated %s", CONFIG_PATH)
    return cfg


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the LAN interface
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def create_app(controller: LightController, token: str) -> Flask:
    app = Flask(__name__)

    def supplied_token() -> str:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return request.headers.get("X-Token") or request.args.get("token", "")

    @app.before_request
    def require_token():
        if request.path == "/health":
            return None
        if not hmac.compare_digest(supplied_token().encode(), token.encode()):
            return jsonify(error="unauthorized"), 401
        return None

    @app.get("/health")
    def health():
        return jsonify(ok=True)

    @app.get("/state")
    def state():
        return jsonify(controller.get_state())

    @app.post("/power")
    def power():
        # {"on": true|false} sets it; an empty body toggles.
        body = request.get_json(silent=True) or {}
        return jsonify(controller.set_power(body.get("on")))

    @app.post("/brightness")
    def brightness():
        # {"value": 60} sets it; {"delta": -10} steps it.
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(controller.set_brightness(body.get("value"), body.get("delta")))
        except (ValueError, TypeError) as e:
            return jsonify(error=str(e)), 400

    @app.post("/preset/<preset_id>")
    def preset(preset_id):
        # Sets the preset's brightness (and turns the lights on).
        try:
            return jsonify(controller.set_preset(preset_id))
        except KeyError:
            return jsonify(error=f"unknown preset '{preset_id}'"), 404

    @app.post("/capture")
    def capture():
        # Remember the colors currently set in OpenRGB as the look to dim (see PROJECT.md).
        return jsonify(captured=controller.capture())

    @app.get("/")
    def test_page():
        return TEST_PAGE

    return app


# Minimal control page for testing from a phone browser: open /?token=<token>
TEST_PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>iCUE bridge</title>
<style>
body{background:#000;color:#ddd;font:16px system-ui;margin:0;padding:16px;max-width:420px}
button{background:#1c1c1e;color:#eee;border:0;border-radius:12px;padding:16px;font-size:16px;margin:4px}
button.on{outline:2px solid #ff9a3c}
#presets button{display:block;width:100%;margin:6px 0;text-align:left}
small{color:#888;display:block;margin-top:14px}
</style>
<h3 id=s>...</h3>
<button onclick="act('/power')">Power</button>
<button onclick="act('/brightness',{delta:-10})">&minus;</button>
<button onclick="act('/brightness',{delta:10})">+</button>
<div id=presets></div>
<small id=note></small>
<button onclick="capture()" style="margin-top:8px">Capture my OpenRGB colors</button>
<script>
const token=new URLSearchParams(location.search).get('token')||'';
async function call(path,body){
  const r=await fetch(path,{method:path=='/state'?'GET':'POST',
    headers:{'X-Token':token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  return r.json();
}
async function act(path,body){render(await call(path,body))}
async function capture(){const r=await call('/capture');note.textContent=(r.captured||[r.error]).join(' | ')}
function render(st){
  if(st.error){s.textContent=st.error;return}
  s.textContent=(st.power?'On':'Off')+' \\u00b7 '+st.brightness+'%'+(st.connected?'':' (iCUE not connected)');
  presets.innerHTML='';
  for(const p of st.presets){
    const b=document.createElement('button');
    b.textContent=p.name+' \\u00b7 '+p.brightness+'%';
    if(p.id==st.preset)b.className='on';
    b.onclick=()=>act('/preset/'+p.id);presets.appendChild(b);
  }
}
act('/state');
</script>"""


_instance_mutex = None  # kept referenced for the life of the process


def already_running() -> bool:
    """True if another bridge holds the named mutex (Windows). Two bridges on one port
    silently share it and only one knows the current token."""
    global _instance_mutex
    if sys.platform != "win32":
        return False
    import ctypes
    _instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\iCUEBridge")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def setup_logging():
    handlers = [logging.handlers.RotatingFileHandler(
        HERE / "bridge.log", maxBytes=500_000, backupCount=2, encoding="utf-8")]
    if sys.stderr:  # None when launched hidden without a console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(name)s: %(message)s")


def main():
    setup_logging()
    if already_running():
        logging.error("another bridge is already running; exiting")
        print("Another bridge is already running. Stop it first.")
        sys.exit(1)
    if "--delay" in sys.argv:  # used at login so iCUE has time to start
        time.sleep(float(sys.argv[sys.argv.index("--delay") + 1]))
    cfg = load_config()
    outputs = []
    if cfg["openrgb"].get("enabled"):
        from openrgb_output import OpenRgbOutput  # imported here so it stays optional
        outputs.append(OpenRgbOutput(cfg["openrgb"]["address"], cfg["openrgb"]["port"],
                                     cfg["openrgb"]["devices"], HERE / "base_colors.json"))
    controller = LightController(load_presets(cfg["presets"]), HERE / "state.json",
                                 cfg["refresh_seconds"], outputs)
    controller.start()
    atexit.register(controller.stop)

    ip = lan_ip()
    print(f"\n  Test page:  http://{ip}:{cfg['port']}/?token={cfg['token']}")
    app = create_app(controller, cfg["token"])

    def serve_http():
        app.run(host=cfg["host"], port=cfg["port"], threaded=True)

    if not cfg["homekit"]:
        print()
        serve_http()
        return

    print(f"  HomeKit:    Home app > Add Accessory > More options > iCUE Bridge, code {cfg['homekit_pin']}\n")
    threading.Thread(target=serve_http, name="http", daemon=True).start()
    from homekit import run_homekit  # imported here so HTTP-only setups don't need HAP-python
    run_homekit(controller, pin=cfg["homekit_pin"], port=cfg["homekit_port"], address=ip,
                persist_file=HERE / "homekit.state")


if __name__ == "__main__":
    main()
