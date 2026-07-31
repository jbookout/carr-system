-- notes-sweep.applescript — the READ half of the Apple Notes call-recording
-- sweep (ORDER 12 lane (a); Fable ruling 2026-07-31 adopting the flagged
-- deviation: the whole hourly run is ONE Bash command, so no per-note file
-- writes by a scheduled session and therefore no second unattended-permission
-- surface. One byte-stable command, one persisted approval.)
--
-- Called only by bin/notes-sweep-post.sh. Two modes:
--   ids   <folderName>                        one note id per line, or NOFOLDER
--   fetch <folderName> <outDir> <idx> [...]   per-note files into outDir
--
-- WHY NOTE TEXT COMES BACK IN FILES AND NOT ON STDOUT: a transcript contains
-- arbitrary text, including any delimiter a parser might trust. Writing each
-- field to its own UTF-8 file removes the question entirely — there is no
-- escaping, no marker, and nothing a note body can say that changes how it is
-- read. Only note IDs and integer indices travel on stdout, and those are
-- machine-generated.
--
-- WHY `plaintext` AND NEVER `body`: `body` is HTML with attachments inlined.
-- Measured 2026-07-31 on a real note — plaintext 24 characters, body 983,221.
-- The socket's ceiling is 1 MiB, so `body` would fail on ordinary notes while
-- carrying nothing the record layer wants.

on findFolder(folderName)
	tell application "Notes"
		repeat with acc in accounts
			repeat with fld in folders of acc
				if (name of fld as string) is folderName then return fld
			end repeat
		end repeat
	end tell
	return missing value
end findFolder

on writeUTF8(pth, txt)
	set fh to open for access (POSIX file pth) with write permission
	try
		set eof fh to 0
		write txt to fh as «class utf8»
		close access fh
	on error errm number errn
		try
			close access fh
		end try
		error errm number errn
	end try
end writeUTF8

on run argv
	if (count of argv) < 2 then return "USAGE"
	set mode to item 1 of argv
	set folderName to item 2 of argv

	set f to my findFolder(folderName)
	if f is missing value then return "NOFOLDER"

	if mode is "ids" then
		set out to ""
		tell application "Notes"
			repeat with aNote in notes of f
				set out to out & (id of aNote as string) & linefeed
			end repeat
		end tell
		return out
	end if

	if mode is not "fetch" then return "USAGE"
	if (count of argv) < 4 then return "OK 0"
	set outDir to item 3 of argv

	set n to 0
	repeat with i from 4 to (count of argv)
		set idx to (item i of argv) as integer
		tell application "Notes"
			set aNote to item idx of (notes of f)
			set theId to (id of aNote) as string
			set theName to (name of aNote) as string
			set theCreated to (creation date of aNote) as string
			set theModified to (modification date of aNote) as string
			set theText to ""
			try
				set theText to (plaintext of aNote) as string
			end try
		end tell
		-- The id file is written LAST on purpose: the shell side treats its
		-- presence as the signal that this note's whole record is complete, so a
		-- run interrupted mid-note cannot queue a half-read transcript.
		my writeUTF8(outDir & "/" & idx & ".name", theName)
		my writeUTF8(outDir & "/" & idx & ".created", theCreated)
		my writeUTF8(outDir & "/" & idx & ".modified", theModified)
		my writeUTF8(outDir & "/" & idx & ".text", theText)
		my writeUTF8(outDir & "/" & idx & ".id", theId)
		set n to n + 1
	end repeat
	return "OK " & n
end run
