// main.swift — entry point.
//
//   quill-dictate               run the menu-bar agent (normal mode)
//   quill-dictate doctor        headless health check, exit 0 = all green
//   quill-dictate transcribe F  run one WAV through the shared engine, print text
//   quill-dictate interpret T   run LiveTyper.interpret(T), print the result
//   quill-dictate cleanup T     run the FULL final-path decision (heuristics
//                               + local-LLM cleanup) on T, print the result
//                               and engine=llm|heuristic|passthrough
//
// doctor and transcribe exist so the pipeline is provable WITHOUT the
// accessibility grant or a live keyboard — the build session verifies the
// engine headlessly, then the live gesture tests prove the rest. interpret
// (added 2026-08-08) extends that same headless-proof discipline to
// CorrectionResolver: it needs no audio and no field, just the text
// LiveTyper.interpret would have received off a whisper hypothesis. cleanup
// (added 2026-08-08) extends it once more to the local-LLM cleanup pass: it
// spawns its OWN temporary CleanupServer (started and stopped within the
// subcommand — never the resident one the menu-bar app would run), so the
// whole final-path decision is provable the same way, still with no
// microphone, no field, and no accessibility grant.

import AppKit
import AVFoundation
import Foundation

let arguments = CommandLine.arguments

func runDoctor() -> Never {
    let config = Config.load()
    var failures = 0
    func check(_ label: String, _ ok: Bool, detail: String = "") {
        print("\(ok ? "ok " : "FAIL") \(label)\(detail.isEmpty ? "" : " — \(detail)")")
        if !ok { failures += 1 }
    }
    check("config", FileManager.default.fileExists(atPath: Config.configPath),
          detail: FileManager.default.fileExists(atPath: Config.configPath)
              ? Config.configPath : "missing (defaults in use — run install-dictate.sh)")
    check("whisper-cli", FileManager.default.isExecutableFile(atPath: config.whisperCli), detail: config.whisperCli)
    check("model", FileManager.default.fileExists(atPath: config.modelPath), detail: config.modelPath)
    check("vocab prompt", FileManager.default.fileExists(atPath: config.vocabPromptPath), detail: config.vocabPromptPath)
    check("mic permission", AVCaptureDevice.authorizationStatus(for: .audio) == .authorized,
          detail: String(describing: AVCaptureDevice.authorizationStatus(for: .audio).rawValue))
    check("accessibility", AXIsProcessTrusted(),
          detail: AXIsProcessTrusted() ? "trusted" : "not granted — System Settings > Privacy & Security > Accessibility")
    let names: [Int64: String] = [54: "right-cmd", 55: "left-cmd", 62: "right-ctrl",
                                  59: "left-ctrl", 61: "right-opt", 58: "left-opt"]
    let triggerDetail = config.triggerKeyCodes
        .map { "\($0) (\(names[$0] ?? "custom"))" }.joined(separator: ", ")
    check("trigger keys", !config.triggerKeyCodes.isEmpty, detail: triggerDetail)
    // Preview is additive: the style is just reported (never a failure on
    // its own), but when it's NOT "off", a missing binary or model means the
    // preview will silently never populate — inline typing or the panel
    // alike — which is worth a red row so that failure mode doesn't have to
    // be discovered live.
    check("preview", true, detail: "preview_style=\(config.previewStyle)")
    if config.previewStyle != "off" {
        check("preview server binary", FileManager.default.isExecutableFile(atPath: "/opt/homebrew/bin/whisper-server"),
              detail: "/opt/homebrew/bin/whisper-server")
        check("preview model", FileManager.default.fileExists(atPath: config.previewModelPath), detail: config.previewModelPath)
    }
    // Same additive-not-a-failure-on-its-own shape as the preview row above:
    // correction_llm is just reported, but when it's not "off", a missing
    // binary or model means the cleanup pass will silently never engage
    // (final path quietly falls back to heuristic-only every time) — worth a
    // red row rather than a silent behavior change discovered live.
    check("correction llm", true, detail: "correction_llm=\(config.correctionLLM)")
    if config.correctionLLM != "off" {
        check("cleanup server binary", FileManager.default.isExecutableFile(atPath: "/opt/homebrew/bin/llama-server"),
              detail: "/opt/homebrew/bin/llama-server")
        check("cleanup model", FileManager.default.fileExists(atPath: config.cleanupModelPath), detail: config.cleanupModelPath)
    }
    exit(failures == 0 ? 0 : 1)
}

func runTranscribe(path: String) -> Never {
    let config = Config.load()
    let transcriber = Transcriber(config: config)
    do {
        let text = try transcriber.transcribe(wav: URL(fileURLWithPath: path))
        print(text)
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("transcribe failed: \(error)\n".utf8))
        exit(1)
    }
}

/// Headless proof for LiveTyper.interpret (scratch-that + CorrectionResolver
/// together) — same shape as runTranscribe: no mic, no field, just text in,
/// text out, so both are provable from a script.
func runInterpret(text: String) -> Never {
    let interpreted = LiveTyper.interpret(text)
    print(interpreted.kept)
    if interpreted.scratchPrevious { print("scratch_previous=true") }
    exit(0)
}

