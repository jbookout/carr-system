// Inserter.swift — land transcribed text at the cursor of the frontmost app.
//
// Default path: clipboard-paste. Save what the clipboard held, put our text
// on it, post a synthetic cmd-V, restore the old contents shortly after.
// Fast, unicode-safe, works in almost every text field. Restore covers plain
// strings; a non-string clipboard (image, file) is not resurrected — noted in
// the README as a v1 limit.
//
// Alternate path ("type" in config): synthesize the text as keystrokes via
// CGEvent keyboardSetUnicodeString in small chunks. Slower, leaves the
// clipboard alone; some terminal-style apps prefer it.

import AppKit
import CoreGraphics
import Foundation

struct Inserter {
    let config: Config

    /// Marker on our synthetic events so the event tap ignores its own output.
    static let syntheticMarker: Int64 = 0x51_44_43 // "QDC"

    func insert(_ text: String) {
        guard !text.isEmpty else { return }
        if config.insertion == "type" {
            typeOut(text)
        } else {
            paste(text)
        }
    }

    private func paste(_ text: String) {
        let front = NSWorkspace.shared.frontmostApplication
        Log.shared.line("PASTE -> \(front?.localizedName ?? "?") (\(front?.bundleIdentifier ?? "?"))")
        let pasteboard = NSPasteboard.general
        let saved = pasteboard.string(forType: .string)
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)

        postKeystroke(keyCode: 9, flags: .maskCommand) // cmd-V

        // Give the frontmost app time to service the paste before the
        // clipboard goes back to what it held. 2s, not less: a 0.6s window
        // lost a live race on 2026-08-07 (TextEdit serviced the cmd-V after
        // the restore and pasted the OLD clipboard). The same-content guard
        // below makes a long window safe.
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
            let current = pasteboard.string(forType: .string)
            guard current == text else { return } // someone else owns it now
            pasteboard.clearContents()
            if let saved {
                pasteboard.setString(saved, forType: .string)
            }
        }
    }

    private func typeOut(_ text: String) {
        let source = CGEventSource(stateID: .combinedSessionState)
        // Chunked: keyboardSetUnicodeString tops out around 20 UTF-16 units
        // per event in practice.
        let units = Array(text.utf16)
        var index = 0
        while index < units.count {
            let chunk = Array(units[index ..< min(index + 16, units.count)])
            if let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true) {
                down.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: chunk)
                down.setIntegerValueField(.eventSourceUserData, value: Inserter.syntheticMarker)
                down.post(tap: .cghidEventTap)
            }
            if let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false) {
                up.setIntegerValueField(.eventSourceUserData, value: Inserter.syntheticMarker)
                up.post(tap: .cghidEventTap)
            }
            index += 16
            usleep(4000)
        }
    }

    private func postKeystroke(keyCode: CGKeyCode, flags: CGEventFlags) {
        let source = CGEventSource(stateID: .combinedSessionState)
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: false) else { return }
        down.flags = flags
        up.flags = flags
        down.setIntegerValueField(.eventSourceUserData, value: Inserter.syntheticMarker)
        up.setIntegerValueField(.eventSourceUserData, value: Inserter.syntheticMarker)
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
    }
}
