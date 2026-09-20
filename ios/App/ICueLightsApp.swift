import SwiftUI

@main
struct ICueLightsApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}

/// Companion app: checks the bridge connection (which also triggers iOS's local-network
/// permission prompt) and holds the address/token so you can copy them into the widget.
struct ContentView: View {
    @AppStorage("host") private var host = ""
    @AppStorage("token") private var token = ""
    @State private var status = "Not tested yet."
    @State private var testing = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Bridge on your PC") {
                    TextField("192.168.x.x:8765", text: $host)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Token", text: $token)
                }

                Section {
                    Button("Test connection") { Task { await test() } }
                        .disabled(testing)
                    Text(status).foregroundStyle(.secondary)
                }

                Section("Widget setup") {
                    Text("Add the iCUE Lights widget, touch and hold it, tap Edit Widget, and enter the same address and token.")
                    Button("Copy address") { UIPasteboard.general.string = host }
                    Button("Copy token") { UIPasteboard.general.string = token }
                }
            }
            .navigationTitle("iCUE Lights")
        }
    }

    private func test() async {
        testing = true
        defer { testing = false }
        do {
            let state = try await BridgeClient(host: host, token: token).state()
            let level = state.power ? "\(state.brightness)%" : "off"
            status = "Connected: \(state.deviceCount) iCUE device(s), lights \(level)."
        } catch {
            status = "Failed: \(error.localizedDescription)"
        }
    }
}
