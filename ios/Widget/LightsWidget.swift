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
        power: true, brightness: 50, preset: "day",
        presets: [
            BridgePreset(id: "day", name: "Day", brightness: 50),
            BridgePreset(id: "night", name: "Night", brightness: 20),
            BridgePreset(id: "movie", name: "Movie", brightness: 5),
        ],
        connected: true, deviceCount: 2)
}

struct LightsWidgetView: View {
    let entry: LightsEntry

    /// Warm accent for the active button and the glow. The bridge never sends colors: the
    /// lights keep whatever colors you set in iCUE and OpenRGB, and this only changes brightness.
    private static let accent = Color(red: 1.0, green: 0.6, blue: 0.25)

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
                RadialGradient(colors: [Self.accent.opacity(glowStrength), .clear], center: .top,
                               startRadius: 0, endRadius: 150)
            }
        }
    }

    /// The background glow follows the real brightness, so the widget itself dims with the lights.
    private var glowStrength: Double {
        guard let state = entry.state, state.power else { return 0 }
        return 0.35 * Double(state.brightness) / 100
    }

    private func controls(_ state: BridgeState) -> some View {
        VStack(spacing: 6) {
            HStack {
                Text(state.power ? "\(state.brightness)%" : "Off")
                Spacer()
                if state.power, let active = state.presets.first(where: { $0.id == state.preset }) {
                    Text(active.name).lineLimit(1)
                }
            }
            .font(.system(size: 11, weight: .medium, design: .rounded))
            .foregroundStyle(.white.opacity(0.6))

            HStack(spacing: 6) {
                Button(intent: SetPowerIntent(host: entry.host, token: entry.token, on: !state.power)) {
                    tile(fill: AnyShapeStyle(state.power ? Self.accent.opacity(0.9) : Color.white.opacity(0.12))) {
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
                ForEach(state.presets.prefix(3)) { preset in
                    presetButton(preset, state: state)
                }
            }
        }
        .buttonStyle(.plain)
    }

    private func presetButton(_ preset: BridgePreset, state: BridgeState) -> some View {
        let active = state.power && preset.id == state.preset
        return Button(intent: SelectPresetIntent(host: entry.host, token: entry.token, presetID: preset.id)) {
            tile(fill: AnyShapeStyle(active ? Self.accent.opacity(0.9) : Color.white.opacity(0.12))) {
                VStack(spacing: 1) {
                    Text(preset.name)
                        .font(.system(size: 10, weight: .bold, design: .rounded))
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                    Text("\(preset.brightness)%")
                        .font(.system(size: 9, weight: .medium, design: .rounded))
                        .opacity(0.75)
                }
                .padding(.horizontal, 2)
                .foregroundStyle(active ? Color.black : Color.white)
            }
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
        .description("Power, brightness and Day / Night / Movie presets for your lighting.")
        .supportedFamilies([.systemSmall])
    }
}

@main
struct LightsWidgetBundle: WidgetBundle {
    var body: some Widget {
        LightsWidget()
    }
}
