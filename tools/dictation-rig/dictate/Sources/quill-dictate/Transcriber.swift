// Transcriber.swift — one utterance through the SHARED engine.
//
// Same whisper-cli, same ggml-large-v3-turbo, same CARR vocab prompt as
// meeting mode's transcribe_session.py. This file is deliberately a thin
// Process wrapper — the engine choice was ruled once (decision 28e35509 /
// Joe's "most effective" model call) and this mode inherits it wholesale.

import Foundation

struct Transcriber {
    let config: Config

    struct Failure: Error { let message: String }

    /// Blocking; run on a background queue. Returns cleaned text ("" when
    /// whisper heard nothing worth keeping).
    func transcribe(wav: URL) throws -> String {
        let model = FileManager.default.fileExists(atPath: config.modelPath)
            ? config.modelPath : config.fallbackModelPath
        guard FileManager.default.fileExists(atPath: model) else {
            throw Failure(message: "no whisper model at \(config.modelPath) or fallback")
        }
        guard FileManager.default.fileExists(atPath: config.whisperCli) else {
            throw Failure(message: "whisper-cli not found at \(config.whisperCli)")
        }

        let outBase = wav.deletingPathExtension().path
        var args = ["-m", model, "-f", wav.path,
                    "-l", "en", "-nt", "-np", "-otxt", "-of", outBase]
        if let prompt = try? String(contentsOfFile: config.vocabPromptPath, encoding: .utf8),
           !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            args += ["--prompt", prompt.trimmingCharacters(in: .whitespacesAndNewlines)]
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: config.whisperCli)
        process.arguments = args
        let sink = Pipe()
        process.standardOutput = sink
        process.standardError = sink
        try process.run()
        process.waitUntilExit()
        let noise = String(data: sink.fileHandleForReading.readDataToEndOfFile(),
                           encoding: .utf8) ?? ""
        guard process.terminationStatus == 0 else {
            throw Failure(message: "whisper-cli rc=\(process.terminationStatus): \(noise.suffix(300))")
        }

        let txtPath = outBase + ".txt"
        defer { try? FileManager.default.removeItem(atPath: txtPath) }
        let raw = (try? String(contentsOfFile: txtPath, encoding: .utf8)) ?? ""
        return Transcriber.clean(raw)
    }

    /// Collapse whisper's line breaks into a single utterance and drop the
    /// known hallucination tokens it emits on silence-adjacent audio.
    static func clean(_ raw: String) -> String {
        let junk: Set<String> = [
            "[BLANK_AUDIO]", "[ Silence ]", "[silence]", "(silence)",
            "[MUSIC]", "[music]", "(music)", "[SOUND]", "[NOISE]",
        ]
        let joined = raw
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && !junk.contains($0) }
            .joined(separator: " ")
        return joined.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
