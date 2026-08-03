-- outlook-draft.applescript — commit a mailto-spawned compose window to Drafts.
--
-- argv: 1 window-title prefix (the subject)  2 "close" or "leave"
--
-- WHY THE ODD KEYSTROKE DANCE, discovered empirically 2026-08-03 and NOT guessable:
-- New Outlook DISCARDS a mailto-spawned draft on close if it considers the message
-- untouched. `File > Save` alone does not save it; neither does Cmd+S. Joe watched
-- three drafts appear in his Drafts folder and vanish the moment the compose window
-- closed. Typing a single character marks the message MODIFIED, and from then on
-- File > Save commits it and it survives the close.
--
-- So: type one character, delete it, then save. Net content change is zero and the
-- modified flag is set. Verified by Joe on CLEAN TEST 4 — draft persisted, and the
-- typed character was not present in the body.
--
-- The window is matched by TITLE PREFIX because Outlook renders it as
-- "<subject> • joe.bookout@carr.us", and the account suffix is not ours to predict.
-- Every value arrives via argv so a subject full of quotes is inert data.

on run argv
  set titlePrefix to item 1 of argv
  set closeAfter to (item 2 of argv is "close")

  tell application "Microsoft Outlook" to activate
  delay 1

  tell application "System Events"
    tell process "Microsoft Outlook"
      -- Poll for the compose window rather than trusting a fixed sleep; Outlook
      -- can take several seconds when it is cold.
      set found to missing value
      repeat 20 times
        repeat with w in windows
          if (name of w) starts with titlePrefix then
            set found to w
            exit repeat
          end if
        end repeat
        if found is not missing value then exit repeat
        delay 0.5
      end repeat

      if found is missing value then
        return "ERROR: no compose window titled " & titlePrefix
      end if

      perform action "AXRaise" of found
      delay 1

      -- mark modified, then undo the mark: type one char, delete it
      keystroke "x"
      delay 0.4
      key code 51
      delay 0.4

      click menu item "Save" of menu 1 of menu bar item "File" of menu bar 1
      delay 2

      if closeAfter then
        -- File > Close, not Cmd+W, so we use the same menu path that is known good
        try
          perform action "AXRaise" of found
          delay 0.3
          click menu item "Close" of menu 1 of menu bar item "File" of menu bar 1
          delay 1.5
        end try
      end if

      return "OK"
    end tell
  end tell
end run
