// PreviewOverlay.swift — floating "what I'm hearing" panel (added 2026-08-08).
//
// Pure presentation: it owns no timer, no network call, no whisper state —
// App.swift drives it with show/update/hide. The one hard requirement is
// that it NEVER steals key focus, since Joe is mid-dictation into whatever
// field was already focused; a panel that becomes key would yank focus away
// from that field and break the very insert this whole rig exists for.
// canBecomeKey is overridden false structurally rather than just "not called"
// so no future call site can get this wrong by accident.

import AppKit

/// NSPanel subclass — canBecomeKey/canBecomeMain hard-pinned false so the
/// overlay can never take focus from whatever text field Joe is dictating
/// into, regardless of how it's shown (orderFront, makeKeyAndOrderFront,
/// etc. all funnel through these).
private final class NonActivatingPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class PreviewOverlay {
    private var panel: NonActivatingPanel?
    private var label: NSTextField!

    private let maxWidth: CGFloat = 480
    private let horizontalPadding: CGFloat = 16
    private let verticalPadding: CGFloat = 10
    private let caretGap: CGFloat = 8

    /// Caret rect (AppKit bottom-left coords) to anchor under, captured once
    /// per `show` and reused by every `update` in the same capture — nil
    /// means "no caret found this capture," which keeps the old fixed
    /// under-the-menu-bar position rather than jumping around on a miss.
    private var anchor: CGRect?

    /// Build (once) and show the panel with placeholder text. Cheap to call
    /// repeatedly — reuses the same panel across captures instead of
    /// rebuilding, so rapid push-to-talk taps don't churn NSWindow objects.
    /// `near` is the caret rect from CaretLocator, already converted to
    /// AppKit coordinates; pass nil (or omit) to keep the original
    /// under-the-menu-bar placement, e.g. when the caret couldn't be found.
    func show(initial: String, near anchor: CGRect? = nil) {
        DispatchQueue.main.async { [self] in
            if panel == nil { buildPanel() }
            self.anchor = anchor
            setText(initial)
            reposition()
            panel?.orderFrontRegardless() // NOT makeKeyAndOrderFront — never steal focus
        }
    }

    /// Replace the whole displayed hypothesis. This is a rolling re-decode
    /// (App.swift re-transcribes the last N seconds each tick), not an
    /// append, so each call fully replaces the prior text rather than
    /// concatenating.
    func update(text: String) {
        DispatchQueue.main.async { [self] in
            guard panel != nil else { return } // hidden already; drop stale ticks
            setText(text)
            reposition() // text height may have changed the panel's size
        }
    }

    func hide() {
        DispatchQueue.main.async { [self] in
            panel?.orderOut(nil)
        }
    }

    private func setText(_ text: String) {
        label.stringValue = text.isEmpty ? "…" : text
    }

    private func buildPanel() {
        let p = NonActivatingPanel(
            contentRect: NSRect(x: 0, y: 0, width: maxWidth, height: 60),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        p.level = .floating
        p.isOpaque = false
        p.backgroundColor = .clear
        p.hasShadow = true
        p.ignoresMouseEvents = true // a preview readout, not a clickable control
        p.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]

        let background = NSVisualEffectView(frame: p.contentView!.bounds)
        background.material = .hudWindow
        background.state = .active
        background.blendingMode = .behindWindow
        background.wantsLayer = true
        background.layer?.cornerRadius = 12
        background.layer?.masksToBounds = true
        // HUD material already reads dark-translucent in both appearances;
        // pin dark explicitly so the overlay text (white) always has contrast
        // regardless of Joe's system appearance (writing-rules doesn't reach
        // this — it's not prospect-visible — but readability still matters).
        background.appearance = NSAppearance(named: .darkAqua)

        let text = NSTextField(labelWithString: "…")
        text.textColor = .white
        text.font = .systemFont(ofSize: 14, weight: .regular)
        text.alignment = .center
        text.lineBreakMode = .byTruncatingHead // keep the END of the hypothesis, matching a live sentence's most-recent words
        text.maximumNumberOfLines = 3
        text.cell?.wraps = true
        text.cell?.isScrollable = false
        text.backgroundColor = .clear
        text.isBezeled = false
        text.isEditable = false
        text.isSelectable = false

        background.addSubview(text)
        p.contentView = background
        self.label = text
        self.panel = p
    }

