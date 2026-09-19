"""HTTP API in front of the iCUE controller. Run: python server.py"""
import atexit
import hmac
import json
import logging
import secrets
import socket
from pathlib import Path

from flask import Flask, jsonify, request

from controller import LightController, load_scenes

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.json"
DEFAULT_CONFIG = {"host": "0.0.0.0", "port": 8765, "refresh_seconds": 0}


def load_config() -> dict:
    """Read config.json, creating it (with a fresh random token) on first run."""
    if not CONFIG_PATH.exists():
        cfg = {**DEFAULT_CONFIG, "token": secrets.token_urlsafe(24)}
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        logging.info("created %s", CONFIG_PATH)
        return cfg
    return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}


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

    @app.post("/scene/<scene_id>")
    def scene(scene_id):
        try:
            return jsonify(controller.set_scene(scene_id))
        except KeyError:
            return jsonify(error=f"unknown scene '{scene_id}'"), 404

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
#scenes button{display:block;width:100%;margin:6px 0;text-align:left}
</style>
<h3 id=s>...</h3>
<button onclick="call('/power')">Power</button>
<button onclick="call('/brightness',{delta:-10})">&minus;</button>
<button onclick="call('/brightness',{delta:10})">+</button>
<div id=scenes></div>
<script>
const token=new URLSearchParams(location.search).get('token')||'';
async function call(path,body){
  const r=await fetch(path,{method:path=='/state'?'GET':'POST',
    headers:{'X-Token':token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  render(await r.json());
}
function render(st){
  if(st.error){s.textContent=st.error;return}
  s.textContent=(st.power?'On':'Off')+' \\u00b7 '+st.brightness+'% \\u00b7 '+st.sceneName+(st.connected?'':' (iCUE not connected)');
  scenes.innerHTML='';
  for(const sc of st.scenes){
    const b=document.createElement('button');
    b.textContent=sc.name;b.style.background='linear-gradient(90deg,'+sc.colors.join(',')+(sc.colors.length<2?','+sc.colors[0]:'')+')';
    b.style.color='#000';if(sc.id==st.scene&&st.power)b.className='on';
    b.onclick=()=>call('/scene/'+sc.id);scenes.appendChild(b);
  }
}
call('/state');
</script>"""


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    cfg = load_config()
    controller = LightController(load_scenes(HERE / "scenes.json"), HERE / "state.json",
                                 cfg["refresh_seconds"])
    controller.start()
    atexit.register(controller.stop)

    print(f"\n  Test page:  http://{lan_ip()}:{cfg['port']}/?token={cfg['token']}\n")
    create_app(controller, cfg["token"]).run(host=cfg["host"], port=cfg["port"], threaded=True)


if __name__ == "__main__":
    main()
