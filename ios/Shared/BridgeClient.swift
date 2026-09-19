import Foundation
import SwiftUI

struct BridgeScene: Decodable, Hashable, Identifiable {
    let id: String
    let name: String
    let colors: [String]
}

struct BridgeState: Decodable {
    let power: Bool
    let brightness: Int
    let scene: String
    let sceneName: String
    let connected: Bool
    let deviceCount: Int
    let scenes: [BridgeScene]
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
    func selectScene(_ id: String) async throws -> BridgeState {
        try await send("scene/\(id)")  
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

struct RGB {
    let r: Double, g: Double, b: Double  // 0...1

    init(hex: String) {
        var value: UInt64 = 0
        Scanner(string: hex.trimmingCharacters(in: CharacterSet(charactersIn: "#")))
            .scanHexInt64(&value)
        r = Double((value >> 16) & 0xFF) / 255
        g = Double((value >> 8) & 0xFF) / 255
        b = Double(value & 0xFF) / 255
    }

    var luminance: Double { 0.299 * r + 0.587 * g + 0.114 * b }
}

extension Color {
    init(hex: String) {
        let c = RGB(hex: hex)
        self.init(red: c.r, green: c.g, blue: c.b)
    }
}
