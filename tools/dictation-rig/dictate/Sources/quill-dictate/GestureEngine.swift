// GestureEngine.swift — the CGEvent tap and the one-key gesture grammar.
//
// Decision f799fd49, verbatim grammar:
//   HOLD right-cmd            -> push-to-talk (speak while held, release = insert)
//   DOUBLE-TAP right-cmd      -> toggle conversation mode
//   HOLD space (in conv mode) -> speak; release disengages (stops + inserts)
//
// Disambiguation rules, so normal typing and Joe's left-hand shortcuts are
// untouched (acceptance check 6):
//   - Only the RIGHT command key participates. Physical right-cmd is tracked
//     via the device-level flag bit (0x10, NX_DEVICERCMDKEYMASK) so a held
//     left-cmd never confuses the state machine.
//   - Any OTHER key pressed while right-cmd is down turns the gesture into an
//     ordinary keyboard shortcut: capture aborts, everything passes through.
//   - A press shorter than holdThresholdMs is a tap, not push-to-talk.
//   - In conversation mode, bare space (no cmd/ctrl/opt) is consumed as the
//     talk key; space with modifiers (cmd-space Spotlight etc.) passes
//     through untouched. Outside conversation mode space is never touched.
//   - Our own synthetic events (cmd-V, typed text) carry a marker and are
//     ignored, so the tap never feeds back on itself.
//
// The tap is a defaultTap (listening + consuming) but consumes ONLY bare
// space keyDown/keyUp while conversation mode is on. Every other event is
// returned unmodified.

import AppKit
import CoreGraphics
import Foundation

protocol GestureDelegate: AnyObject {
    func gestureCaptureStarted(kind: GestureEngine.CaptureKind)
    func gestureCaptureEnded()
    func gestureCaptureAborted()
    func gestureConversationToggled(on: Bool)
}

final class GestureEngine {
    enum CaptureKind { case pushToTalk, conversation }

    private let config: Config
    weak var delegate: GestureDelegate?

    private var tap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?

    // Right-cmd gesture state
    private var triggerDownAt: Date?
    private var triggerCancelled = false
    private var lastTapAt: Date?
    private var holdTimer: DispatchWorkItem?

    // Conversation mode
    private(set) var conversationMode = false
    private var spaceHeld = false

    /// What is capturing right now (nil = idle).
    private(set) var activeCapture: CaptureKind?
    /// Set while a transcription is still in flight; new captures are refused
    /// so utterances cannot interleave out of order.
    var busy = false

    private static let rightCmdDeviceBit: UInt64 = 0x10 // NX_DEVICERCMDKEYMASK

    init(config: Config) {
        self.config = config
    }