    /// Anchored under the text caret when one was found for this capture
    /// (see `anchor`/Wispr-Flow-style placement, 2026-08-08); otherwise
    /// horizontally centered on the main screen, just below the menu bar —
    /// the original placement, untouched. Recomputed on every show/update
    /// because the label's height changes with content (1-3 lines).
    /// NSTextField has no UIKit-style sizeThatFits, so the wrapped height is
    /// measured directly off the string with boundingRect and clamped to 3
    /// lines worth of height.
    private func reposition() {
        guard let panel else { return }
        let availableWidth = maxWidth - horizontalPadding * 2
        let fitHeight = PreviewOverlay.measuredHeight(
            for: label.stringValue, font: label.font ?? .systemFont(ofSize: 14),
            width: availableWidth, maxLines: 3
        )
        let panelWidth = maxWidth
        let panelHeight = fitHeight + verticalPadding * 2

        label.frame = NSRect(x: horizontalPadding, y: verticalPadding, width: availableWidth, height: fitHeight)

        let origin: CGPoint
        if let anchor {
            origin = anchoredOrigin(for: anchor, panelWidth: panelWidth, panelHeight: panelHeight)
        } else {
            guard let screen = NSScreen.main else { return }
            let screenFrame = screen.frame
            let visibleFrame = screen.visibleFrame
            // Just below the menu bar: menu bar occupies the gap between
            // frame.maxY and visibleFrame.maxY on the main screen.
            let topY = screenFrame.maxY - (screenFrame.maxY - visibleFrame.maxY) - 8
            origin = CGPoint(x: screenFrame.midX - panelWidth / 2, y: topY - panelHeight)
        }

        panel.setFrame(NSRect(x: origin.x, y: origin.y, width: panelWidth, height: panelHeight), display: true)
        (panel.contentView as? NSVisualEffectView)?.frame = NSRect(x: 0, y: 0, width: panelWidth, height: panelHeight)
    }

    /// Panel origin for the caret-anchored case: starts at the caret's x,
    /// top edge `caretGap` below the caret's bottom edge (AppKit y grows
    /// upward, so "below" is the caret's minY), then clamped fully onto
    /// whichever screen the caret sits on. Clamping matters because a caret
    /// near a screen edge — e.g. the last line of a maximized window, or a
    /// caret near the right edge on a narrow window — would otherwise push
    /// the fixed-width panel partway or fully off-screen.
    private func anchoredOrigin(for anchor: CGRect, panelWidth: CGFloat, panelHeight: CGFloat) -> CGPoint {
        let screen = PreviewOverlay.screen(containing: anchor.origin) ?? NSScreen.main
        var originX = anchor.minX
        var originY = anchor.minY - caretGap - panelHeight

        if let bounds = screen?.frame {
            // No room below the anchor -> flip ABOVE it. This is the normal
            // case when the anchor is a prompt box at the bottom of a window
            // (the Claude app's, notably): clamping alone would drop the
            // panel ON TOP of the box instead of beside it (found live
            // 2026-08-08).
            if originY < bounds.minY {
                originY = anchor.maxY + caretGap
            }
            originX = min(max(originX, bounds.minX), bounds.maxX - panelWidth)
            originY = min(max(originY, bounds.minY), bounds.maxY - panelHeight)
        }
        return CGPoint(x: originX, y: originY)
    }

    private static func screen(containing point: CGPoint) -> NSScreen? {
        NSScreen.screens.first { $0.frame.contains(point) }
    }

    private static func measuredHeight(for string: String, font: NSFont, width: CGFloat, maxLines: Int) -> CGFloat {
        let attrs: [NSAttributedString.Key: Any] = [.font: font]
        let bounds = NSRect(x: 0, y: 0, width: width, height: .greatestFiniteMagnitude)
        let rect = (string as NSString).boundingRect(
            with: bounds.size,
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: attrs
        )
        let lineHeight = font.ascender - font.descender + font.leading
        let maxHeight = lineHeight * CGFloat(maxLines)
        return max(lineHeight, min(rect.height.rounded(.up), maxHeight))
    }
}
