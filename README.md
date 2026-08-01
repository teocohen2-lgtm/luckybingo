# Lucky Bingo Pro V20

Targeted fixes applied to the supplied project:

1. The first automatic number is called immediately when the host starts an automatic game.
2. A host-browser heartbeat calls `/api/auto-tick` every 800 ms. The backend remains authoritative and calls a new number only when everyone who has the latest number is ready, or the safety timeout expires.
3. The original server-generated neural voices remain unchanged: `fil-PH-AngeloNeural`, with `en-PH-JamesNeural` as the server fallback.
4. Mobile playback now uses an unlocked Web Audio `AudioContext` and decoded MP3 buffers. This avoids delayed HTML-audio autoplay blocking on Android and iPhone.
5. The existing UI and card alignment are preserved.

## Local run

```bash
pip install -r requirements.txt
python app.py
```

## Render

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
gunicorn app:app --workers 1 --threads 30 --timeout 120 --keep-alive 65
```

Use one worker because rooms are stored in memory.
