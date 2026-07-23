#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  osascript -e 'display dialog "Python 3 was not found. Install Python 3 from python.org and try again." buttons {"OK"} default button "OK" with icon stop'
  exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
  osascript -e 'display dialog "This Python installation does not include Tkinter. Install Python 3 from python.org and try again." buttons {"OK"} default button "OK" with icon stop'
  exit 1
fi

python3 "$SCRIPT_DIR/coralnet_image_downloader_gui.py"
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
  echo
  echo "The app exited with status $STATUS."
  read -r -p "Press Return to close..."
fi

exit "$STATUS"
