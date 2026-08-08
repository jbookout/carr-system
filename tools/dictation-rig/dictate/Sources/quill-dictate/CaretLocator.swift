// CaretLocator.swift — finds the text-insertion caret's screen rect in the
// frontmost app, via the Accessibility API, so PreviewOverlay can anchor
// there instead of sitting fixed under the menu bar (added 2026-08-08, Joe
// wants Wispr-Flow-style caret-anchored preview).
//
// This is a READ-only AX consumer riding on the permission the event tap
// already holds — it does not request or need anything new. Kept as a free
// function returning an optional rather than a type: there is no state to
// own between calls (the caret can move between every capture), and a nil
// return is the expected, common case (many apps expose no AX text role, or
// the frontmost surface is a web view AXWebArea with partial support) rather
// than an error worth a throw.

import AppKit
import ApplicationServices

enum CaretLocator {
    /// Electron/Chromium apps keep their AX tree OFF until a client flips
    /// this app-level attribute — without it, every query returns
    /// cannotComplete and the caret can never be found (probed live against
    /// the Claude desktop app, 2026-08-08: set returns success, and the tree
    /// starts answering). Flipped at most once per pid; the set is harmless
    /// on apps that don't implement the attribute (notImplemented error).
    private static var activatedPids = Set<pid_t>()

    private static func activateElectronTree(for app: NSRunningApplication) {
        let pid = app.processIdentifier
        guard !activatedPids.contains(pid) else { return }
        activatedPids.insert(pid)
        let appElement = AXUIElementCreateApplication(pid)
        AXUIElementSetAttributeValue(appElement, "AXManualAccessibility" as CFString, kCFBooleanTrue)
        AXUIElementSetAttributeValue(appElement, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
    }

    /// Screen rect of the caret (or the collapsed selection) in AppKit
    /// bottom-left-origin coordinates, or nil if it cannot be determined.
    /// Never throws — one pass, with a single retry through the frontmost
    /// app's own AX element after the Electron activation flip, since this
    /// runs ahead of showing the overlay and must not add noticeable delay
    /// to "the panel appeared." First capture inside a freshly-activated
    /// Electron app may still fall back (the tree builds asynchronously);
    /// the second one anchors.
    static func caretScreenRect() -> CGRect? {
        let systemWide = AXUIElementCreateSystemWide()

        var focusedElement: AXUIElement?
        var focusedRef: CFTypeRef?
        let focusedResult = AXUIElementCopyAttributeValue(
            systemWide, kAXFocusedUIElementAttribute as CFString, &focusedRef
        )
        if focusedResult == .success, let focusedRef,
           CFGetTypeID(focusedRef) == AXUIElementGetTypeID() {
            focusedElement = (focusedRef as! AXUIElement)
        } else if let front = NSWorkspace.shared.frontmostApplication {
            // System-wide lookup failed — the Electron-off-by-default case.
            // Flip the tree on and ask the app element directly.
            activateElectronTree(for: front)
            usleep(150_000)
            let appElement = AXUIElementCreateApplication(front.processIdentifier)
            var appFocusedRef: CFTypeRef?
            let appResult = AXUIElementCopyAttributeValue(
                appElement, kAXFocusedUIElementAttribute as CFString, &appFocusedRef
            )
            guard appResult == .success, let appFocusedRef,
                  CFGetTypeID(appFocusedRef) == AXUIElementGetTypeID() else { return nil }
            focusedElement = (appFocusedRef as! AXUIElement)
        }
        guard let focusedElement else { return nil }

        var rangeRef: CFTypeRef?
        let rangeResult = AXUIElementCopyAttributeValue(
            focusedElement, kAXSelectedTextRangeAttribute as CFString, &rangeRef
        )
        guard rangeResult == .success, let rangeRef,
              CFGetTypeID(rangeRef) == AXValueGetTypeID() else { return nil }
        let rangeValue = rangeRef as! AXValue
        guard AXValueGetType(rangeValue) == .cfRange else { return nil }
        var cfRange = CFRange()
        guard AXValueGetValue(rangeValue, .cfRange, &cfRange) else { return nil }

        var boundsRef: CFTypeRef?
        let boundsResult = AXUIElementCopyParameterizedAttributeValue(
            focusedElement,
            kAXBoundsForRangeParameterizedAttribute as CFString,
            rangeValue,
            &boundsRef
        )
        guard boundsResult == .success, let boundsRef,
              CFGetTypeID(boundsRef) == AXValueGetTypeID() else { return nil }
        let boundsValue = boundsRef as! AXValue
        guard AXValueGetType(boundsValue) == .cgRect else { return nil }
        var axRect = CGRect.zero
        guard AXValueGetValue(boundsValue, .cgRect, &axRect) else { return nil }

        // An empty field with no real caret position sometimes reports
        // exactly (0,0,0,0) instead of a real zero-width rect at the caret —
        // that combination is the failure signature, not a valid answer. A
        // genuine collapsed-selection caret still has a nonzero origin (it
        // sits somewhere in the field, not at the screen corner) even when
        // its width is zero, so width/height alone can't be the test.
        if axRect.origin == .zero && axRect.size == .zero { return nil }

        // AX bounds are top-left-origin (y grows downward from the primary
        // screen's top); AppKit is bottom-left-origin. Only the PRIMARY
        // screen's height anchors this conversion — NSScreen.main is
        // whichever screen has key focus, which is not the same thing and
        // gives a wrong flip on a multi-monitor setup where the primary
        // isn't frontmost.
        guard let primaryHeight = NSScreen.screens.first?.frame.height else { return nil }
        let nsY = primaryHeight - axRect.origin.y - axRect.size.height
        return CGRect(x: axRect.origin.x, y: nsY, width: axRect.size.width, height: axRect.size.height)
    }
}
