-- outlook-draft.applescript — create a DRAFT in the local Outlook, never send.
--
-- Called by bin/outlook-draft.py, which is the interface you should use. Every
-- value arrives as an argv item so no caller text is ever interpolated into this
-- script: a subject containing quotes, backslashes or AppleScript keywords is
-- inert data here, which is the whole reason for the argv shape.
--
-- argv: 1 subject · 2 html body · 3 to (comma-joined) · 4 cc · 5 bcc
--
-- THERE IS NO SEND PATH IN THIS FILE AND THERE MUST NEVER BE ONE. `make new
-- outgoing message` persists straight into Drafts on its own — no `save` call,
-- which in Outlook's dictionary means save-to-file and errors with -1701. The
-- human opens Drafts, reviews, and presses Send. That is the one human gate.

on splitOn(theText, theDelim)
  set savedDelims to AppleScript's text item delimiters
  set AppleScript's text item delimiters to theDelim
  set theParts to text items of theText
  set AppleScript's text item delimiters to savedDelims
  return theParts
end splitOn

on trimmed(s)
  set s to s as text
  repeat while s starts with " "
    if length of s is 1 then return ""
    set s to text 2 thru -1 of s
  end repeat
  repeat while s ends with " "
    if length of s is 1 then return ""
    set s to text 1 thru -2 of s
  end repeat
  return s
end trimmed

on run argv
  set theSubject to item 1 of argv
  set theBody to item 2 of argv
  set toRaw to item 3 of argv
  set ccRaw to item 4 of argv
  set bccRaw to item 5 of argv

  tell application "Microsoft Outlook"
    -- `content` is typed as HTML in Outlook's dictionary; the Python wrapper
    -- escapes and wraps the body so what arrives here is already valid HTML.
    set m to make new outgoing message with properties {subject:theSubject, content:theBody}

    repeat with a in my splitOn(toRaw, ",")
      set addr to my trimmed(a)
      if addr is not "" then
        make new to recipient at m with properties {email address:{address:addr}}
      end if
    end repeat

    repeat with a in my splitOn(ccRaw, ",")
      set addr to my trimmed(a)
      if addr is not "" then
        make new cc recipient at m with properties {email address:{address:addr}}
      end if
    end repeat

    repeat with a in my splitOn(bccRaw, ",")
      set addr to my trimmed(a)
      if addr is not "" then
        make new bcc recipient at m with properties {email address:{address:addr}}
      end if
    end repeat

    return (id of m) as string
  end tell
end run
