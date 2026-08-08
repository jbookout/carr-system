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

    // Trigger gesture state (one gesture at a time, whichever trigger key)
    private var triggerDownAt: Date?
    private var triggerCancelled = false
    private var triggerUsesDeviceBit = true
    private var activeTriggerKey: Int64?

    /// Modifier signature per trigger keycode: the generic flag mask, the
    /// device-level bit that identifies THIS side, and the sibling bit for
    /// the opposite side. The sibling matters because some boards (Joe's
    /// Logitech, live 2026-08-08) report BOTH instances of a modifier under
    /// ONE keycode — his LEFT control arrived as keycode 62 and fired
    /// dictation, because the press check accepted any maskControl event on
    /// a trigger keycode and only consulted the side bit at release. Press
    /// now requires the correct side bit whenever the board sets either bit
    /// of the pair; the bit-less fallback (boards that omit device bits
    /// entirely) stays.
    private struct ModifierSignature { let mask: CGEventFlags; let deviceBit: UInt64; let siblingBit: UInt64 }
    private static let signatures: [Int64: ModifierSignature] = [
        54: ModifierSignature(mask: .maskCommand, deviceBit: 0x10, siblingBit: 0x8),     // right cmd (sibling: left)
        55: ModifierSignature(mask: .maskCommand, deviceBit: 0x8, siblingBit: 0x10),     // left cmd
        62: ModifierSignature(mask: .maskControl, deviceBit: 0x2000, siblingBit: 0x1),   // right ctrl (sibling: left)
        59: ModifierSignature(mask: .maskControl, deviceBit: 0x1, siblingBit: 0x2000),   // left ctrl
        61: ModifierSignature(mask: .maskAlternate, deviceBit: 0x40, siblingBit: 0x20),  // right opt
        58: ModifierSignature(mask: .maskAlternate, deviceBit: 0x20, siblingBit: 0x40),  // left opt
    ]
    private var lastTapAt: Date?
    private var holdTimer: DispatchWorkItem?

    // Conversation mode
    private(set) var conversationMode = false
    private var spaceHeld = false
    private var spaceHoldTimer: DispatchWorkItem?
    private var spaceCapturing = false

    /// What is capturing right now (nil = idle).
    private(set) var activeCapture: CaptureKind?
    /// Set while a transcription is still in flight; new captures are refused
    /// so utterances cannot interleave out of order.
    var busy = false

    /// Monotonically increasing count of REAL (non-synthetic) keyDown events
    /// this engine has observed. FIX 3 (2026-08-08 live failure round): App
    /// snapshots this at gestureCaptureEnded, before dispatching the final
    /// transcription to workQueue, then compares again right before the
    /// result would land in the field — if Joe typed for real in between, a
    /// late final pass is stale and must not overwrite what he already
    /// typed. Incremented inline in the tap callback (O(1), no allocation),
    /// so this never adds latency to the live tick path.
    private(set) var userKeystrokes = 0


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

        // FIX 3: count every REAL keyDown Joe produces, trigger chords and
        // ordinary typing alike — App only needs to know SOMETHING was
        // typed, not what.
        if type == .keyDown {
            userKeystrokes += 1
        }

        let keyCode = event.getIntegerValueField(.keyboardEventKeycode)

        if type == .flagsChanged && config.triggerKeyCodes.contains(keyCode) {
            let sig = GestureEngine.signatures[keyCode]
                ?? ModifierSignature(mask: .maskCommand, deviceBit: 0, siblingBit: 0)
            // Trust the device-level left/right bit when the press carried
            // it; fall back to the generic modifier flag when it didn't
            // (some third-party boards omit the bit — found live 2026-08-07).
            let deviceBit = sig.deviceBit != 0 && (event.flags.rawValue & sig.deviceBit) != 0
            let maskSet = event.flags.contains(sig.mask)
            // SIDE CHECK AT PRESS (2026-08-08): when the event carries either
            // side bit of this modifier pair, the trigger fires only on the
            // RIGHT-side bit. Defensive, and it did NOT fix the bug it was
            // written for: Joe's stray trigger turned out to be his Logitech's
            // left-Control keycap emitting keycode 54 with the genuine
            // right-cmd device bit — indistinguishable from the real thing at
            // this layer, and repaired at the HID layer instead (see
            // bin/logitech-keymap.sh). Kept because a board that sets side
            // bits honestly should still be read honestly. Boards with no side
            // bits keep the maskSet-only fallback.
            let pairBits = event.flags.rawValue & (sig.deviceBit | sig.siblingBit)
            let sideOK = pairBits == 0 || deviceBit

            // Ownership is decided BEFORE any state mutation below, because
            // consumption depends on it and triggerReleased() clears the very
            // state that proves the release was ours.
            let isOurPress = triggerDownAt == nil && maskSet && sideOK
            let isOurRelease = triggerDownAt != nil && activeTriggerKey == keyCode
                && !(triggerUsesDeviceBit ? deviceBit : maskSet)

            if isOurPress {
                activeTriggerKey = keyCode
                triggerUsesDeviceBit = deviceBit
                triggerPressed()
            } else if isOurRelease {
                activeTriggerKey = nil
                triggerReleased()
            }
            // CONSUMED — but only events that are OURS: a right-side press
            // (quill-dictate owns its trigger keys outright; Siri's "press
            // either cmd twice" kept firing no matter what Settings said,
            // live 2026-08-07), or the release of OUR active gesture. A
            // sibling-side press under the same keycode (Joe's Logitech
            // reports left ctrl as keycode 62 too, live fix 2026-08-08)
            // passes through untouched — and so does its release: eating a
            // left-ctrl release we never owned would leave apps believing
            // Control is still held. Subsequent keyDowns carry the modifier
            // in their own flags either way, so chords survive.
            return (isOurPress || isOurRelease) ? nil : Unmanaged.passUnretained(event)
        }

        // Another key while the trigger is held = a real shortcut. Stand down.
        if type == .keyDown, triggerDownAt != nil {
            cancelTriggerGesture()
        }

        // Escape is the panic exit: always leaves conversation mode, and the
        // keystroke still reaches the app underneath.
        if conversationMode && type == .keyDown && keyCode == 53 {
            toggleConversationMode()
            return Unmanaged.passUnretained(event)
        }

        // Conversation mode: HELD space is the talk key. A quick tap is
        // replayed as a normal typed space so typing keeps working — being in
        // the mode must never mean losing the space bar (live lesson,
        // 2026-08-07: Joe's spaces were silently eaten mid-sentence).
        if conversationMode && keyCode == config.conversationKeyCode {
            let hasModifiers = !event.flags.intersection([.maskCommand, .maskControl, .maskAlternate]).isEmpty
            if hasModifiers { return Unmanaged.passUnretained(event) }
            let isRepeat = event.getIntegerValueField(.keyboardEventAutorepeat) != 0
            if type == .keyDown {
                if !isRepeat { spacePressed() }
                return nil // consumed (replayed on quick release)
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
        if !conversationMode {
            spaceHoldTimer?.cancel()
            spaceHoldTimer = nil
            spaceHeld = false
            spaceCapturing = false
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
        spaceCapturing = false
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.spaceHeld, self.conversationMode,
                  self.activeCapture == nil, !self.busy else { return }
            self.spaceCapturing = true
            self.activeCapture = .conversation
            self.delegate?.gestureCaptureStarted(kind: .conversation)
        }
        spaceHoldTimer = work
        DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(config.spaceHoldThresholdMs), execute: work)
    }

    private func spaceReleased() {
        guard spaceHeld else { return }
        spaceHeld = false
        spaceHoldTimer?.cancel()
        spaceHoldTimer = nil
        if spaceCapturing, activeCapture == .conversation {
            spaceCapturing = false
            activeCapture = nil
            delegate?.gestureCaptureEnded()
        } else {
            // Quick tap: give the app the space it asked for.
            GestureEngine.postSyntheticSpace(keyCode: CGKeyCode(config.conversationKeyCode))
        }
    }

    /// Replay a plain space keystroke, marked so our own tap ignores it.
    static func postSyntheticSpace(keyCode: CGKeyCode) {
        let source = CGEventSource(stateID: .combinedSessionState)
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: false) else { return }
        down.setIntegerValueField(.eventSourceUserData, value: Inserter.syntheticMarker)
        up.setIntegerValueField(.eventSourceUserData, value: Inserter.syntheticMarker)
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
    }
}
