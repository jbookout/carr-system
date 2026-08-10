// PostReleaseActivity.swift — pure, testable journal for keys typed while
// Quill's final transcription is in flight.

import CoreGraphics
import Foundation

public struct PostReleaseActivity: Equatable {
    public private(set) var appendedText = ""
    public private(set) var keyDowns = 0
    public private(set) var unsafeReason: String?

    public init() {}

    public var isSafe: Bool { unsafeReason == nil }

    /// Records only edits that can be reversed and replayed safely at the
    /// current caret. Plain text extends the suffix; backspace may shorten
    /// only that suffix. Everything else could move focus/caret, submit a
    /// form, or mutate earlier text, so it makes reconciliation unsafe.
    public mutating func recordKey(keyCode: Int64, flags: CGEventFlags, text: String) {
        keyDowns += 1
        guard unsafeReason == nil else { return }

        let disallowed = flags.intersection([.maskCommand, .maskControl, .maskAlternate, .maskSecondaryFn])
        guard disallowed.isEmpty else {
            markUnsafe("keyboard shortcut")
            return
        }

        if keyCode == 51 { // delete/backspace
            guard !appendedText.isEmpty else {
                markUnsafe("backspace reached dictation text")
                return
            }
            appendedText.removeLast()
            return
        }

        let containsControl = text.unicodeScalars.contains {
            CharacterSet.controlCharacters.contains($0)
        }
        guard !text.isEmpty, !containsControl else {
            markUnsafe("navigation or non-text key")
            return
        }
        appendedText.append(text)
    }

    public mutating func markUnsafe(_ reason: String) {
        if unsafeReason == nil { unsafeReason = reason }
    }
}