/// Headless proof for the FULL final-path decision — scratch-that strip,
/// correction cue gate, local-LLM cleanup call, and the deletion-only
/// paraphrase guard — the same logic App.swift's resolveFinalText runs, but
/// reachable from a script with no mic, no field, and no accessibility
/// grant. Spawns its OWN temporary CleanupServer (started and stopped here,
/// never the menu-bar app's resident one) and waits for it to actually
/// answer /health before sending the real request — the resident server
/// instead tolerates a cold start via its own per-request timeout, but a
/// one-shot CLI call needs to wait up front or a cold start would
/// misreport as "the LLM never engaged."
func runCleanup(text: String) -> Never {
    let config = Config.load()
    let interpreted = LiveTyper.interpretFinal(text)

    // NOTE: every exit below goes through `exit(0)`, which — unlike a normal
    // return — does NOT run Swift `defer` blocks (it's the C stdlib exit(),
    // a hard process termination, not stack unwinding). A `defer { server.
    // stop() }` here would silently never fire and leak the child
    // llama-server on every call. So `finish`/`finishAndStop` below stop the
    // server EXPLICITLY, synchronously, before ever calling exit().
    var server: CleanupServer?
    func finish(_ result: String, engine: String) -> Never {
        server?.stop()
        print(result)
        print("engine=\(engine)")
        exit(0)
    }

    if interpreted.scratchPrevious {
        finish(interpreted.raw, engine: "passthrough")
    }

    // ARBITRATION FLIP (2026-08-08, same as App.resolveFinalText): heuristics
    // are PRIMARY. If the rule chain resolved something, use it and skip the
    // LLM entirely — never even spawn the server.
    // Word-level, not string-level — same reasoning as App.resolveFinalText.
    guard !CorrectionResolver.differsInWords(interpreted.heuristic, interpreted.raw) else {
        finish(interpreted.heuristic, engine: "heuristic")
    }

    // Heuristic found NOTHING. No cue in the raw text (or the LLM is turned
    // off) — fully local fast path, no server spawn, no network call at all.
    guard config.correctionLLM == "auto", LiveTyper.containsCorrectionCue(interpreted.raw) else {
        finish(interpreted.heuristic, engine: "passthrough")
    }

    let spawned = CleanupServer(config: config)
    spawned.start()
    server = spawned
    guard spawned.isSpawned else {
        FileHandle.standardError.write(Data("cleanup: llama-server failed to spawn — falling back to heuristic\n".utf8))
        finish(interpreted.heuristic, engine: "heuristic")
    }

    let readyDeadline = Date().addingTimeInterval(20.0)
    var ready = false
    while Date() < readyDeadline {
        if let url = URL(string: "http://127.0.0.1:\(config.cleanupServerPort)/health"),
           let data = try? Data(contentsOf: url),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           (json["status"] as? String) == "ok" {
            ready = true
            break
        }
        usleep(200_000)
    }
    guard ready else {
        FileHandle.standardError.write(Data("cleanup: llama-server did not become ready in time — falling back to heuristic\n".utf8))
        finish(interpreted.heuristic, engine: "heuristic")
    }

    let semaphore = DispatchSemaphore(value: 0)
    var llmResult: String?
    spawned.cleanup(text: interpreted.raw) { result in
        llmResult = result
        semaphore.signal()
    }
    _ = semaphore.wait(timeout: .now() + 3.0) // generous vs. cleanup()'s own 1.5s request timeout

    guard let candidate = llmResult, !candidate.isEmpty else {
        finish(interpreted.heuristic, engine: "heuristic")
    }
    guard CorrectionResolver.isDeletionOnly(candidate: candidate, of: interpreted.raw) else {
        finish(interpreted.heuristic, engine: "heuristic")
    }
    // Same no-op demotion as App.resolveFinalText: an unchanged echo passes
    // the guard trivially but must not outrank a heuristic that resolved.
    if CorrectionResolver.isDeletionOnly(candidate: interpreted.raw, of: candidate) {
        finish(interpreted.heuristic, engine: "heuristic")
    }
    finish(candidate, engine: "llm")
}

if arguments.count > 1 {
    switch arguments[1] {
    case "doctor":
        runDoctor()
    case "transcribe":
        guard arguments.count > 2 else {
            FileHandle.standardError.write(Data("usage: quill-dictate transcribe <wav>\n".utf8))
            exit(2)
        }
        runTranscribe(path: arguments[2])
    case "interpret":
        guard arguments.count > 2 else {
            FileHandle.standardError.write(Data("usage: quill-dictate interpret <text>\n".utf8))
            exit(2)
        }
        runInterpret(text: arguments[2])
    case "cleanup":
        guard arguments.count > 2 else {
            FileHandle.standardError.write(Data("usage: quill-dictate cleanup <text>\n".utf8))
            exit(2)
        }
        runCleanup(text: arguments[2])
    default:
        FileHandle.standardError.write(Data("usage: quill-dictate [doctor|transcribe <wav>|interpret <text>|cleanup <text>]\n".utf8))
        exit(2)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
