# Lucky Bingo Pro – Responsive UI Edition

## Local run
```bash
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000

## Render
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app --workers 1 --threads 40 --timeout 120 --keep-alive 65`

Use one worker because rooms are stored in memory.
