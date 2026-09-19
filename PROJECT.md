# iCUE StandBy widget

Control Corsair iCUE lighting from an iOS StandBy widget.

## Decisions

| Area | Decision |
|---|---|
| iOS route | Custom WidgetKit widget, built on a GitHub Actions macOS runner (public repo), sideloaded from Windows (AltStore / Sideloadly) with a free Apple ID. Re-sign every 7 days. |
| Target OS | iOS 26+, StandBy, interactive widget (App Intents) |
| Connectivity | Home Wi-Fi only for now. Tailscale can be added later without changes. |
| Devices | All connected iCUE devices |
| Scenes | Solid colors and gradients, defined in `bridge/scenes.json` |
| Auth | Shared secret token in `bridge/config.json` (git-ignored) |
| App Groups | Not used (free Apple IDs are restricted on entitlements). The widget reads all state from the bridge, and the bridge address and token are parameters on the widget itself (touch and hold > Edit Widget), so no secrets are baked into the build. |

## Widget design

One `systemSmall` widget (~160x160 pt in StandBy), dark and minimal, black background, soft glow in the active scene's color, readable under StandBy Night Mode's red tint.

```
 [ Power ] [  -  ] [  +  ]      brightness shown as "60%"
 [ Scene1] [Scene2] [Scene3]    first three scenes from GET /state
```

WidgetKit has no sliders, so brightness moves in steps of 10. A real slider can go in the companion app later.

## Bridge API (`bridge/`)

All endpoints except `/health` need the token: header `X-Token: <token>` (or `Authorization: Bearer`, or `?token=` for browser testing).

| Method | Path | Body | Effect |
|---|---|---|---|
| GET | `/health` | | liveness, no auth |
| GET | `/state` | | `power, brightness, scene, sceneName, connected, deviceCount, scenes[{id,name,colors}]` |
| POST | `/power` | `{"on": true}` or empty to toggle | |
| POST | `/brightness` | `{"value": 60}` or `{"delta": -10}` | clamped to 5-100; turns power on |
| POST | `/scene/<id>` | | turns power on |
| GET | `/` | | minimal browser test page (`/?token=...`) |

Every write returns the new state, so the widget can refresh from the response.

### Run it

```powershell
.venv\Scripts\python.exe IOS_ICUE\bridge\list_devices.py   # check iCUE connection
.venv\Scripts\python.exe IOS_ICUE\bridge\server.py         # start the bridge
```

First run creates `config.json` with a random token and prints the test-page URL. iCUE must be running with the SDK enabled (iCUE > Settings > Software and Games).

For your phone to reach it, Windows Firewall must allow inbound TCP 8765 on your private network (admin PowerShell):

```powershell
New-NetFirewallRule -DisplayName "iCUE bridge" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
```

Also give the PC a fixed IP (DHCP reservation on your router), because the widget will store the address.

### Notes from testing

- The bridge takes exclusive lighting control of each device. iCUE's own effects resume when the bridge stops.
- "Off" is all LEDs black, not releasing control.
- `CueSdk.disconnect()` in the cuesdk Python package crashes (0xc000001d): it frees the callback before the native call. `controller.disconnect_sdk()` works around it.
- Nautilus LCD cap reports 0 LEDs, so it is skipped.

## Status

- [x] Bridge: device listing, controller, HTTP API, scenes, browser test page
- [x] Verified from phone browser over Wi-Fi
- [ ] Bridge auto-start with Windows
- [x] SwiftUI companion app written (`ios/App`), not yet compiled
- [x] WidgetKit extension + App Intents written (`ios/Widget`), not yet compiled
- [x] XcodeGen project (`ios/project.yml`) + GitHub Actions workflow -> unsigned IPA, not yet run
- [ ] First CI build passes (expect a few compile fixes: no Xcode was available while writing)
- [ ] Sideload and test in StandBy

## iOS layout

```
ios/project.yml            XcodeGen spec (the .xcodeproj is generated in CI)
ios/Shared/BridgeClient.swift   HTTP client + models (both targets)
ios/Widget/                WidgetKit extension: LightsWidget.swift (UI), Intents.swift (buttons + config)
ios/App/                   companion app: connection test + copy address/token
.github/workflows/build-ios.yml
```

The repo root is `IOS_ICUE/`. `.gitignore` excludes Corsair's SDK folder, `bridge/config.json` (token) and generated files.

## Risks to check early

- Can the widget process reach a LAN host? iOS needs the local-network permission (granted via the companion app) and an ATS exception for plain HTTP.
- Widget refresh after a tap is not guaranteed instantly. Use the response from each intent to reload the timeline.
- The free-account App ID limit counts the widget extension as a second App ID.
