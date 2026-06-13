import Foundation

/// Manages the embedded Flask server subprocess.
@MainActor
final class ServerManager: ObservableObject {
  enum State { case idle, starting, ready(port: Int), failed(String) }

  @Published private(set) var state: State = .idle

  private var process: Process?
  private let port = 5001

  // MARK: - Public

  func start() {
    guard case .idle = state else { return }
    state = .starting

    Task.detached(priority: .userInitiated) { [weak self] in
      guard let self else { return }
      do {
        try await self.launch()
      } catch {
        await MainActor.run { self.state = .failed(error.localizedDescription) }
      }
    }
  }

  func stop() {
    process?.terminate()
    process = nil
    state = .idle
  }

  // MARK: - Launch

  private func launch() async throws {
    guard Bundle.main.path(forResource: "server", ofType: "py") != nil else {
      throw AppError.missingResource("server.py")
    }

    let tmpDir = FileManager.default.temporaryDirectory.appendingPathComponent("YoutubeDownloaderApp")
    try? FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)

    for (name, ext) in [("server", "py"), ("index", "html")] {
      guard let src = Bundle.main.path(forResource: name, ofType: ext) else { continue }
      let dst = tmpDir.appendingPathComponent("\(name).\(ext)").path
      if FileManager.default.fileExists(atPath: dst) { try? FileManager.default.removeItem(atPath: dst) }
      try FileManager.default.copyItem(atPath: src, toPath: dst)
    }

    let python = try Self.findPythonWithFlask()

    let proc = Process()
    proc.executableURL    = URL(fileURLWithPath: python)
    proc.arguments        = [tmpDir.appendingPathComponent("server.py").path]
    proc.currentDirectoryURL = tmpDir
    proc.environment      = Self.buildEnv(pythonPath: python)
    proc.standardOutput   = FileHandle.nullDevice
    proc.standardError    = FileHandle.nullDevice

    try proc.run()
    process = proc

    try await pollUntilReady()
    await MainActor.run { self.state = .ready(port: self.port) }
  }

  // MARK: - Readiness — poll HTTP instead of parsing stdout

  private func pollUntilReady() async throws {
    let url      = URL(string: "http://localhost:\(port)/")!
    let config   = URLSessionConfiguration.ephemeral
    config.timeoutIntervalForRequest = 1
    let session  = URLSession(configuration: config)
    let deadline = Date().addingTimeInterval(20)

    while Date() < deadline {
      if process?.isRunning == false { throw AppError.processDied }
      if let (_, resp) = try? await session.data(from: url),
         (resp as? HTTPURLResponse)?.statusCode == 200 { return }
      try await Task.sleep(nanoseconds: 500_000_000)
    }
    throw AppError.serverTimeout
  }

  // MARK: - Python discovery

  /// Returns the first Python 3 that can import flask and flask_cors.
  private static func findPythonWithFlask() throws -> String {
    let home = NSHomeDirectory()
    let candidates = [
      "\(home)/Library/Python/3.13/bin/python3",
      "\(home)/Library/Python/3.12/bin/python3",
      "\(home)/Library/Python/3.11/bin/python3",
      "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
      "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
      "/opt/homebrew/bin/python3",
      "/usr/local/bin/python3",
      "/usr/bin/python3",
    ]
    for path in candidates {
      guard FileManager.default.isExecutableFile(atPath: path) else { continue }
      let check = Process()
      check.executableURL = URL(fileURLWithPath: path)
      check.arguments     = ["-c", "import flask, flask_cors"]
      check.standardOutput = FileHandle.nullDevice
      check.standardError  = FileHandle.nullDevice
      try? check.run()
      check.waitUntilExit()
      if check.terminationStatus == 0 { return path }
    }
    throw AppError.noPythonWithFlask
  }

  private static func buildEnv(pythonPath: String) -> [String: String] {
    var env   = ProcessInfo.processInfo.environment
    let home  = NSHomeDirectory()
    let bin   = URL(fileURLWithPath: pythonPath).deletingLastPathComponent().path
    let extra = [bin,
                 "\(home)/Library/Python/3.13/bin",
                 "\(home)/Library/Python/3.12/bin",
                 "/opt/homebrew/bin",
                 "/usr/local/bin"].joined(separator: ":")
    env["PATH"]             = extra + ":" + (env["PATH"] ?? "/usr/bin:/bin")
    env["PYTHONUNBUFFERED"] = "1"
    return env
  }
}

// MARK: - Errors

enum AppError: LocalizedError {
  case missingResource(String)
  case noPythonWithFlask
  case processDied
  case serverTimeout

  var errorDescription: String? {
    switch self {
    case .missingResource(let n): "Bundled resource '\(n)' not found."
    case .noPythonWithFlask:      "No Python 3 with Flask found.\nRun: pip install flask flask-cors yt-dlp"
    case .processDied:            "Server process exited unexpectedly.\nRun: pip install flask flask-cors"
    case .serverTimeout:          "Server did not respond within 20 seconds."
    }
  }
}
