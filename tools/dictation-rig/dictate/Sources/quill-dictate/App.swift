// App.swift — menu-bar shell wiring gestures -> recorder -> whisper -> insert.
//
// Visibly distinct from meeting mode on purpose (loop #243's boundary): quill
// meeting mode has its own menu-bar item and an audible consent announcement;
// quill-dictate shows a keyboard-badge mic icon, plays only local UI cues, and
// records nothing except while a key is physically held.

import AppKit
import AVFoundation
import Foundation

final class AppDelegate: NSObject, NSApplicationDelegate, GestureDelegate {
    private var config = Config.load()
    private var statusItem: NSStatusItem!
    private var gestures: GestureEngine!
    private let recorder = Recorder()
    private let workQueue = DispatchQueue(label: "quill-dictate.pipeline", qos: .userInitiated)
    private var axPollTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        Log.shared.path = config.logPath
        Log.shared.line("START quill-dictate pid=\(ProcessInfo.processInfo.processIdentifier) trigger_key=\(config.triggerKeyCode)")

        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        buildMenu()
        setIcon(state: .waitingPermission)

        requestMicAccess()
        startWhenTrusted()
    }

    // MARK: - Permissions

    private func requestMicAccess() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            break
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                Log.shared.line("INFO mic permission \(granted ? "granted" : "DENIED")")
            }
        default:
            Log.shared.line("WARN mic permission denied/restricted — dictation cannot capture")
        }
    }

    private func startWhenTrusted() {
        let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
        if AXIsProcessTrustedWithOptions(options) {
            startTap()
            return
        }
        Log.shared.line("WAIT accessibility permission not granted yet — prompted; polling")
        axPollTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] timer in
            guard let self, AXIsProcessTrusted() else { return }
            timer.invalidate()
            self.axPollTimer = nil
            self.startTap()
        }
    }

    private func startTap() {
        gestures = GestureEngine(config: config)
        gestures.delegate = self
        if gestures.start() {
            Log.shared.line("READY event tap live; hold right-cmd to talk, double-tap for conversation mode")
            setIcon(state: .idle)
        } else {
            Log.shared.line("ERROR event tap creation failed even though AX is trusted")
            setIcon(state: .error)
        }
    }

    // MARK: - GestureDelegate

    func gestureCaptureStarted(kind: GestureEngine.CaptureKind) {
        do {
            try recorder.start()
            setIcon(state: .recording)
            cue("Pop")
        } catch {
            Log.shared.line("ERROR mic start failed: \(error)")
            cue("Basso")
            setIcon(state: gestures.conversationMode ? .conversation : .idle)
        }
    }

    func gestureCaptureEnded() {
        guard recorder.isRecording else { return }
        let peak = recorder.peakLevel
        let seconds = recorder.capturedSeconds
        let wav = recorder.stop(writeTo: config.workDir)
        setIcon(state: .busy)

        guard let wav else {
            Log.shared.line("INFO empty capture, nothing to transcribe")
            settle()
            return
        }
        // Silence gate: a keypress with no real speech behind it inserts
        // nothing, instead of whisper hallucinating a caption onto noise.
        guard peak >= config.minPeakLevel, seconds >= 0.35 else {
            Log.shared.line("INFO capture gated (peak=\(String(format: "%.4f", peak)) seconds=\(String(format: "%.2f", seconds))) — discarded")
            try? FileManager.default.removeItem(at: wav)
            cue("Bottle")
            settle()
            return
        }

        gestures.busy = true
        let transcriber = Transcriber(config: config)
        let inserter = Inserter(config: config)
        workQueue.async { [weak self] in
            defer {
                DispatchQueue.main.async {
                    self?.gestures.busy = false
                    self?.settle()
                }
            }
            do {
                let started = Date()
                let text = try transcriber.transcribe(wav: wav)
                let elapsed = String(format: "%.1f", Date().timeIntervalSince(started))
                try? FileManager.default.removeItem(at: wav)
                guard !text.isEmpty else {
                    Log.shared.line("INFO whisper heard nothing (\(elapsed)s)")
                    DispatchQueue.main.async { self?.cue("Bottle") }
                    return
                }
                Log.shared.line("INSERT \(text.count) chars in \(elapsed)s (audio \(String(format: "%.1f", seconds))s)")
                DispatchQueue.main.async { inserter.insert(text) }
            } catch {
                Log.shared.line("ERROR transcription failed: \(error)")
                DispatchQueue.main.async { self?.cue("Basso") }
            }
        }
    }

    func gestureCaptureAborted() {
        recorder.abort()
        Log.shared.line("INFO capture aborted (shortcut or mode exit)")
        settle()
    }

    func gestureConversationToggled(on: Bool) {
        Log.shared.line("MODE conversation \(on ? "ON — hold space to talk" : "off")")
        cue(on ? "Glass" : "Submarine")
        settle()
        refreshMenuState()
    }

    private func settle() {
        setIcon(state: gestures?.conversationMode == true ? .conversation : .idle)
    }

    // MARK: - Status item

    private enum IconState { case waitingPermission, idle, recording, busy, conversation, error }

    private func setIcon(state: IconState) {
        let symbol: (name: String, description: String)
        switch state {
        case .waitingPermission: symbol = ("mic.badge.xmark", "quill-dictate: needs permission")
        case .idle: symbol = ("mic", "quill-dictate: idle")
        case .recording: symbol = ("mic.fill", "quill-dictate: recording")
        case .busy: symbol = ("waveform", "quill-dictate: transcribing")
        case .conversation: symbol = ("mic.square", "quill-dictate: conversation mode")
        case .error: symbol = ("exclamationmark.triangle", "quill-dictate: error")
        }
        DispatchQueue.main.async { [self] in
            let image = NSImage(systemSymbolName: symbol.name, accessibilityDescription: symbol.description)
            image?.isTemplate = true
            statusItem.button?.image = image
            statusItem.button?.toolTip = symbol.description
        }
    }

    private var conversationMenuItem: NSMenuItem!

    private func buildMenu() {
        let menu = NSMenu()
        let title = NSMenuItem(title: "Quill Dictate — hold right-⌘ to talk", action: nil, keyEquivalent: "")
        title.isEnabled = false
        menu.addItem(title)
        conversationMenuItem = NSMenuItem(title: "Conversation mode (double-tap right-⌘)",
                                          action: #selector(toggleConversationFromMenu), keyEquivalent: "")
        conversationMenuItem.target = self
        menu.addItem(conversationMenuItem)
        menu.addItem(.separator())
        let reload = NSMenuItem(title: "Reload config", action: #selector(reloadConfig), keyEquivalent: "")
        reload.target = self
        menu.addItem(reload)
        let quit = NSMenuItem(title: "Quit Quill Dictate", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        menu.addItem(quit)
        statusItem.menu = menu
    }

    private func refreshMenuState() {
        DispatchQueue.main.async { [self] in
            conversationMenuItem.state = gestures?.conversationMode == true ? .on : .off
        }
    }

    @objc private func toggleConversationFromMenu() {
        // Same toggle the double-tap drives, reachable by mouse.
        gestures?.menuToggleConversation()
    }

    @objc private func reloadConfig() {
        config = Config.load()
        Log.shared.path = config.logPath
        Log.shared.line("INFO config reloaded")
    }

    private func cue(_ name: String) {
        guard config.sounds else { return }
        NSSound(named: name)?.play()
    }
}
