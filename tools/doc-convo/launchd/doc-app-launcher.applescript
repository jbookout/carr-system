-- Doc.app — the private front door for Joe and Dell (2026-08-08).
-- Opens Doc in a chromeless Chrome window pointed at the always-on local
-- engine (launchd com.carr.doc-engine). No URL to remember, no browser tab.
-- If the engine is down, launchd restarts it; we wait briefly, then open.
on run
	set engineUp to false
	repeat with i from 1 to 10
		try
			do shell script "curl -sf -m 1 http://127.0.0.1:4680/state > /dev/null"
			set engineUp to true
			exit repeat
		on error
			delay 1
		end try
	end repeat
	if engineUp then
		do shell script "open -na 'Google Chrome' --args --app=http://127.0.0.1:4680 --window-size=430,860"
	else
		display dialog "Doc's engine isn't answering." & return & return & "It runs itself in the background; if this keeps happening, run:" & return & "launchctl kickstart -k gui/$(id -u)/com.carr.doc-engine" buttons {"OK"} default button 1 with title "Doc"
	end if
end run
