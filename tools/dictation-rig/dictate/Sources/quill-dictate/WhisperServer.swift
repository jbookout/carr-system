// WhisperServer.swift — resident whisper-server child process, shared by two
// roles (generalized 2026-08-08 from the preview-only PreviewServer; the
// class and file were renamed in the same pass — this history is preserved
// via `git mv`, not a delete+add).
//
// PREVIEW (added 2026-08-08): the live preview overlay/inline typer. Runs
// small.en on preview_server_port for speed — the preview only needs to feel
// live, so it deliberately trades accuracy for latency and stays resident
// for the life of the app instead of spawned per utterance (spawn latency
// would defeat the point of a "live" preview).
//
// FINAL (added 2026-08-08): the same engine and model as Transcriber's
// whisper-cli fallback (model_path, large-v3-turbo — decision 28e35509,
// unchanged) on final_server_port, kept resident so the ~1.6GB model load
// and ~14s Metal cold start are paid once per app launch instead of once per
// utterance. This class owns ONLY that process's lifecycle (spawn at
// launch, stop at terminate, restart once on crash) — the FINAL request
// itself is issued directly by Transcriber.swift over HTTP, not through this
// class's own transcribe(wavData:completion:), because Transcriber must also
// work standalone from the CLI (`quill-dictate transcribe`) with no
// WhisperServer instance — any App-owned one or otherwise — in the process
// at all. See Transcriber.swift's header and WhisperServer.postFinalInference
// below.
//
// Everything here is best-effort. A failure — server won't start, crashes,
// times out, returns garbage — is logged once and otherwise invisible: for
// PREVIEW it must never touch the push-to-talk capture/transcribe/insert
// pipeline (every failure path is a silent `nil`/return rather than a thrown
// error); for FINAL, Transcriber's own fallback to whisper-cli is the
// answer to every failure mode, so this class doesn't need to distinguish
// them either.

import Foundation

final class WhisperServer {
    enum Role {
        case preview
        case final

        var tag: String {
            switch self {
            case .preview: return "preview"
            case .final: return "final"
            }
        }
    }

    private let role: Role
    private let modelPath: String
    private let port: Int
    private var process: Process?
    private var hasRestartedOnCrash = false
    private var loggedUnreachable = false
    private var consecutivePreviewFailures = 0
    private var restartInProgress = false
    private var generation = 0
    private let stateQueue: DispatchQueue

    /// Not `private` — Transcriber.swift's tryServerTranscribe checks this
    /// same path before paying the connect-retry cost in postFinalInference,
    /// so a whisper-server that was NEVER going to start (missing binary)
    /// fails instantly instead of waiting out the full connect ceiling. See
    /// postFinalInference's header for why that distinction matters.
    static let binaryPath = "/opt/homebrew/bin/whisper-server"

