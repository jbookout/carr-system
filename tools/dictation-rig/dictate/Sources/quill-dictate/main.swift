// main.swift — entry point.
//
//   quill-dictate               run the menu-bar agent (normal mode)
//   quill-dictate doctor        headless health check, exit 0 = all green
//   quill-dictate transcribe F  run one WAV through the shared engine, print text
//
// doctor and transcribe exist so the pipeline is provable WITHOUT the
// accessibility grant or a live keyboard — the build session verifies the
// engine headlessly, then the live gesture tests prove the rest.

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
    check("trigger key", config.triggerKeyCode == 54,
          detail: "keycode \(config.triggerKeyCode)\(config.triggerKeyCode == 54 ? " (right-cmd)" : " (custom)")")
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
    default:
        FileHandle.standardError.write(Data("usage: quill-dictate [doctor|transcribe <wav>]\n".utf8))
        exit(2)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
