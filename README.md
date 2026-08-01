# Lucky Bingo V11 – Smart Auto Caller

A mobile-first multiplayer Bingo game for 2–20 players, deployable on Render with no database.

## New in V11

- Host chooses Manual or Auto caller when creating a room.
- Auto caller waits until every player who has the latest number marks it.
- Players who do not have the number are automatically ignored for readiness.
- Live readiness chips show Marked, Waiting, or Not on card.
- 25-second safety timeout prevents an inactive player from blocking the game forever.
- Host can also play normally.
- 3×3 uses 1–15, 4×4 uses 1–35, and 5×5 uses 1–75.
- English number pronunciation with English + Tagalog comments.
- Row, Column, Diagonal, and All Out prizes remain supported.

## Local run

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000

## Render

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
gunicorn app:app --workers 1 --threads 30 --timeout 120 --keep-alive 65
```

Environment variables:

```text
SECRET_KEY=replace-with-a-long-random-value
BINGO_VOICE=fil-PH-AngeloNeural
```

Keep exactly one Gunicorn worker because rooms are stored in process memory.
