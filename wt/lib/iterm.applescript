on run argv
    tell application "iTerm2"
        activate
        if (count of argv) > 1 then
            create window with profile (item 2 of argv) command (item 1 of argv)
        else
            create window with default profile command (item 1 of argv)
        end if
    end tell
end run
