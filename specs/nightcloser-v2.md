# Nightcloser v2 (2026-10-20)
A call link: CAL_LINK https://cal.com/recareo/15min, allow-listed in unseen_contact; offered once per session (persona + code fallback on buying intent / after sample block; repeat stripped); in nudge 2.
B follow-up: web GET /followups (session ids without "web:"), POST /nudged; bridge 60 s tick (CLOSER_NUDGES=0 disables) sends only to chats in state active, never starts chats, hourly cap with reserve, per-chat chain, skips if the user wrote since the tick began, POST /nudged after send. MAX_NUDGES=2. Nudge1 3 h value+question; nudge2 20 h link+stop offer.
C KB: nightcloser/kb.py fetch (http/https, ports 80/443, all resolved IPs must be global, connect to the validated IP, max 3 redirects each re-checked, 6 s total, 400 KB, text/html), text 6000 chars, one KB per session, injected in a delimited untrusted block; treated as typed by the contact guard. Failure -> plain note + ask to paste.
D eval stub scenarios n22-n25; E deploy with backup, smoke over curl.
Kill: if live model ignores KB/link rules, code fallback stays; revert via *.bak-20261020.
