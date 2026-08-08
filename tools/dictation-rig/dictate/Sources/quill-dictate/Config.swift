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
    /// Virtual keycodes of the push-to-talk / mode-toggle trigger — a SET,
    /// one entry per keyboard, same gesture on each. 54 = right command
    /// (MacBook internal). 62 = right control (the Logitech's key right of
    /// space — live capture 2026-08-07 showed that board emits NO true
    /// right-cmd: its right side sends rctrl/ropt/LEFT-cmd, so decision
    /// f799fd49's "right-cmd on both" premise failed at the emission level
    /// and the config absorbed it, exactly as the ruling intended).
    var triggerKeyCodes: [Int64] = [54, 62]

    /// Keycode that speaks while held inside conversation mode. 49 = space.
    var conversationKeyCode: Int64 = 49

    /// Hold this long before a press counts as push-to-talk (else it is a tap).
    var holdThresholdMs: Int = 200

    /// A press shorter than this is a "tap" for double-tap detection.
    var tapMaxMs: Int = 350

    /// Two taps within this window toggle conversation mode.
    var doubleTapGapMs: Int = 400

    /// Inside conversation mode: a space held at least this long talks; a
    /// quicker tap is passed through as a normal typed space, so typing
    /// still works while the mode is on (added after the 2026-08-07 live
    /// test where Joe's spaces were silently eaten).
    var spaceHoldThresholdMs: Int = 200

    /// "paste" (clipboard + synthetic cmd-V, default) or "type" (synthetic
    /// keystrokes; slower, but leaves the clipboard alone).
    var insertion: String = "paste"

    /// Audible cues (capture start / stop / mode toggle / error). Local
    /// feedback for Joe only — NOT the meeting-mode consent announcement,
    /// which deliberately does not exist in this mode (no counterparty).
    /// Names are macOS system sounds (/System/Library/Sounds); editable in
    /// config + "Reload config" so taste changes need no rebuild. Capture
    /// start is a single chime by Joe's call, 2026-08-08.
    var sounds: Bool = true
    var soundCaptureStart: String = "Glass"
    var soundGated: String = "Bottle"
    var soundError: String = "Basso"
    var soundModeOn: String = "Purr"
    var soundModeOff: String = "Submarine"

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

    /// Live-transcription preview overlay (added 2026-08-08, Joe wants to SEE
    /// words appear while he speaks). Strictly additive: a SEPARATE resident
    /// whisper-server + a fast small.en model, never the final whisper-cli
    /// pass — the preview must never slow down or gate the real insert.
    var livePreview: Bool = true
    var previewServerPort: Int = 8595
    var previewIntervalMs: Int = 900
    var previewWindowSeconds: Int = 15
    var previewModelPath: String = NSString(string: "~/.cache/whisper-cpp/models/ggml-small.en.bin").expandingTildeInPath

    enum CodingKeys: String, CodingKey {
        case triggerKeyCodes = "trigger_key_codes"
        case conversationKeyCode = "conversation_key_code"
        case holdThresholdMs = "hold_threshold_ms"
        case tapMaxMs = "tap_max_ms"
        case doubleTapGapMs = "double_tap_gap_ms"
        case spaceHoldThresholdMs = "space_hold_threshold_ms"
        case insertion
        case sounds
        case soundCaptureStart = "sound_capture_start"
        case soundGated = "sound_gated"
        case soundError = "sound_error"
        case soundModeOn = "sound_mode_on"
        case soundModeOff = "sound_mode_off"
        case minPeakLevel = "min_peak_level"
        case whisperCli = "whisper_cli"
        case modelPath = "model_path"
        case fallbackModelPath = "fallback_model_path"
        case vocabPromptPath = "vocab_prompt_path"
        case workDir = "work_dir"
        case logPath = "log_path"
        case livePreview = "live_preview"
        case previewServerPort = "preview_server_port"
        case previewIntervalMs = "preview_interval_ms"
        case previewWindowSeconds = "preview_window_seconds"
        case previewModelPath = "preview_model_path"
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
    var trigger_key_codes: [Int64]?
    var conversation_key_code: Int64?
    var hold_threshold_ms: Int?
    var tap_max_ms: Int?
    var double_tap_gap_ms: Int?
    var space_hold_threshold_ms: Int?
    var insertion: String?
    var sounds: Bool?
    var sound_capture_start: String?
    var sound_gated: String?
    var sound_error: String?
    var sound_mode_on: String?
    var sound_mode_off: String?
    var min_peak_level: Double?
    var whisper_cli: String?
    var model_path: String?
    var fallback_model_path: String?
    var vocab_prompt_path: String?
    var work_dir: String?
    var log_path: String?
    var live_preview: Bool?
    var preview_server_port: Int?
    var preview_interval_ms: Int?
    var preview_window_seconds: Int?
    var preview_model_path: String?

    func overlay(onto config: inout Config) {
        // Old single-key form still honored; the list form wins when present.
        if let v = trigger_key_code { config.triggerKeyCodes = [v] }
        if let v = trigger_key_codes, !v.isEmpty { config.triggerKeyCodes = v }
        if let v = conversation_key_code { config.conversationKeyCode = v }
        if let v = hold_threshold_ms { config.holdThresholdMs = v }
        if let v = tap_max_ms { config.tapMaxMs = v }
        if let v = double_tap_gap_ms { config.doubleTapGapMs = v }
        if let v = space_hold_threshold_ms { config.spaceHoldThresholdMs = v }
        if let v = insertion { config.insertion = v }
        if let v = sounds { config.sounds = v }
        if let v = sound_capture_start { config.soundCaptureStart = v }
        if let v = sound_gated { config.soundGated = v }
        if let v = sound_error { config.soundError = v }
        if let v = sound_mode_on { config.soundModeOn = v }
        if let v = sound_mode_off { config.soundModeOff = v }
        if let v = min_peak_level { config.minPeakLevel = v }
        if let v = whisper_cli { config.whisperCli = NSString(string: v).expandingTildeInPath }
        if let v = model_path { config.modelPath = NSString(string: v).expandingTildeInPath }
        if let v = fallback_model_path { config.fallbackModelPath = NSString(string: v).expandingTildeInPath }
        if let v = vocab_prompt_path { config.vocabPromptPath = NSString(string: v).expandingTildeInPath }
        if let v = work_dir { config.workDir = NSString(string: v).expandingTildeInPath }
        if let v = log_path { config.logPath = NSString(string: v).expandingTildeInPath }
        if let v = live_preview { config.livePreview = v }
        if let v = preview_server_port { config.previewServerPort = v }
        if let v = preview_interval_ms { config.previewIntervalMs = v }
        if let v = preview_window_seconds { config.previewWindowSeconds = v }
        if let v = preview_model_path { config.previewModelPath = NSString(string: v).expandingTildeInPath }
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
