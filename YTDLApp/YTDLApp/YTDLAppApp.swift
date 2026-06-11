import SwiftUI

@main
struct YTDLAppApp: App {
  @StateObject private var server = ServerManager()

  var body: some Scene {
    WindowGroup {
      ContentView()
        .environmentObject(server)
        .frame(minWidth: 720, minHeight: 560)
        .onAppear { server.start() }
    }
    .windowStyle(.titleBar)
    .windowToolbarStyle(.unified)
    .commands {
      CommandGroup(replacing: .newItem) {}
    }
  }
}
