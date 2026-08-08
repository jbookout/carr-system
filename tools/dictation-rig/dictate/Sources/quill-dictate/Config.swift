// Config.swift — quill-dictate configuration.
//
// The trigger key lives HERE, not in code paths scattered around, because the
// ruling (decision f799fd49) makes a future keyboard a one-line change:
// `trigger_key_code` is the only thing that names right-cmd.
//
// File: ~/.config/quill-dictate/config.json (deployed by install-dictate.sh).
// Missing file or missing keys fall back to the defaults below, so a broken
// config never bricks the agent — it logs and runs on defaults.

import Foundation

struct Config: Codable {
    /// Virtual keycode of the push-to-talk / mode-toggle trigger.
    /// 54 = right command — confirmed present on both of Joe's keyboards
    /// (MacBook internal + Logitech Mac-layout universal), decision f799fd49.
    var triggerKeyCode: Int64 = 54

    /// Keycode that speaks while held inside conversation mode. 49 = space.
    var conversationKeyCode: Int64 = 49

    /// Hold this long before a press counts as push-to-talk (else it is a tap).
    var holdThresholdMs: Int = 200

    /// A press shorter than this is a "tap" for double-tap detection.
    var tapMaxMs: Int = 350

    /// Two taps within this window toggle conversation mode.
    var doubleTapGapMs: Int = 400

    /// "paste" (clipboard + synthetic cmd-V, default) or "type" (synthetic
    /// keystrokes; slower, but leaves the clipboard alone).
    var insertion: String = "paste"

    /// Audible cues (capture start / stop / mode toggle / error). Local
    /// feedback for Joe only — NOT the meeting-mode consent announcement,
    /// which deliberately does not exist in this mode (no counterparty).
    var sounds: Bool = true

    /// Discard captures whose peak level never crosses this (0..1 of full
    /// scale). Stops whisper hallucinating text onto a silent press.
    var minPeakLevel: Double = 0.015

    /// Shared transcription engine — same binary, model, and vocab prompt as
    /// meeting mode (transcribe_session.py). Never a second engine.
    var whisperCli: String = "/opt/homebrew/bin/whisper-cli"
    var modelPath: String = NSString(string: "~/.cache/whisper-cpp/models/ggml-large-v3-turbo.bin").expandingTildeInPath
    var fallbackModelPath: String = NSString(string: "~/.cache/whisper-cpp/models/ggml-small.en.bin").expandingTildeInPath
    var vocabPromptPath: String = NSString(string: "~/carr-system/tools/dictation-rig/vocab-prompt.txt").expandingTildeInPath

    /// Where utterance WAVs and logs go. Cleared after successful insert.
    var workDir: String = NSString(string: "~/Library/Caches/quill-dictate").expandingTildeInPath
    var logPath: String = NSString(string: "~/Library/Logs/quill-dictate.log").expandingTildeInPath

    enum CodingKeys: String, CodingKey {
        case triggerKeyCode = "trigger_key_code"
        case conversationKeyCode = "conversation_key_code"
        case holdThresholdMs = "hold_threshold_ms"
        case tapMaxMs = "tap_max_ms"
        case doubleTapGapMs = "double_tap_gap_ms"
        case insertion
        case sounds
        case minPeakLevel = "min_peak_level"
        case whisperCli = "whisper_cli"
        case modelPath = "model_path"
        case fallbackModelPath = "fallback_model_path"
        case vocabPromptPath = "vocab_prompt_path"
        case workDir = "work_dir"
        case logPath = "log_path"
    }

    static var configPath: String {
        NSString(string: "~/.config/quill-dictate/config.json").expandingTildeInPath
    }

    /// Load config; every key optional, defaults fill the gaps.
    static func load() -> Config {
        let url = URL(fileURLWithPath: configPath)
        guard let data = try? Data(contentsOf: url) else { return Config() }
        do {
            let decoder = JSONDecoder()
            // Decode into a partial dictionary-backed config: decode as
            // [String: AnyDecodable] is overkill — instead decode a mirror
            // struct where everything is optional, then overlay.
            let partial = try decoder.decode(PartialConfig.self, from: data)
            var config = Config()
            partial.overlay(onto: &config)
            return config
        } catch {
            Log.shared.line("WARN config parse failed (\(error)); running on defaults")
            return Config()
        }
    }

    func expanded(_ path: String) -> String {
        NSString(string: path).expandingTildeInPath
    }
}

/// Every field optional so a config file may set only what it changes.
private struct PartialConfig: Codable {
    var trigger_key_code: Int64?
    var conversation_key_code: Int64?
    var hold_threshold_ms: Int?
    var tap_max_ms: Int?
    var double_tap_gap_ms: Int?
    var insertion: String?
    var sounds: Bool?
    var min_peak_level: Double?
    var whisper_cli: String?
    var model_path: String?
    var fallback_model_path: String?
    var vocab_prompt_path: String?
    var work_dir: String?
    var log_path: String?

    func overlay(onto config: inout Config) {
        if let v = trigger_key_code { config.triggerKeyCode = v }
        if let v = conversation_key_code { config.conversationKeyCode = v }
        if let v = hold_threshold_ms { config.holdThresholdMs = v }
        if let v = tap_max_ms { config.tapMaxMs = v }
        if let v = double_tap_gap_ms { config.doubleTapGapMs = v }
        if let v = insertion { config.insertion = v }
        if let v = sounds { config.sounds = v }
        if let v = min_peak_level { config.minPeakLevel = v }
        if let v = whisper_cli { config.whisperCli = NSString(string: v).expandingTildeInPath }
        if let v = model_path { config.modelPath = NSString(string: v).expandingTildeInPath }
        if let v = fallback_model_path { config.fallbackModelPath = NSString(string: v).expandingTildeInPath }
        if let v = vocab_prompt_path { config.vocabPromptPath = NSString(string: v).expandingTildeInPath }
        if let v = work_dir { config.workDir = NSString(string: v).expandingTildeInPath }
        if let v = log_path { config.logPath = NSString(string: v).expandingTildeInPath }
    }
}

/// Tiny append-only logger; never throws, never blocks the event tap.
final class Log {
    static let shared = Log()
    private let queue = DispatchQueue(label: "quill-dictate.log", qos: .utility)
    var path: String = NSString(string: "~/Library/Logs/quill-dictate.log").expandingTildeInPath

    func line(_ message: String) {
        let ts = ISO8601DateFormatter().string(from: Date())
        let entry = "\(ts) \(message)\n"
        queue.async { [path] in
            if !FileManager.default.fileExists(atPath: path) {
                FileManager.default.createFile(atPath: path, contents: nil)
            }
            if let handle = FileHandle(forWritingAtPath: path) {
                defer { try? handle.close() }
                _ = try? handle.seekToEnd()
                try? handle.write(contentsOf: Data(entry.utf8))
            }
        }
        FileHandle.standardError.write(Data(entry.utf8))
    }
}
