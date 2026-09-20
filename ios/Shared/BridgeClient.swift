import Foundation

struct BridgePreset: Decodable, Hashable, Identifiable {
    let id: String
    let name: String
    let brightness: Int
}

struct BridgeState: Decodable {
    let power: Bool
    let brightness: Int
    let preset: String?  // id of the preset matching the current brightness, if any
    let presets: [BridgePreset]
    let connected: Bool
    let deviceCount: Int
}

enum BridgeError: LocalizedError {
    case notConfigured
    case unauthorized
    case http(Int)

    var errorDescription: String? {
        switch self {
        case .notConfigured: return "Set the bridge address and token."
        case .unauthorized: return "Wrong token."
        case .http(let code): return "Bridge returned HTTP \(code)."
        }
    }
}

/// Talks to the PC bridge (see bridge/server.py). Every call returns the new state.
struct BridgeClient {
    let host: String
    let token: String

    private var baseURL: URL? {
        let trimmed = host.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return URL(string: trimmed.contains("://") ? trimmed : "http://\(trimmed)")
    }

    var isConfigured: Bool { baseURL != nil && !token.isEmpty }

    func state() async throws -> BridgeState { try await send("state", method: "GET") }

    @discardableResult
    func setPower(_ on: Bool) async throws -> BridgeState {
        try await send("power", body: ["on": on])
    }

    @discardableResult
    func stepBrightness(_ delta: Int) async throws -> BridgeState {
        try await send("brightness", body: ["delta": delta])
    }

    @discardableResult
    func selectPreset(_ id: String) async throws -> BridgeState {
        try await send("preset/\(id)")
    }

    private func send(_ path: String, method: String = "POST",
                      body: [String: Any]? = nil) async throws -> BridgeState {
        guard isConfigured, let base = baseURL else { throw BridgeError.notConfigured }
        var request = URLRequest(url: base.appendingPathComponent(path), timeoutInterval: 5)
        request.httpMethod = method
        request.setValue(token, forHTTPHeaderField: "X-Token")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        switch code {
        case 200: return try JSONDecoder().decode(BridgeState.self, from: data)
        case 401: throw BridgeError.unauthorized
        default: throw BridgeError.http(code)
        }
    }
}
