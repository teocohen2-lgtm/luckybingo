# Lucky Bingo V10 — Power Modes

## Game modes
- **3×3 Fast:** numbers 1–15, FREE center.
- **4×4 Medium:** numbers 1–35, no FREE center.
- **5×5 Classic:** numbers 1–75, FREE center.

The selected mode controls card generation, called-number board, remaining count, letter ranges, Catch Up, voice announcements, and all prize validation.

## Voice
- Letter and number are spoken in English words.
- Fun comment is English + Tagalog.
- Every player taps **Enable Announcer** once because mobile browsers require a user gesture.
- MP3 calls are generated with the Filipino male neural voice, cached, and retried on transient failures.

## Render
Build: `pip install -r requirements.txt`

Start: `gunicorn app:app --workers 1 --threads 30 --timeout 120 --keep-alive 65`

Environment variables:
- `SECRET_KEY`: a long random value
- `BINGO_VOICE`: defaults to `fil-PH-AngeloNeural`
