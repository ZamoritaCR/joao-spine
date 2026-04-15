
## CRITICAL: GreenGeeks Deploy Path (2026-04-15)

theartofthepossible.io docroot: `/home/pcmodder/public_html/theartofthepossible.io/`

| Site path | cPanel path |
|-----------|-------------|
| /drdata/  | /home/pcmodder/public_html/theartofthepossible.io/drdata/ |
| /focusflow/ | /home/pcmodder/public_html/theartofthepossible.io/focusflow/ |
| /joao/    | /home/pcmodder/public_html/theartofthepossible.io/joao/ |

**WRONG** (do not use): `/home/pcmodder/public_html/drdata/`

Deploy: `~/deploy_to_greengeeks.sh <file> <path>`
Verify: `curl -sk --resolve theartofthepossible.io:443:$(python3 -c "import socket; print(socket.gethostbyname('chi209.greengeeks.net'))") https://theartofthepossible.io/drdata/ -o /dev/null -w '%{size_download} %{http_code}'`
