import SwiftUI
import WidgetKit

/// Bare-bones widget with no App Intents. If this shows up in the widget gallery but
/// LightsWidget doesn't, the problem is App Intents; if neither shows, the extension isn't
/// being registered at all. Delete this file and its line in LightsWidgetBundle once sorted.
struct ProbeEntry: TimelineEntry {
    let date: Date
}

struct ProbeProvider: TimelineProvider {
    func placeholder(in context: Context) -> ProbeEntry { ProbeEntry(date: Date()) }

    func getSnapshot(in context: Context, completion: @escaping (ProbeEntry) -> Void) {
        completion(ProbeEntry(date: Date()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<ProbeEntry>) -> Void) {
        completion(Timeline(entries: [ProbeEntry(date: Date())], policy: .never))
    }
}

struct ProbeWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "ProbeWidget", provider: ProbeProvider()) { _ in
            Text("Widget works")
                .font(.system(size: 14, weight: .semibold, design: .rounded))
                .foregroundStyle(.white)
                .containerBackground(.black, for: .widget)
        }
        .configurationDisplayName("iCUE widget test")
        .description("Plain test widget with no App Intents.")
        .supportedFamilies([.systemSmall])
    }
}
