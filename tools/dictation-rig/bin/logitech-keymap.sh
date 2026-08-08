#!/bin/sh
# logitech-keymap.sh — repair a mislabeled modifier in Joe's Logitech keyboard
# (USB receiver 046d:c52b), found 2026-08-08 while chasing "my left control
# dings and tries to dictate."
#
# THE DEFECT, measured rather than guessed (CGEvent field 87 carries the
# originating HID service's registry id, which is how two keyboards emitting
# identical keycodes get told apart):
#   - the key LEFT of the space bar, labeled Control, emitted keycode 54 =
#     RIGHT COMMAND (flags 0x100110) — byte-identical to the MacBook's own
#     right-cmd, so it fired dictation AND never worked as a Control key;
#   - the key RIGHT of the space bar, labeled Command, emits keycode 62 =
#     RIGHT CONTROL (flags 0x42100).
# The board has those two swapped in firmware. Nothing in quill-dictate could
# distinguish the false right-cmd from the real one by keycode alone, so the
# repair belongs at the HID layer, not in the app.
#
# THE REPAIR: for THIS vendor/product only, remap HID usage 0x7000000E7
# (Right GUI, what the mislabeled key actually sends) to 0x7000000E0 (Left
# Control, what its keycap promises). Scoped by VendorID/ProductID so no other
# keyboard — least of all the MacBook's — is touched. Fully reversible:
#   hidutil property --matching '{"VendorID":1133,"ProductID":50475}' \
#       --set '{"UserKeyMapping":[]}'
#
# hidutil mappings do NOT persist across reboot, sleep-with-replug, or the
# receiver being moved to another port, which is why com.carr.logitech-keymap
# runs this at login and re-applies periodically. The set is idempotent.
set -eu

hidutil property \
    --matching '{"VendorID":1133,"ProductID":50475}' \
    --set '{"UserKeyMapping":[{"HIDKeyboardModifierMappingSrc":0x7000000E7,"HIDKeyboardModifierMappingDst":0x7000000E0}]}' \
    >/dev/null 2>&1 || exit 0
