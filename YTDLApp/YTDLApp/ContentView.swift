import SwiftUI
import WebKit

struct ContentView: View {
  @EnvironmentObject private var server: ServerManager

  var body: some View {
    Group {
      switch server.state {
      case .idle, .starting:
        startingView

      case .ready(let port):
        WebView(url: URL(string: "http://localhost:\(port)")!)
          .ignoresSafeArea()

      case .failed(let msg):
        failedView(msg)
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
  }

  // MARK: - States

  private var startingView: some View {
    VStack(spacing: 16) {
      ProgressView()
        .scaleEffect(1.4)
      Text("Starting server…")
        .font(.system(.body, design: .monospaced))
        .foregroundStyle(.secondary)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(Color(nsColor: .windowBackgroundColor))
  }

  private func failedView(_ message: String) -> some View {
    VStack(spacing: 16) {
      Image(systemName: "exclamationmark.triangle.fill")
        .font(.system(size: 40))
        .foregroundStyle(.orange)
      Text("Server failed to start")
        .font(.headline)
      Text(message)
        .font(.caption.monospaced())
        .foregroundStyle(.secondary)
        .multilineTextAlignment(.center)
        .padding(.horizontal)
      Button("Retry") { server.start() }
        .buttonStyle(.borderedProminent)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(Color(nsColor: .windowBackgroundColor))
  }
}

// MARK: - WKWebView wrapper

struct WebView: NSViewRepresentable {
  let url: URL

  func makeNSView(context: Context) -> WKWebView {
    let config = WKWebViewConfiguration()
    config.preferences.isElementFullscreenEnabled = true
    let wv = WKWebView(frame: .zero, configuration: config)
    wv.navigationDelegate = context.coordinator
    wv.load(URLRequest(url: url))
    return wv
  }

  func updateNSView(_ nsView: WKWebView, context: Context) {}

  func makeCoordinator() -> Coordinator { Coordinator() }

  final class Coordinator: NSObject, WKNavigationDelegate {
    func webView(_ webView: WKWebView,
                 didFail navigation: WKNavigation!,
                 withError error: Error) {
      // Retry after a short delay if server isn't up yet
      DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
        if let url = webView.url { webView.load(URLRequest(url: url)) }
      }
    }
  }
}
