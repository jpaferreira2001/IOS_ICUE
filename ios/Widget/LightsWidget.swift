import SwiftUI
import WidgetKit

struct LightsEntry: TimelineEntry {
    let date: Date
    let host: String
    let token: String
    let state: BridgeState?
    let message: String?
}

struct LightsProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> LightsEntry {
        LightsEntry(date: Date(), host: "", token: "", state: .sample, message: nil)
    }

    func snapshot(for configuration: BridgeConfigIntent, in context: Context) async -> LightsEntry {
        if context.isPreview {
            return LightsEntry(date: Date(), host: "", token: "", state: .sample, message: nil)
        }
        return await load(configuration)
    }

    func timeline(for configuration: BridgeConfigIntent, in context: Context) async -> Timeline<LightsEntry> {
        let entry = await load(configuration)
        // Button taps reload the timeline immediately; this only catches changes made elsewhere.
        return Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(15 * 60)))
    }

    private func load(_ config: BridgeConfigIntent) async -> LightsEntry {
        let client = BridgeClient(host: config.host, token: config.token)
        func entry(state: BridgeState?, message: String?) -> LightsEntry {
            LightsEntry(date: Date(), host: config.host, token: config.token,
                        state: state, message: message)
        }
        guard client.isConfigured else {
            return entry(state: nil, message: "Touch and hold, then Edit Widget to set the address and token.")
        }
        do {
            return entry(state: try await client.state(), message: nil)
        } catch {
            return entry(state: nil, message: error.localizedDescription)
        }
    }
}

extension BridgeState {
    static let sample = BridgeState(
        power: true, brightness: 60, scene: "ocean", sceneName: "Ocean", connected: true,
        deviceCount: 2,
        scenes: [
            BridgeScene(id: "warm", name: "Warm", colors: ["#FF9A3C"]),
            BridgeScene(id: "ocean", name: "Ocean", colors: ["#0A3CFF", "#00D4FF"]),
            BridgeScene(id: "sunset", name: "Sunset", colors: ["#FF512F", "#DD2476"]),
        ])
}

struct LightsWidgetView: View {
    let entry: LightsEntry

    var body: some View {
        Group {
            if let state = entry.state {
                controls(state)
            } else {
                Text(entry.message ?? "No data")
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(.white.opacity(0.7))
                    .multilineTextAlignment(.center)
            }
        }
        .containerBackground(for: .widget) {
            ZStack {
                Color.black
                RadialGradient(colors: [glow.opacity(0.30), .clear], center: .top,
                               startRadius: 0, endRadius: 150)
            }
        }
    }

    private var glow: Color {
        guard let state = entry.state, state.power,
              let hex = state.scenes.first(where: { $0.id == state.scene })?.colors.first
        else { return .clear }
        return Color(hex: hex)
    }

    private func controls(_ state: BridgeState) -> some View {
        VStack(spacing: 6) {
            HStack {
                Text(state.power ? "\(state.brightness)%" : "Off")
                Spacer()
                Text(state.sceneName).lineLimit(1)
            }
            .font(.system(size: 11, weight: .medium, design: .rounded))
            .foregroundStyle(.white.opacity(0.6))

            HStack(spacing: 6) {
                Button(intent: SetPowerIntent(host: entry.host, token: entry.token, on: !state.power)) {
                    tile(fill: AnyShapeStyle(state.power ? glow.opacity(0.9) : Color.white.opacity(0.12))) {
                        Image(systemName: "power")
                            .foregroundStyle(state.power ? Color.black : Color.white)
                    }
                }
                Button(intent: StepBrightnessIntent(host: entry.host, token: entry.token, delta: -10)) {
                    tile { Image(systemName: "minus").foregroundStyle(.white) }
                }
                Button(intent: StepBrightnessIntent(host: entry.host, token: entry.token, delta: 10)) {
                    tile { Image(systemName: "plus").foregroundStyle(.white) }
                }
            }

            HStack(spacing: 6) {
                ForEach(state.scenes.prefix(3)) { scene in
                    sceneButton(scene, state: state)
                }
            }
        }
        .buttonStyle(.plain)
    }

    private func sceneButton(_ scene: BridgeScene, state: BridgeState) -> some View {
        let colors = scene.colors.map { Color(hex: $0) }
        let gradient = LinearGradient(colors: colors + (colors.count == 1 ? colors : []),
                                      startPoint: .leading, endPoint: .trailing)
        let luminance = scene.colors.map { RGB(hex: $0).luminance }.reduce(0, +)
            / Double(max(scene.colors.count, 1))
        let active = state.power && scene.id == state.scene
        return Button(intent: SelectSceneIntent(host: entry.host, token: entry.token, sceneID: scene.id)) {
            tile(fill: AnyShapeStyle(gradient)) {
                Text(scene.name)
                    .font(.system(size: 10, weight: .bold, design: .rounded))
                    .lineLimit(1)
                    .minimumScaleFactor(0.5)
                    .padding(.horizontal, 2)
                    .foregroundStyle(luminance > 0.55 ? Color.black : Color.white)
            }
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(Color.white, lineWidth: active ? 2 : 0)
            )
            .opacity(state.power ? 1 : 0.4)
        }
    }

    private func tile<Content: View>(
        fill: AnyShapeStyle = AnyShapeStyle(Color.white.opacity(0.12)),
        @ViewBuilder content: () -> Content
    ) -> some View {
        content()
            .font(.system(size: 16, weight: .semibold))
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(fill, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct LightsWidget: Widget {
    let kind = "LightsWidget"

    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: kind, intent: BridgeConfigIntent.self,
                               provider: LightsProvider()) { entry in
            LightsWidgetView(entry: entry)
        }
        .configurationDisplayName("iCUE Lights")
        .description("Power, brightness and scenes for your iCUE lighting.")
        .supportedFamilies([.systemSmall])
    }
}

@main
struct LightsWidgetBundle: WidgetBundle {
    var body: some Widget {
        LightsWidget()
        ProbeWidget()
    }
}
