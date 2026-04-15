#!/bin/bash
# Deploy a file to theartofthepossible.io on GreenGeeks
# Usage: ./deploy_to_greengeeks.sh <local_file> <site_path>
# Example: ./deploy_to_greengeeks.sh drdata_full.html drdata
set -e

LOCAL_FILE="$1"
SITE_PATH="$2"  # e.g. "drdata" or "focusflow"

if [ -z "$LOCAL_FILE" ] || [ -z "$SITE_PATH" ]; then
  echo "Usage: $0 <local_file> <site_path>"
  exit 1
fi

source ~/.env 2>/dev/null || export $(grep -v '^#' ~/.env | xargs)

DEST_DIR="/home/${GREENGEEKS_USERNAME}/public_html/theartofthepossible.io/${SITE_PATH}"

echo "Deploying $LOCAL_FILE → $DEST_DIR/index.html"

python3 << PYEOF
import os, urllib.request, urllib.parse, json, ssl, socket, subprocess

token = "${GREENGEEKS_API_TOKEN}"
host  = "${GREENGEEKS_HOST}"
user  = "${GREENGEEKS_USERNAME}"
dest_dir = "${DEST_DIR}"
local_file = "${LOCAL_FILE}"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
headers = {'Authorization': f'cpanel {user}:{token}'}

html = open(local_file).read()
print(f"File: {len(html)} bytes")

body = urllib.parse.urlencode({'dir': dest_dir, 'file': 'index.html', 'content': html, 'charset': 'UTF-8'}).encode()
req = urllib.request.Request(f'https://{host}:2083/execute/Fileman/save_file_content', data=body,
      headers={**headers, 'Content-Type': 'application/x-www-form-urlencoded'})
with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
    res = json.loads(r.read().decode())
print("Upload:", res.get('status'), res.get('errors'))

ip = socket.gethostbyname(host)
r = subprocess.run(['curl','-sk','--resolve',f'theartofthepossible.io:443:{ip}',
    f'https://theartofthepossible.io/${SITE_PATH}/','-o','/dev/null','-w','%{size_download} %{http_code}'],
    capture_output=True, text=True)
print("Origin verify:", r.stdout)
PYEOF
