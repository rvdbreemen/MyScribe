on run
	set out to ""
	tell application "System Events"
		if not (exists process "MyScribe") then return "MyScribe process not found"
		tell process "MyScribe"
			set out to out & "windows: " & (count of windows) & linefeed
			repeat with w in windows
				set out to out & "WINDOW name: " & (name of w) & linefeed
				try
					set out to out & "WINDOW desc: " & (description of w) & linefeed
				end try
				try
					set out to out & "WINDOW pos: " & (position of w) & " size: " & (size of w) & linefeed
				end try
			end repeat
		end tell
		tell process "MyScribe"
			repeat with w in windows
				repeat with e in (entire contents of w)
					try
						set r to (role of e) as text
						set nm to ""
						try
							set nm to (name of e) as text
						end try
						set v to ""
						try
							set v to (value of e) as text
						end try
						if v is not "" then set out to out & "TEXT " & r & ": " & v & linefeed
						if nm is not "" then set out to out & "NAME " & r & ": " & nm & linefeed
					end try
				end repeat
			end repeat
		end tell
	end tell
	return out
end run
