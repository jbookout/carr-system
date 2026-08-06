$ErrorActionPreference = "Stop"
Import-Module ExchangeOnlineManagement
# OPTIONAL determinism pin (loop #200). Joe's Calendar was PUBLISHED from OWA on
# 2026-08-06 ("Can view all details"); the published calendar.ics feed already
# reached ~11 months back at creation. This pins PublishDateRangeFrom to OneYear
# explicitly so the window can never silently narrow to a default.
# History: the original feed was a reachcalendar.ics SHARE link, which this
# cmdlet does not govern at all — that was the whole 2026-08-06 hunt.
# Interactive browser sign-in (device-code flow is blocked by CARR's
# Conditional Access; interactive passed 2026-08-06). Run from Joe's Mac:
#   pwsh -NoProfile -File ~/carr-system/ops/widen-calendar-window.ps1
Connect-ExchangeOnline -UserPrincipalName joe.bookout@carr.us -ShowBanner:$false
Write-Host "=== CONNECTED ==="
$id = "joe.bookout@carr.us:\Calendar"
Write-Host "--- BEFORE ---"
Get-MailboxCalendarFolder -Identity $id | Format-List PublishEnabled,PublishDateRangeFrom,PublishDateRangeTo,PublishedICalUrl
# No -ResetUrl, ever: that would mint a new URL and orphan ~/.config/carr/calendar.env.
Set-MailboxCalendarFolder -Identity $id -PublishDateRangeFrom OneYear
Write-Host "--- AFTER ---"
Get-MailboxCalendarFolder -Identity $id | Format-List PublishEnabled,PublishDateRangeFrom,PublishDateRangeTo,PublishedICalUrl
Disconnect-ExchangeOnline -Confirm:$false
Write-Host "=== DONE ==="
