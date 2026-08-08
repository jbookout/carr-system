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
    /// Screen rect of the caret (or the collapsed selection) in AppKit
    /// bottom-left-origin coordinates, or nil if it cannot be determined.
    /// Never throws, never retries — one shot at the AX calls and back out,
    /// since this runs ahead of showing the overlay and must not add
    /// noticeable delay to "the panel appeared."
    static func caretScreenRect() -> CGRect? {
        let systemWide = AXUIElementCreateSystemWide()

        var focusedRef: CFTypeRef?
        let focusedResult = AXUIElementCopyAttributeValue(
            systemWide, kAXFocusedUIElementAttribute as CFString, &focusedRef
        )
        guard focusedResult == .success, let focusedRef,
              CFGetTypeID(focusedRef) == AXUIElementGetTypeID() else { return nil }
        let focusedElement = focusedRef as! AXUIElement

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
