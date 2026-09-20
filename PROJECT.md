# iCUE StandBy widget

Control Corsair iCUE lighting from an iOS StandBy widget.

## Decisions

| Area | Decision |
|---|---|
| iOS route | Custom WidgetKit widget, built in GitHub Actions, sideloaded with Sideloadly. Status: the app installs and connects, but the widget did not appear in the gallery on the first build (see "Widget not showing" below). HomeKit (`bridge/homekit.py`, off by default) is an unused fallback. |
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

## Widget not showing (debugging log)

Symptom: after a Sideloadly install (iOS 26.6.2, free Apple ID) the app works, but "iCUE Lights" is not in the widget gallery, even after restarting the phone and reinstalling. The IPA is fine: it contains `PlugIns/ICueLightsWidget.appex` (arm64, links WidgetKit + AppIntents, has the extension entry point) and Sideloadly is not dropping it.

Clue: Sideloadly's log shows a single App ID ("iCUE Lights") and the counter stayed at 9 remaining, so the extension probably never got its own App ID/profile. Sideloadly looks App IDs up by name, and app and extension both had the display name "iCUE Lights".

Build 2 changes: extension display name is now "iCUE Lights Widget"; an app icon was added; and a plain static `ProbeWidget` ("iCUE widget test", no App Intents) ships in the same extension. Reading the result:

- Counter drops to 8 remaining after install: the extension got its own App ID (good sign).
- Only the test widget shows: App Intents are the problem (`AppIntentConfiguration` / interactive buttons).
- Both show: fixed; delete `ProbeWidget.swift` and its line in `LightsWidgetBundle`.
- Neither shows: the extension isn't being registered; next step is a different signing route.

Ruled out: Sideloadly "Drop plug-ins" (0 of 1 dropped), missing extension in the IPA, plist mistakes, restart/reinstall. SideStore's iOS 18 widget fix (PR #746) was about its own widget's `containerBackground`, unrelated.

## HomeKit (`bridge/homekit.py`, optional, off by default)

Enable with `"homekit": true` in `config.json`. The bridge then also runs a HomeKit bridge accessory (HAP-python, pure Python) on TCP 51826, advertised over mDNS. It exposes:

- **iCUE Lights**: a light with on/off and brightness (5-100).
- **One switch per scene** in `scenes.json`, on while that scene is active. Turning the active scene's switch off turns the lights off.

Changes made through the HTTP API show up in the Home app too, and the other way round.

**Pair once:** with the bridge running, Home app > **+** > Add Accessory > **More options...** > iCUE Bridge, then enter the setup code printed at startup (also `homekit_pin` in `config.json`). Accept the "uncertified accessory" warning. Then add the tiles to your Home favourites and use the Home widget in StandBy.

Firewall (admin PowerShell), so the phone can find and reach it:

```powershell
New-NetFirewallRule -DisplayName "iCUE HomeKit" -Direction Inbound -Protocol TCP -LocalPort 51826 -Action Allow -Profile Private
New-NetFirewallRule -DisplayName "iCUE HomeKit mDNS" -Direction Inbound -Protocol UDP -LocalPort 5353 -Action Allow -Profile Private
```

`homekit.state` (pairing keys) is git-ignored; delete it, and remove the accessory in the Home app, to re-pair. Scene switches keep their identity as long as new scenes are added at the END of `scenes.json`. Set `"homekit": false` in `config.json` to run HTTP only.

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
- Devices reporting 0 LEDs are re-checked (every 3 s, then every 30 s), because iCUE can be slow to initialise them. On 2026-09-19 the iCUE LINK System Hub (62 LEDs earlier) reported 0 LEDs for a long time after restarts; if the hub's lights don't respond, check that iCUE itself shows those devices.

## Status

- [x] Bridge: device listing, controller, HTTP API, scenes, browser test page
- [x] Verified from phone browser over Wi-Fi
- [x] HomeKit accessory in the bridge (advertisement verified on the PC's network)
- [ ] Pair with the Home app, test the light and scene switches, add Home widget in StandBy
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
