import AppIntents
import WidgetKit

/// Widget settings (long-press the widget > Edit Widget). Widgets can't share storage with
/// the app without App Groups, so the bridge address and token live on the widget itself.
struct BridgeConfigIntent: WidgetConfigurationIntent {
    static let title: LocalizedStringResource = "iCUE Bridge"
    static let description = IntentDescription("Address and token of the bridge running on your PC.")

    @Parameter(title: "Address", default: "")
    var host: String

    @Parameter(title: "Token", default: "")
    var token: String
}

// The button intents carry host/token with them because they run without the widget's config.
// WidgetKit reloads the timeline after each one, which re-fetches the fresh state.

struct SetPowerIntent: AppIntent {
    static let title: LocalizedStringResource = "Set lights power"
    static let isDiscoverable = false

    @Parameter(title: "Address") var host: String
    @Parameter(title: "Token") var token: String
    @Parameter(title: "On") var on: Bool

    init() {}
    init(host: String, token: String, on: Bool) {
        self.host = host
        self.token = token
        self.on = on
    }

    func perform() async throws -> some IntentResult {
        try await BridgeClient(host: host, token: token).setPower(on)
        return .result()
    }
}

struct StepBrightnessIntent: AppIntent {
    static let title: LocalizedStringResource = "Change brightness"
    static let isDiscoverable = false

    @Parameter(title: "Address") var host: String
    @Parameter(title: "Token") var token: String
    @Parameter(title: "Delta") var delta: Int

    init() {}
    init(host: String, token: String, delta: Int) {
        self.host = host
        self.token = token
        self.delta = delta
    }

    func perform() async throws -> some IntentResult {
        try await BridgeClient(host: host, token: token).stepBrightness(delta)
        return .result()
    }
}

struct SelectSceneIntent: AppIntent {
    static let title: LocalizedStringResource = "Select scene"
    static let isDiscoverable = false

    @Parameter(title: "Address") var host: String
    @Parameter(title: "Token") var token: String
    @Parameter(title: "Scene") var sceneID: String

    init() {}
    init(host: String, token: String, sceneID: String) {
        self.host = host
        self.token = token
        self.sceneID = sceneID
    }

    func perform() async throws -> some IntentResult {
        try await BridgeClient(host: host, token: token).selectScene(sceneID)
        return .result()
    }
}
