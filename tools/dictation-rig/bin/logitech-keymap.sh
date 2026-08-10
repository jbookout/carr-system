#!/bin/sh
# logitech-keymap.sh — repair swapped right-side modifiers on Joe's Logitech
# keyboard (USB receiver 046d:c52b).
#
# THE DEFECT, measured rather than guessed (CGEvent field 87 carries the
# originating HID service's registry id, which is how two keyboards emitting
# identical keycodes get told apart):
#   - the Command key immediately RIGHT of space emits RIGHT CONTROL (E4);
#   - the Control key farther right emits RIGHT GUI / Command (E7).
# The prior workaround repaired only the second key (E7 -> left Control) and
# taught Quill to accept right-Control too. That made the physical Control key
# the Quill trigger whenever the transient hidutil mapping was absent. Repair
# both directions instead so the labels, macOS semantics, and Quill all agree.
#
# THE REPAIR: for THIS vendor/product only, swap HID Right Control (E4) and
# Right GUI / Command (E7). The immediate-right Command becomes a real Command
# and the farther-right Control becomes a real Control. Scoped by
# VendorID/ProductID so no other keyboard — least of all the MacBook's — is
# touched. Fully reversible:
#   hidutil property --matching '{"VendorID":1133,"ProductID":50475}' \
#       --set '{"UserKeyMapping":[]}'
#
# hidutil mappings do NOT persist across reboot, sleep-with-replug, or the
# receiver being moved to another port, which is why com.carr.logitech-keymap
# runs this at login and re-applies periodically. The set is idempotent.
set -eu

/usr/bin/hidutil property \
    --matching '{"VendorID":1133,"ProductID":50475}' \
    --set '{"UserKeyMapping":[{"HIDKeyboardModifierMappingSrc":0x7000000E4,"HIDKeyboardModifierMappingDst":0x7000000E7},{"HIDKeyboardModifierMappingSrc":0x7000000E7,"HIDKeyboardModifierMappingDst":0x7000000E4}]}' \
    >/dev/null
