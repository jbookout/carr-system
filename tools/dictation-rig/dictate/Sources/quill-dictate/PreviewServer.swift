// PreviewServer.swift — resident whisper-server child process for the live
// preview overlay (added 2026-08-08).
//
// Deliberately a SECOND engine from Transcriber's whisper-cli: the final
// insert path stays on whisper-cli + large-v3-turbo (decision 28e35509,
// unchanged), because that is the accuracy bar Joe signed off on. The preview
// only needs to be fast enough to feel live, so it runs small.en behind
// whisper-server's HTTP /inference endpoint, resident for the life of the
// app instead of spawned per utterance (spawn latency would defeat the
// point of a "live" preview).
//
// Everything here is best-effort. A preview failure — server won't start,
// crashes, times out, returns garbage — is logged once and otherwise
// invisible: it must never touch the push-to-talk capture/transcribe/insert
// pipeline, which is why every failure path here is a silent `nil`/return
// rather than a thrown error the caller would have to handle.

import Foundation

final class PreviewServer {
    private let config: Config
    private var process: Process?
    private var hasRestartedOnCrash = false
    private var loggedUnreachable = false
    private let stateQueue = DispatchQueue(label: "quill-dictate.preview-server.state")

    private static let binaryPath = "/opt/homebrew/bin/whisper-server"

    /// One session for every preview request. A per-request URLSession is a
    /// resource leak: sessions hold worker threads until invalidated, and the
    /// preview fires about once a second for as long as a key is held.
    private let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 2.0
        c.timeoutIntervalForResource = 2.0
        return URLSession(configuration: c)
    }()

    init(config: Config) {
        self.config = config
    }

    /// True once a spawn attempt has been made and the process object exists.
    /// Does not guarantee the HTTP server is actually accepting connections
    /// yet (whisper-server takes a moment to load the model) — transcribe(_:)
    /// tolerates that via its own short timeout and silent failure.
    var isSpawned: Bool {
        stateQueue.sync { process != nil }
    }

    /// Spawn the resident server. Safe to call even when prerequisites are
    /// missing — logs once and leaves `process` nil, so `isSpawned` stays
    /// false and callers skip the preview path entirely.
    func start() {
        guard FileManager.default.isExecutableFile(atPath: PreviewServer.binaryPath) else {
            Log.shared.line("WARN preview: whisper-server not found at \(PreviewServer.binaryPath) — live preview disabled")
            return
        }
        guard FileManager.default.fileExists(atPath: config.previewModelPath) else {
            Log.shared.line("WARN preview: model missing at \(config.previewModelPath) — live preview disabled")
            return
        }
        spawn()
    }

    private func spawn() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: PreviewServer.binaryPath)
        p.arguments = ["-m", config.previewModelPath, "--host", "127.0.0.1",
                       "--port", String(config.previewServerPort)]
        // Discard stdout/stderr into a pipe rather than inheriting the app's —
        // whisper-server is chatty per-request and none of it belongs in the
        // menu-bar app's own log.
        let sink = Pipe()
        p.standardOutput = sink
        p.standardError = sink
        p.terminationHandler = { [weak self] proc in
            guard let self else { return }
            Log.shared.line("WARN preview: whisper-server exited (status \(proc.terminationStatus))")
            self.stateQueue.sync { self.process = nil }
            self.restartOnceOnCrash()
        }

        do {
            try p.run()
            stateQueue.sync { self.process = p }
            Log.shared.line("INFO preview: whisper-server spawned pid=\(p.processIdentifier) port=\(config.previewServerPort) model=\(config.previewModelPath)")
        } catch {
            Log.shared.line("WARN preview: failed to spawn whisper-server: \(error)")
        }
    }

    /// One restart attempt only — a server that keeps crashing is a config
    /// problem, not something to retry forever in the background of a
    /// dictation app.
    private func restartOnceOnCrash() {
        guard !hasRestartedOnCrash else {
            Log.shared.line("WARN preview: whisper-server crashed again — not restarting further")
            return
        }
        hasRestartedOnCrash = true
        Log.shared.line("INFO preview: restarting whisper-server once after crash")
        spawn()
    }

    /// Terminate the child. Called from applicationWillTerminate; never
    /// blocks waiting on a graceful shutdown longer than the process needs.
    func stop() {
        stateQueue.sync {
            guard let process, process.isRunning else { return }
            process.terminationHandler = nil // this is an intentional stop, not a crash
            process.terminate()
            self.process = nil
        }
    }

    /// POST a WAV chunk to whisper-server's /inference and return whatever
    /// text it hypothesizes. Fails silently (completion(nil)) on any error —
    /// server not up yet, timeout, bad JSON — logging only the FIRST such
    /// failure so a downed server doesn't spam the log every preview tick.
    func transcribe(wavData: Data, completion: @escaping (String?) -> Void) {
        guard isSpawned else { completion(nil); return }

        let url = URL(string: "http://127.0.0.1:\(config.previewServerPort)/inference")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 2.0

        let boundary = "quill-dictate-preview-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = PreviewServer.multipartBody(boundary: boundary, wavData: wavData)

        let task = session.dataTask(with: request) { [weak self] data, _, error in
            guard let self else { completion(nil); return }
            if let error {
                self.logUnreachableOnce("request failed: \(error)")
                completion(nil)
                return
            }
            guard let data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let text = json["text"] as? String else {
                self.logUnreachableOnce("unparseable response")
                completion(nil)
                return
            }
            completion(text.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        task.resume()
    }

    private func logUnreachableOnce(_ detail: String) {
        stateQueue.sync {
            guard !loggedUnreachable else { return }
            loggedUnreachable = true
            Log.shared.line("WARN preview: server unreachable (\(detail)) — preview will stay blank until it recovers")
        }
    }

    private static func multipartBody(boundary: String, wavData: Data) -> Data {
        var body = Data()
        func append(_ string: String) { body.append(Data(string.utf8)) }

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"chunk.wav\"\r\n")
        append("Content-Type: audio/wav\r\n\r\n")
        body.append(wavData)
        append("\r\n")

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"temperature\"\r\n\r\n")
        append("0.0\r\n")

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"response_format\"\r\n\r\n")
        append("json\r\n")

        append("--\(boundary)--\r\n")
        return body
    }
}
