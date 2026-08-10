import CoreGraphics
import Foundation
import QuillActivity

var failures = 0
func expect(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() {
        print("ok \(label)")
    } else {
        failures += 1
        print("FAIL \(label)")
    }
}

var typing = PostReleaseActivity()
typing.recordKey(keyCode: 0, flags: [], text: "a")
typing.recordKey(keyCode: 1, flags: [.maskShift], text: "B")
typing.recordKey(keyCode: 49, flags: [], text: " ")
expect(typing.isSafe, "plain typing remains safe")
expect(typing.appendedText == "aB ", "plain typing is preserved")
expect(typing.keyDowns == 3, "keyDowns are counted")

var deletion = PostReleaseActivity()
deletion.recordKey(keyCode: 0, flags: [], text: "a")
deletion.recordKey(keyCode: 1, flags: [], text: "b")
deletion.recordKey(keyCode: 51, flags: [], text: "")
expect(deletion.appendedText == "a", "backspace edits the new suffix")
deletion.recordKey(keyCode: 51, flags: [], text: "")
deletion.recordKey(keyCode: 51, flags: [], text: "")
expect(deletion.unsafeReason == "backspace reached dictation text", "backspace cannot cross into dictation")

var shortcut = PostReleaseActivity()
shortcut.recordKey(keyCode: 8, flags: [.maskCommand], text: "c")
expect(shortcut.unsafeReason == "keyboard shortcut", "shortcut is unsafe")

var arrow = PostReleaseActivity()
arrow.recordKey(keyCode: 123, flags: [], text: "")
expect(arrow.unsafeReason == "navigation or non-text key", "navigation is unsafe")
arrow.recordKey(keyCode: 8, flags: [.maskCommand], text: "c")
expect(arrow.unsafeReason == "navigation or non-text key", "first unsafe reason wins")
expect(arrow.keyDowns == 2, "unsafe keys remain counted")

var pointer = PostReleaseActivity()
pointer.markUnsafe("mouse click")
expect(pointer.unsafeReason == "mouse click", "pointer activity is unsafe")

var grapheme = PostReleaseActivity()
grapheme.recordKey(keyCode: 0, flags: [], text: "e\u{301}")
grapheme.recordKey(keyCode: 0, flags: [], text: "👨‍👩‍👧‍👦")
grapheme.recordKey(keyCode: 51, flags: [], text: "")
expect(grapheme.appendedText == "e\u{301}", "backspace removes one Unicode grapheme")

var ime = PostReleaseActivity()
ime.recordKey(keyCode: 0, flags: [], text: "日本語")
expect(ime.appendedText == "日本語", "multi-character input is preserved")

if failures == 0 {
    print("PASS quill post-release activity checks")
    exit(0)
}
print("FAIL \(failures) quill post-release activity checks")
exit(1)