    /// Kill any leftover process of OUR server binary still bound to `port`
    /// (see spawn()'s comment for why orphans exist at all). pkill -f with
    /// binary name + exact port is the narrowest match the command line
    /// offers; blocking wait is fine — this runs once per spawn, at launch.
    static func killStaleListener(binaryName: String, port: Int) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        p.arguments = ["-f", "\(binaryName).*--port \(port)"]
        try? p.run()
        p.waitUntilExit()
        if p.terminationStatus == 0 {
            Log.shared.line("INFO reclaimed port \(port) from a stale \(binaryName) (orphaned by a hard restart)")
            usleep(300_000) // give the socket a beat to actually release
        }
    }

    /// One session for every preview request. A per-request URLSession is a
    /// resource leak: sessions hold worker threads until invalidated, and the
    /// preview fires about once a second for as long as a key is held. (The
    /// FINAL role never uses this session — its request is a one-shot call
    /// from Transcriber via postFinalInference, which builds its own
    /// session sized to the 30s final-pass timeout instead of this 2s one.)
    private let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 2.0
        c.timeoutIntervalForResource = 2.0
        return URLSession(configuration: c)
    }()

    init(role: Role, config: Config) {
        self.role = role
        switch role {
        case .preview:
            self.modelPath = config.previewModelPath
            self.port = config.previewServerPort
        case .final:
            self.modelPath = config.modelPath
            self.port = config.finalServerPort
        }
        self.stateQueue = DispatchQueue(label: "quill-dictate.\(role.tag)-server.state")
    }

    /// True once a spawn attempt has been made and the process object exists.
    /// Does not guarantee the HTTP server is actually accepting connections
    /// yet (whisper-server takes a moment to load the model) — callers
    /// tolerate that via their own timeout and silent failure.
    var isSpawned: Bool {
        stateQueue.sync { process != nil }
    }

    /// Spawn the resident server. Safe to call even when prerequisites are
    /// missing — logs once and leaves `process` nil, so `isSpawned` stays
    /// false and callers skip this role's path entirely.
    func start() {
        guard FileManager.default.isExecutableFile(atPath: WhisperServer.binaryPath) else {
            Log.shared.line("WARN \(role.tag): whisper-server not found at \(WhisperServer.binaryPath) — \(role.tag) disabled")
            return
        }
        guard FileManager.default.fileExists(atPath: modelPath) else {
            Log.shared.line("WARN \(role.tag): model missing at \(modelPath) — \(role.tag) disabled")
            return
        }
        spawn()
    }

    private func spawn() {
        // Pre-kill any stale server holding this port. applicationWillTerminate
        // never runs when launchd SIGKILLs the app (`kickstart -k`), so every
        // hard restart orphans the previous children — found live 2026-08-08
        // with EIGHT orphaned servers from one morning of restarts. Each spawn
        // reclaiming its own port is self-healing for every past and future
        // orphan; scoped to our binary name + exact port so it can never touch
        // anything else.
        WhisperServer.killStaleListener(binaryName: "whisper-server", port: port)
        let p = Process()
        p.executableURL = URL(fileURLWithPath: WhisperServer.binaryPath)
        p.arguments = ["-m", modelPath, "--host", "127.0.0.1", "--port", String(port)]
        // These are long-lived, chatty servers. An unread Pipe is NOT a sink:
        // macOS fills its 16 KiB buffer, then blocks the child on its next
        // write while the listener misleadingly stays alive. That exact
        // failure wedged all three resident Quill servers on 2026-08-10.
        // /dev/null is the real discard target; Quill logs lifecycle and
        // request failures itself below.
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { [weak self] proc in
            guard let self else { return }
            Log.shared.line("WARN \(self.role.tag): whisper-server exited (status \(proc.terminationStatus))")
            self.stateQueue.sync { self.process = nil }
            self.restartOnceOnCrash()
        }

        do {
            try p.run()
            stateQueue.sync {
                self.process = p
                self.generation += 1
            }
            Log.shared.line("INFO \(role.tag): whisper-server spawned pid=\(p.processIdentifier) port=\(port) model=\(modelPath)")
        } catch {
            Log.shared.line("WARN \(role.tag): failed to spawn whisper-server: \(error)")
        }
    }

    /// One restart attempt only — a server that keeps crashing is a config
    /// problem, not something to retry forever in the background of a
    /// dictation app.
    private func restartOnceOnCrash() {
        guard !hasRestartedOnCrash else {
            Log.shared.line("WARN \(role.tag): whisper-server crashed again — not restarting further")
            return
        }
        hasRestartedOnCrash = true
        Log.shared.line("INFO \(role.tag): restarting whisper-server once after crash")
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
    /// text it hypothesizes. PREVIEW role only — the FINAL role's request
    /// goes through postFinalInference below instead. Fails silently
    /// (completion(nil)) on any error — server not up yet, timeout, bad
    /// JSON — logging only the FIRST such failure so a downed server doesn't
    /// spam the log every preview tick.
    func transcribe(wavData: Data, completion: @escaping (String?) -> Void) {
        guard isSpawned else { completion(nil); return }
        let requestGeneration = stateQueue.sync { generation }

        let url = URL(string: "http://127.0.0.1:\(port)/inference")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 2.0

        let boundary = "quill-dictate-\(role.tag)-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = WhisperServer.multipartBody(boundary: boundary, wavData: wavData, prompt: nil)

        let task = session.dataTask(with: request) { [weak self] data, _, error in
            guard let self else { completion(nil); return }
            if let error {
                self.notePreviewFailure("request failed: \(error)", generation: requestGeneration)
                completion(nil)
                return
            }
            guard let data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let text = json["text"] as? String else {
                self.notePreviewFailure("unparseable response", generation: requestGeneration)
                completion(nil)
                return
            }
            self.notePreviewSuccess(generation: requestGeneration)
            completion(text.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        task.resume()
    }

    /// A single 2s preview miss can be ordinary page-in pressure, especially
    /// on the first utterance after sleep. Three consecutive misses mean the
    /// resident process is not serving useful work; recycle it even though
    /// it may still own a listening socket.
    private func notePreviewFailure(_ detail: String, generation requestGeneration: Int) {
        var shouldLog = false
        var shouldRestart = false
        var failureCount = 0
        stateQueue.sync {
            guard requestGeneration == generation else { return }
            consecutivePreviewFailures += 1
            failureCount = consecutivePreviewFailures
            if !loggedUnreachable {
                loggedUnreachable = true
                shouldLog = true
            }
            if consecutivePreviewFailures >= 3, !restartInProgress {
                restartInProgress = true
                shouldRestart = true
            }
        }
        if shouldLog {
            Log.shared.line("WARN \(role.tag): server unreachable (\(detail)) — retrying; health restart after 3 consecutive failures")
        }
        if shouldRestart {
            restartUnresponsive(reason: "\(failureCount) consecutive preview failures")
        }
    }

    private func notePreviewSuccess(generation requestGeneration: Int) {
        var recovered = false
        stateQueue.sync {
            guard requestGeneration == generation else { return }
            recovered = consecutivePreviewFailures > 0 || loggedUnreachable
            consecutivePreviewFailures = 0
            loggedUnreachable = false
        }
        if recovered {
            Log.shared.line("INFO \(role.tag): server responding again")
        }
    }

    /// Stop an unresponsive FINAL listener before running whisper-cli, then
    /// start a clean resident server after the fallback finishes. Keeping the
    /// wedged large model alive while the CLI loads the same model can exhaust
    /// Metal resources and made the fallback itself crash with signal 11 in
    /// the 2026-08-10 failure.
    func withFinalServerStoppedForRecovery<T>(
        reason: String,
        fallback: () throws -> T
    ) rethrows -> T {
        switch role {
        case .preview: return try fallback()
        case .final: break
        }

        let recovery: (started: Bool, process: Process?) = stateQueue.sync {
            guard !restartInProgress else { return (false, nil) }
            restartInProgress = true
            let old = process
            process = nil
            return (true, old)
        }
        guard recovery.started else { return try fallback() }

        if let oldProcess = recovery.process {
            oldProcess.terminationHandler = nil
            if oldProcess.isRunning { oldProcess.terminate() }
        }
        Log.shared.line("WARN final: recycling unresponsive resident server (\(reason)); cli fallback owns this utterance")

        defer {
            // spawn() first reclaims any listener that ignored terminate(),
            // so the new process can never lose a race for the fixed port.
            spawn()
            stateQueue.sync {
                restartInProgress = false
                loggedUnreachable = false
            }
        }
        return try fallback()
    }

    /// Preview has no alternate engine to own the utterance, so recycle the
    /// server immediately after the threshold instead of waiting for another
    /// capture. Called from URLSession's callback queue, never the event tap.
    private func restartUnresponsive(reason: String) {
        let oldProcess: Process? = stateQueue.sync {
            let old = process
            process = nil
            return old
        }
        if let oldProcess {
            oldProcess.terminationHandler = nil
            if oldProcess.isRunning { oldProcess.terminate() }
        }
        Log.shared.line("WARN \(role.tag): recycling unresponsive resident server (\(reason))")
        spawn()
        stateQueue.sync {
            consecutivePreviewFailures = 0
            loggedUnreachable = false
            restartInProgress = false
        }
    }

    /// Shared multipart-body builder for both roles. `prompt`, when
    /// non-nil, adds the vocab-bias form field the FINAL role needs (see
    /// postFinalInference) — PREVIEW never passes one.
    private static func multipartBody(boundary: String, wavData: Data, prompt: String?) -> Data {
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

        if let prompt, !prompt.isEmpty {
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"prompt\"\r\n\r\n")
            append("\(prompt)\r\n")
        }

        append("--\(boundary)--\r\n")
        return body
    }

    /// Blocking POST for the FINAL pass. Transcriber.swift's server-first
    /// path calls this directly rather than through an instance's
    /// transcribe(_:completion:) — Transcriber must work standalone
    /// (`quill-dictate transcribe`, main.swift) with no WhisperServer
    /// instance owned by any process; it simply talks to whatever is (or
    /// isn't) listening on `port`. Returns nil on ANY failure so the
    /// caller's existing whisper-cli fallback is the single answer to every
    /// failure mode, not something this function sorts into categories.
    ///
    /// TWO PHASES, not one long-timeout request — corrected 2026-08-08 from
    /// the original single-POST design after live measurement showed the
    /// original's assumption was wrong for this whisper-server build: a
    /// connection attempt made while the model is still loading is REFUSED
    /// outright (ECONNREFUSED), not accepted-and-then-delayed, so a single
    /// request fired during the ~2-14s post-spawn load window would fail
    /// instantly and never get the "waits through load" behavior the
    /// caller needs. Phase 1 (waitForPort) retries a cheap connection probe
    /// every 200ms — same interval main.swift's runCleanup already polls
    /// llama-server's /health with — until the port answers or a bounded
    /// ceiling passes; phase 2 fires the real /inference request once,
    /// against a server already known to be accepting connections, with
    /// whatever's left of `timeoutSeconds` as its own budget (comfortably
    /// enough for "long utterances are legal" after a typical load).
    static func postFinalInference(port: Int, wavData: Data, prompt: String?, timeoutSeconds: TimeInterval) -> String? {
        let deadline = Date().addingTimeInterval(timeoutSeconds)
        // Cap the connect-wait phase well under the full budget — covers the
        // documented ~10-14s cold start with margin, but a final server
        // that's OFF/misconfigured/permanently crashed (doctor's "final
        // server" row exists to catch this) fails in ~20s, not the full 30,
        // leaving the rest of `timeoutSeconds` for the request itself.
        let connectDeadline = min(deadline, Date().addingTimeInterval(20.0))
        guard waitForPort(port: port, deadline: connectDeadline) else { return nil }

        let remaining = max(5.0, deadline.timeIntervalSinceNow)
        return performInferenceRequest(port: port, wavData: wavData, prompt: prompt, timeoutSeconds: remaining)
    }

    /// Headless diagnostics use the same HTTP-response test as the final
    /// path. A listening socket alone is insufficient: the unread-pipe bug
    /// left ports open while their server threads were permanently blocked.
    static func portResponds(port: Int, timeoutSeconds: TimeInterval = 2.0) -> Bool {
        waitForPort(port: port, deadline: Date().addingTimeInterval(timeoutSeconds))
    }

    /// Retries a cheap connection probe against the server's root until it
    /// answers (any HTTP response counts — status code is irrelevant, this
    /// only cares whether something is listening) or `deadline` passes.
    private static func waitForPort(port: Int, deadline: Date) -> Bool {
        let probeConfig = URLSessionConfiguration.ephemeral
        probeConfig.timeoutIntervalForRequest = 1.0
        probeConfig.timeoutIntervalForResource = 1.0
        let probeSession = URLSession(configuration: probeConfig)
        let url = URL(string: "http://127.0.0.1:\(port)/")!

        while Date() < deadline {
            let semaphore = DispatchSemaphore(value: 0)
            var reachable = false
            let task = probeSession.dataTask(with: url) { _, response, _ in
                reachable = response is HTTPURLResponse
                semaphore.signal()
            }
            task.resume()
            _ = semaphore.wait(timeout: .now() + 1.5)
            if reachable { return true }
            usleep(200_000) // 200ms — same poll interval main.swift's runCleanup uses for llama-server
        }
        return false
    }

    private static func performInferenceRequest(port: Int, wavData: Data, prompt: String?, timeoutSeconds: TimeInterval) -> String? {
        let url = URL(string: "http://127.0.0.1:\(port)/inference")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = timeoutSeconds

        let boundary = "quill-dictate-final-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = multipartBody(boundary: boundary, wavData: wavData, prompt: prompt)

        let sessionConfig = URLSessionConfiguration.ephemeral
        sessionConfig.timeoutIntervalForRequest = timeoutSeconds
        sessionConfig.timeoutIntervalForResource = timeoutSeconds
        let session = URLSession(configuration: sessionConfig)

        let semaphore = DispatchSemaphore(value: 0)
        var result: String?
        let task = session.dataTask(with: request) { data, _, error in
            defer { semaphore.signal() }
            guard error == nil,
                  let data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let text = json["text"] as? String else { return }
            result = text.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        task.resume()
        // +1.0s margin over the request's own timeout so URLSession's
        // internal timeout always fires and signals the semaphore first,
        // rather than this wait racing it and returning nil while the
        // request is technically still in flight.
        _ = semaphore.wait(timeout: .now() + timeoutSeconds + 1.0)
        return result
    }
}
