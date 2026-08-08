// CleanupServer.swift — resident llama-server child process for the local
// self-correction cleanup pass (added 2026-08-08).
//
// Structurally a close mirror of PreviewServer.swift (same spawn/restart-
// once/log-once-on-unreachable shape) because the two are the same kind of
// thing — a resident local model server the app talks to over loopback HTTP
// — but a DIFFERENT engine for a DIFFERENT job: PreviewServer runs
// whisper-server/small.en for live speech-to-text; this runs llama-server/
// Qwen2.5-1.5B for text-to-text cleanup of the FINAL transcription only. The
// two never share a process, a port, or a request in flight.
//
// THE GOVERNING SAFETY RULE (from live testing): this model sometimes
// paraphrases ("about" -> "regarding") instead of only deleting words — a
// dictation tool must never reword the speaker. This class does not enforce
// that itself; it hands back whatever the model said. The paraphrase
// firewall is CorrectionResolver.isDeletionOnly(candidate:of:), applied by
// the CALLER (App.swift's final path / main.swift's `cleanup` subcommand)
// before the model's output is ever allowed to reach a field. Keeping the
// guard out of this class is deliberate: this class's only job is "ask the
// model, hand back what it said, fail silently" — the same narrow contract
// PreviewServer keeps for its own engine.
//
// Everything here is best-effort, same as PreviewServer: a failure — server
// won't start, crashes, times out, returns garbage — is logged once (or, for
// per-call failures, handled by the caller's own fallback) and never allowed
// to block or delay insertion. The 1.5s per-request timeout exists for
// exactly that reason: a slow cleanup must not delay insertion noticeably.

import Foundation

final class CleanupServer {
    private let config: Config
    private var process: Process?
    private var hasRestartedOnCrash = false
    private var loggedUnreachable = false
    private let stateQueue = DispatchQueue(label: "quill-dictate.cleanup-server.state")

    private static let binaryPath = "/opt/homebrew/bin/llama-server"

    private static let systemPrompt = """
    You fix dictated text. The speaker sometimes corrects themselves mid-sentence. Output ONLY the corrected text with self-corrections resolved. You may only DELETE the speaker's words - never add, replace, or rephrase anything. If there is no self-correction, output the text exactly unchanged.
    """

    /// One session for every cleanup request, same reasoning as
    /// PreviewServer's session: a per-request URLSession leaks worker
    /// threads, and this fires once per qualifying final transcription for
    /// as long as the app runs. (PreviewServer's own per-request-session
    /// leak was fixed before this class was written — this copies the fixed
    /// pattern from the start rather than repeating the mistake.)
    private let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 2.0
        c.timeoutIntervalForResource = 2.0
        return URLSession(configuration: c)
    }()

    init(config: Config) {
        self.config = config
    }

    /// True once a spawn attempt has been made and the process object
    /// exists. Does not guarantee the HTTP server is actually accepting
    /// connections yet (llama-server takes a moment to load the model) —
    /// cleanup(text:completion:) tolerates that via its own short timeout
    /// and silent failure, exactly like PreviewServer.transcribe.
    var isSpawned: Bool {
        stateQueue.sync { process != nil }
    }

    /// Spawn the resident server. Safe to call even when prerequisites are
    /// missing — logs once and leaves `process` nil, so `isSpawned` stays
    /// false and callers skip the cleanup path entirely.
    func start() {
        guard FileManager.default.isExecutableFile(atPath: CleanupServer.binaryPath) else {
            Log.shared.line("WARN cleanup: llama-server not found at \(CleanupServer.binaryPath) — cleanup disabled")
            return
        }
        guard FileManager.default.fileExists(atPath: config.cleanupModelPath) else {
            Log.shared.line("WARN cleanup: model missing at \(config.cleanupModelPath) — cleanup disabled")
            return
        }
        spawn()
    }

    private func spawn() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: CleanupServer.binaryPath)
        p.arguments = ["-m", config.cleanupModelPath, "--host", "127.0.0.1",
                       "--port", String(config.cleanupServerPort), "-c", "1024"]
        // Discard stdout/stderr into a pipe rather than inheriting the app's —
        // llama-server is chatty per-request and none of it belongs in the
        // menu-bar app's own log.
        let sink = Pipe()
        p.standardOutput = sink
        p.standardError = sink
        p.terminationHandler = { [weak self] proc in
            guard let self else { return }
            Log.shared.line("WARN cleanup: llama-server exited (status \(proc.terminationStatus))")
            self.stateQueue.sync { self.process = nil }
            self.restartOnceOnCrash()
        }

        do {
            try p.run()
            stateQueue.sync { self.process = p }
            Log.shared.line("INFO cleanup: llama-server spawned pid=\(p.processIdentifier) port=\(config.cleanupServerPort) model=\(config.cleanupModelPath)")
        } catch {
            Log.shared.line("WARN cleanup: failed to spawn llama-server: \(error)")
        }
    }

    /// One restart attempt only — a server that keeps crashing is a config
    /// problem, not something to retry forever in the background of a
    /// dictation app.
    private func restartOnceOnCrash() {
        guard !hasRestartedOnCrash else {
            Log.shared.line("WARN cleanup: llama-server crashed again — not restarting further")
            return
        }
        hasRestartedOnCrash = true
        Log.shared.line("INFO cleanup: restarting llama-server once after crash")
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

    /// POST `text` to llama-server's OpenAI-compatible /v1/chat/completions
    /// and return whatever it says the self-correction-resolved text is.
    /// Fails silently (completion(nil)) on any error — server not up yet,
    /// timeout, bad JSON — logging only the FIRST such failure so a downed
    /// server doesn't spam the log every call. The caller is responsible for
    /// the paraphrase-firewall check (CorrectionResolver.isDeletionOnly)
    /// before trusting anything this hands back.
    func cleanup(text: String, completion: @escaping (String?) -> Void) {
        guard isSpawned else { completion(nil); return }

        let url = URL(string: "http://127.0.0.1:\(config.cleanupServerPort)/v1/chat/completions")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // 1.5s: a slow cleanup must not delay insertion noticeably — the
        // caller is holding the final-insert path open on this response.
        request.timeoutInterval = 1.5

        let body: [String: Any] = [
            "messages": [
                ["role": "system", "content": CleanupServer.systemPrompt],
                ["role": "user", "content": text],
            ],
            "temperature": 0,
            "max_tokens": 200,
        ]
        guard let bodyData = try? JSONSerialization.data(withJSONObject: body) else {
            completion(nil)
            return
        }
        request.httpBody = bodyData

        let task = session.dataTask(with: request) { [weak self] data, _, error in
            guard let self else { completion(nil); return }
            if let error {
                self.logUnreachableOnce("request failed: \(error)")
                completion(nil)
                return
            }
            guard let data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let choices = json["choices"] as? [[String: Any]],
                  let first = choices.first,
                  let message = first["message"] as? [String: Any],
                  let content = message["content"] as? String else {
                self.logUnreachableOnce("unparseable response")
                completion(nil)
                return
            }
            completion(content.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        task.resume()
    }

    private func logUnreachableOnce(_ detail: String) {
        stateQueue.sync {
            guard !loggedUnreachable else { return }
            loggedUnreachable = true
            Log.shared.line("WARN cleanup: server unreachable (\(detail)) — cleanup will fall back to heuristic until it recovers")
        }
    }
}