    func start() -> Bool {
        let mask: CGEventMask =
            (1 << CGEventType.keyDown.rawValue) |
            (1 << CGEventType.keyUp.rawValue) |
            (1 << CGEventType.flagsChanged.rawValue)

        let refcon = Unmanaged.passUnretained(self).toOpaque()
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: mask,
            callback: { _, type, event, refcon in
                guard let refcon else { return Unmanaged.passUnretained(event) }
                let engine = Unmanaged<GestureEngine>.fromOpaque(refcon).takeUnretainedValue()
                return engine.handle(type: type, event: event)
            },
            userInfo: refcon
        ) else {
            return false
        }
        self.tap = tap
        let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        self.runLoopSource = source
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        return true
    }

    // MARK: - Event handling (runs on the main run loop)

    private func handle(type: CGEventType, event: CGEvent) -> Unmanaged<CGEvent>? {
        switch type {
        case .tapDisabledByTimeout, .tapDisabledByUserInput:
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
            Log.shared.line("WARN event tap re-enabled after \(type == .tapDisabledByTimeout ? "timeout" : "user input")")
            return Unmanaged.passUnretained(event)
        default:
            break
        }

        // Never react to our own synthetic events.
        if event.getIntegerValueField(.eventSourceUserData) == Inserter.syntheticMarker {
            return Unmanaged.passUnretained(event)
        }

        let keyCode = event.getIntegerValueField(.keyboardEventKeycode)

        if type == .flagsChanged && keyCode == config.triggerKeyCode {
            let physicallyDown = (event.flags.rawValue & GestureEngine.rightCmdDeviceBit) != 0
            if physicallyDown { triggerPressed() } else { triggerReleased() }
            return Unmanaged.passUnretained(event)
        }

        // Another key while the trigger is held = a real shortcut. Stand down.
        if type == .keyDown, triggerDownAt != nil {
            cancelTriggerGesture()
        }

        // Conversation mode: bare space is the talk key and is consumed.
        if conversationMode && keyCode == config.conversationKeyCode {
            let hasModifiers = !event.flags.intersection([.maskCommand, .maskControl, .maskAlternate]).isEmpty
            if hasModifiers { return Unmanaged.passUnretained(event) }
            let isRepeat = event.getIntegerValueField(.keyboardEventAutorepeat) != 0
            if type == .keyDown {
                if !isRepeat { spacePressed() }
                return nil // consumed
            }
            if type == .keyUp {
                spaceReleased()
                return nil // consumed
            }
        }

        return Unmanaged.passUnretained(event)
    }

    // MARK: - Trigger (right-cmd) grammar

    private func triggerPressed() {
        triggerDownAt = Date()
        triggerCancelled = false

        let work = DispatchWorkItem { [weak self] in
            guard let self, self.triggerDownAt != nil, !self.triggerCancelled,
                  self.activeCapture == nil, !self.busy else { return }
            self.activeCapture = .pushToTalk
            self.delegate?.gestureCaptureStarted(kind: .pushToTalk)
        }
        holdTimer = work
        DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(config.holdThresholdMs), execute: work)
    }

    private func triggerReleased() {
        holdTimer?.cancel()
        holdTimer = nil
        guard let downAt = triggerDownAt else { return }
        triggerDownAt = nil
        let heldMs = Int(Date().timeIntervalSince(downAt) * 1000)

        if activeCapture == .pushToTalk {
            activeCapture = nil
            delegate?.gestureCaptureEnded()
            lastTapAt = nil
            return
        }
        guard !triggerCancelled else { return }

        // Tap bookkeeping for the double-tap toggle.
        if heldMs <= config.tapMaxMs {
            let now = Date()
            if let last = lastTapAt,
               Int(now.timeIntervalSince(last) * 1000) <= config.doubleTapGapMs {
                lastTapAt = nil
                toggleConversationMode()
            } else {
                lastTapAt = now
            }
        } else {
            lastTapAt = nil
        }
    }

    private func cancelTriggerGesture() {
        guard triggerDownAt != nil, !triggerCancelled else { return }
        triggerCancelled = true
        holdTimer?.cancel()
        holdTimer = nil
        lastTapAt = nil
        if activeCapture == .pushToTalk {
            activeCapture = nil
            delegate?.gestureCaptureAborted()
        }
    }

    // MARK: - Conversation mode

    /// Mouse-reachable twin of the double-tap toggle (menu item).
    func menuToggleConversation() {
        toggleConversationMode()
    }

    private func toggleConversationMode() {
        conversationMode.toggle()
        if !conversationMode, spaceHeld || activeCapture == .conversation {
            spaceHeld = false
            if activeCapture == .conversation {
                activeCapture = nil
                delegate?.gestureCaptureAborted()
            }
        }
        delegate?.gestureConversationToggled(on: conversationMode)
    }

    private func spacePressed() {
        guard !spaceHeld else { return }
        spaceHeld = true
        guard activeCapture == nil, !busy else { return }
        activeCapture = .conversation
        delegate?.gestureCaptureStarted(kind: .conversation)
    }

    private func spaceReleased() {
        guard spaceHeld else { return }
        spaceHeld = false
        if activeCapture == .conversation {
            activeCapture = nil
            delegate?.gestureCaptureEnded()
        }
    }
}
