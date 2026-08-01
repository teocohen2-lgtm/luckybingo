import asyncio
import hashlib
import os
import random
import string
import threading
import time
import uuid
from pathlib import Path

import edge_tts
from flask import Flask, jsonify, render_template, request, send_file, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-on-render")

@app.after_request
def disable_stale_browser_cache(response):
    if response.mimetype in {"text/html", "application/json", "text/javascript"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

ROOMS = {}
LOCK = threading.RLock()
ROOM_CHANGED = threading.Condition(LOCK)
AUDIO_LOCK = threading.RLock()
MAX_PLAYERS = 20
MIN_PLAYERS = 2
CARD_SIZES = (3, 4, 5)
CARD_LAYOUTS = {
    # Fast 15-ball game: three compact columns, five possible values per column.
    3: [("B", 1, 5), ("N", 6, 10), ("O", 11, 15)],
    # Medium 35-ball game: values are distributed across four B-I-G-O columns.
    4: [("B", 1, 9), ("I", 10, 18), ("G", 19, 27), ("O", 28, 35)],
    # Classic 75-ball game.
    5: [("B", 1, 15), ("I", 16, 30), ("N", 31, 45), ("G", 46, 60), ("O", 61, 75)],
}
TOTAL_NUMBERS = {3: 15, 4: 35, 5: 75}
AUDIO_DIR = Path(os.environ.get("BINGO_AUDIO_DIR", "/tmp/bingo_audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
VOICE = os.environ.get("BINGO_VOICE", "fil-PH-AngeloNeural")
PRIZE_KEYS = ("row", "column", "diagonal", "all_out")
PRIZE_LABELS = {"row": "Row", "column": "Column", "diagonal": "Diagonal", "all_out": "All Out"}
AUTO_INTERVALS = (3, 5, 8, 10, 15)
MAX_READY_WAIT = 25


def bump(room):
    room["version"] = room.get("version", 0) + 1
    ROOM_CHANGED.notify_all()


def room_code():
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choices(alphabet, k=5))
        if code not in ROOMS:
            return code


def card_number_pool(size):
    return [number for _, start, end in CARD_LAYOUTS[size] for number in range(start, end + 1)]


def make_card(size=5):
    if size not in CARD_SIZES:
        raise ValueError("Unsupported card size")
    layout = CARD_LAYOUTS[size]
    columns = [random.sample(range(start, end + 1), size) for _, start, end in layout]
    card = [[{"value": columns[col][row], "free": False} for col in range(size)] for row in range(size)]
    # Traditional center FREE space on odd-sized cards. A 4x4 card has no single center cell.
    if size % 2 == 1:
        center = size // 2
        card[center][center] = {"value": "FREE", "free": True}
    return card


def card_key(card):
    return tuple(cell["value"] for row in card for cell in row if not cell["free"])


def unique_card(room):
    size = room["card_size"]
    existing = {card_key(player["card"]) for player in room["players"].values()}
    for _ in range(500):
        card = make_card(size)
        if card_key(card) not in existing:
            return card
    return make_card(size)


def bingo_letter(number, size=5):
    """Return the correct column letter for the selected card mode."""
    for letter, start, end in CARD_LAYOUTS[size]:
        if start <= number <= end:
            return letter
    raise ValueError(f"Number {number} is outside the {size}x{size} game range")


BILINGUAL_LINES = {
    1: "Number one! Unang bola—good luck, mga ka-bingo!",
    2: "Number two! Dalawa na—check your card!",
    5: "Number five! High five, mga ka-bingo!",
    7: "Lucky number seven! Swerte na, baka bingo na!",
    8: "Number eight! Don't be late—mark it now!",
    10: "Perfect ten! Parang perfect score sa karaoke!",
    11: "Number eleven! Two straight lines—tingnan ang card!",
    12: "Number twelve! Isang dosena—mark it, please!",
    13: "Number thirteen! Not unlucky today—swerte tayo!",
    15: "Number fifteen! Payday feeling—sana all!",
    18: "Number eighteen! Legal na—pero behave pa rin!",
    20: "Number twenty! Bente na—check your card!",
    21: "Number twenty-one! Adulting later, bingo first!",
    22: "Number twenty-two! Two little ducks—quack quack!",
    25: "Number twenty-five! Quarter century—buhay na buhay!",
    30: "Number thirty! Clean and lucky—hindi dirty!",
    33: "Number thirty-three! Double three—parang instant noodles!",
    40: "Number forty! Life begins, and bingo continues!",
    44: "Number forty-four! Double four—open the door!",
    45: "Number forty-five! Sing until five—videoke later!",
    50: "Number fifty! Halfway to one hundred—lapit na!",
    55: "Number fifty-five! Double five—high five ulit!",
    60: "Number sixty! Keep going—malapit na ang bingo!",
    66: "Number sixty-six! Double six—click na click!",
    69: "Number sixty-nine! Baliktaran—eyes on your card!",
    70: "Number seventy! Pitumpu na—stay alert!",
    75: "Number seventy-five! Last ball—bingo na ba, mga kaibigan?",
}


ENGLISH_NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
    16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
    21: "twenty-one", 22: "twenty-two", 23: "twenty-three", 24: "twenty-four", 25: "twenty-five",
    26: "twenty-six", 27: "twenty-seven", 28: "twenty-eight", 29: "twenty-nine", 30: "thirty",
    31: "thirty-one", 32: "thirty-two", 33: "thirty-three", 34: "thirty-four", 35: "thirty-five",
    36: "thirty-six", 37: "thirty-seven", 38: "thirty-eight", 39: "thirty-nine", 40: "forty",
    41: "forty-one", 42: "forty-two", 43: "forty-three", 44: "forty-four", 45: "forty-five",
    46: "forty-six", 47: "forty-seven", 48: "forty-eight", 49: "forty-nine", 50: "fifty",
    51: "fifty-one", 52: "fifty-two", 53: "fifty-three", 54: "fifty-four", 55: "fifty-five",
    56: "fifty-six", 57: "fifty-seven", 58: "fifty-eight", 59: "fifty-nine", 60: "sixty",
    61: "sixty-one", 62: "sixty-two", 63: "sixty-three", 64: "sixty-four", 65: "sixty-five",
    66: "sixty-six", 67: "sixty-seven", 68: "sixty-eight", 69: "sixty-nine", 70: "seventy",
    71: "seventy-one", 72: "seventy-two", 73: "seventy-three", 74: "seventy-four", 75: "seventy-five",
}


def generate_voice_file(text, path, rate="-4%", pitch="-2Hz"):
    """Generate and cache audio, retrying transient network/TTS failures."""
    last_error = None
    temp_path = path.with_suffix(".part.mp3")
    for attempt in range(3):
        try:
            if temp_path.exists():
                temp_path.unlink()
            asyncio.run(edge_tts.Communicate(text, VOICE, rate=rate, pitch=pitch).save(str(temp_path)))
            if not temp_path.exists() or temp_path.stat().st_size < 500:
                raise RuntimeError("Generated audio was empty")
            temp_path.replace(path)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.35 * (attempt + 1))
    if temp_path.exists():
        temp_path.unlink(missing_ok=True)
    raise last_error or RuntimeError("Voice generation failed")


def make_announcement(number, size=5):
    letter = bingo_letter(number, size)
    joke = BILINGUAL_LINES.get(number) or random.choice([
        "Check your card—baka ito na ang hinihintay mo!",
        "Mark it now—huwag puro chika!",
        "Stay awake, mga ka-bingo—baka panalo ka na!",
        "Look at your card, mga kaibigan!",
        "No cheating, just fun—walang daya, puro saya!",
    ])
    return f"{letter}. {ENGLISH_NUMBER_WORDS[number]}. {joke}"


def clean_name(name):
    return " ".join((name or "").strip().split())[:24]


def get_room_and_player():
    code, player_id = session.get("room_code"), session.get("player_id")
    room = ROOMS.get(code) if code else None
    return (room, room["players"].get(player_id)) if room and player_id else (None, None)


def completed_patterns(card, marked, called):
    valid = set(called)
    size = len(card)
    grid = [[cell["free"] or (cell["value"] in marked and cell["value"] in valid) for cell in row] for row in card]
    rows = [i for i in range(size) if all(grid[i])]
    columns = [i for i in range(size) if all(grid[r][i] for r in range(size))]
    diagonals = []
    if all(grid[i][i] for i in range(size)):
        diagonals.append("main")
    if all(grid[i][size - 1 - i] for i in range(size)):
        diagonals.append("reverse")
    return {
        "row": rows,
        "column": columns,
        "diagonal": diagonals,
        "all_out": [True] if all(all(row) for row in grid) else [],
    }


def prize_public(room):
    result = {}
    for key, prize in room["prizes"].items():
        result[key] = {
            "label": PRIZE_LABELS[key],
            "status": prize["status"],
            "winners": [room["players"][pid]["name"] for pid in prize["winners"] if pid in room["players"]],
        }
    return result


def player_has_number(player, number):
    return any((not cell["free"] and cell["value"] == number) for row in player["card"] for cell in row)


def readiness_state(room):
    number = room["called_numbers"][-1] if room["called_numbers"] else None
    players = []
    required = 0
    ready = 0
    if number is not None:
        for pid, player in room["players"].items():
            has_number = player_has_number(player, number)
            if not has_number:
                status = "not_on_card"
            elif number in player["marked"]:
                status = "marked"
                required += 1
                ready += 1
            else:
                status = "waiting"
                required += 1
            players.append({"id": pid, "name": player["name"], "status": status, "is_host": pid == room["host_id"]})
    all_ready = number is None or ready >= required
    deadline = float(room.get("call_deadline") or 0)
    remaining = max(0, int(deadline - time.time() + 0.999)) if deadline else 0
    timed_out = bool(deadline and time.time() >= deadline)
    can_advance = number is None or all_ready or timed_out
    return {
        "number": number,
        "players": players,
        "required": required,
        "ready": ready,
        "all_ready": all_ready,
        "deadline_remaining": remaining,
        "timed_out": timed_out,
        "can_advance": can_advance,
    }


def set_current_call_window(room):
    room["call_started_at"] = time.time()
    room["call_deadline"] = time.time() + MAX_READY_WAIT
    room["ready_since"] = None
    room["deadline_notified"] = False


def perform_call_locked(room):
    closed = close_pending_prizes(room)
    if all(room["prizes"][key]["status"] == "closed" for key in PRIZE_KEYS):
        room["status"] = "finished"
        room["message"] = "All four prizes are frozen. The round is complete."
        room["announcement"] = "Game complete! Congratulations to all our winners!"
        room["announcement_id"] = uuid.uuid4().hex
        room["announcement_kind"] = "system"
        bump(room)
        return False
    if not room["available_numbers"]:
        room["status"] = "finished"
        room["message"] = "All available card numbers have been called. The round is complete."
        room["announcement"] = "All numbers called! Congratulations, mga ka-bingo!"
        room["announcement_id"] = uuid.uuid4().hex
        room["announcement_kind"] = "system"
        bump(room)
        return False
    number = random.choice(room["available_numbers"])
    room["available_numbers"].remove(number)
    room["called_numbers"].append(number)
    room["message"] = (f"{', '.join(closed)} prize frozen. " if closed else "") + f"Number {number} was called. Waiting only for players who have it."
    room["announcement"] = make_announcement(number, room["card_size"])
    room["announcement_id"] = uuid.uuid4().hex
    room["announcement_kind"] = "number"
    set_current_call_window(room)
    prewarm_audio(room["announcement"])
    bump(room)
    return True


def auto_caller_loop():
    while True:
        time.sleep(0.5)
        with LOCK:
            now = time.time()
            for room in list(ROOMS.values()):
                if room.get("status") != "playing":
                    continue
                ready_snapshot = readiness_state(room)
                if room.get("call_mode") != "auto":
                    if ready_snapshot["timed_out"] and not room.get("deadline_notified"):
                        room["deadline_notified"] = True
                        bump(room)
                    continue
                if not room["called_numbers"]:
                    if now >= room.get("next_auto_call_at", now + 999):
                        perform_call_locked(room)
                    continue
                ready = readiness_state(room)
                if ready["all_ready"]:
                    if room.get("ready_since") is None:
                        room["ready_since"] = now
                        bump(room)
                    due = room["ready_since"] + room.get("auto_interval", 5)
                elif ready["timed_out"]:
                    due = now
                else:
                    room["ready_since"] = None
                    continue
                if now >= due:
                    perform_call_locked(room)


threading.Thread(target=auto_caller_loop, daemon=True, name="bingo-auto-caller").start()


def public_state(room, player_id):
    player = room["players"].get(player_id)
    return {
        "version": room["version"],
        "player_version": player.get("version", 1) if player else 0,
        "code": room["code"], "status": room["status"], "card_size": room["card_size"],
        "total_numbers": TOTAL_NUMBERS[room["card_size"]],
        "card_headers": [item[0] for item in CARD_LAYOUTS[room["card_size"]]],
        "min_players": room["min_players"], "max_players": room["max_players"],
        "player_count": len(room["players"]),
        "players": [{"id": pid, "name": p["name"], "is_host": pid == room["host_id"],
                     "wins": [k for k, prize in room["prizes"].items() if pid in prize["winners"]]}
                    for pid, p in room["players"].items()],
        "is_host": player_id == room["host_id"], "player_id": player_id,
        "player_name": player["name"] if player else "", "card": player["card"] if player else None,
        "marked_numbers": sorted(player["marked"]) if player else [],
        "called_numbers": room["called_numbers"],
        "last_number": room["called_numbers"][-1] if room["called_numbers"] else None,
        "remaining": len(room["available_numbers"]),
        "prizes": prize_public(room),
        "message": room.get("message", ""), "announcement": room.get("announcement", ""),
        "announcement_id": room.get("announcement_id", ""),
        "announcement_kind": room.get("announcement_kind", "system"),
        "audio_url": f"/api/audio/{room.get('announcement_id')}" if room.get("announcement_id") else "",
        "call_mode": room.get("call_mode", "manual"),
        "auto_interval": room.get("auto_interval", 5),
        "readiness": readiness_state(room),
    }


def reset_prizes(room):
    room["prizes"] = {k: {"status": "open", "winners": [], "call_index": None} for k in PRIZE_KEYS}


def close_pending_prizes(room):
    closed = []
    for key, prize in room["prizes"].items():
        if prize["status"] == "pending":
            prize["status"] = "closed"
            closed.append(PRIZE_LABELS[key])
    return closed

def audio_path_for_text(text):
    audio_key = hashlib.sha256(f"{VOICE}|{text}".encode("utf-8")).hexdigest()[:24]
    return AUDIO_DIR / f"{audio_key}.mp3"


def prewarm_audio(text):
    """Generate the next call in the background before phones request it."""
    path = audio_path_for_text(text)
    if path.exists():
        return
    def worker():
        try:
            with AUDIO_LOCK:
                if not path.exists():
                    generate_voice_file(text, path, rate="-4%", pitch="-2Hz")
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/create")
def create_room():
    data = request.get_json(silent=True) or {}
    name = clean_name(data.get("name"))
    try: max_players = int(data.get("max_players", MAX_PLAYERS))
    except (TypeError, ValueError): max_players = MAX_PLAYERS
    try: card_size = int(data.get("card_size", 5))
    except (TypeError, ValueError): card_size = 5
    if not name: return jsonify(error="Enter your name."), 400
    if not MIN_PLAYERS <= max_players <= MAX_PLAYERS: return jsonify(error="Players must be between 2 and 20."), 400
    if card_size not in CARD_SIZES: return jsonify(error="Card size must be 3x3, 4x4, or 5x5."), 400
    call_mode = str(data.get("call_mode", "manual")).lower()
    if call_mode not in {"manual", "auto"}: call_mode = "manual"
    try: auto_interval = int(data.get("auto_interval", 5))
    except (TypeError, ValueError): auto_interval = 5
    if auto_interval not in AUTO_INTERVALS: auto_interval = 5
    with LOCK:
        code, player_id = room_code(), uuid.uuid4().hex
        room = {"code": code, "host_id": player_id, "status": "lobby", "min_players": MIN_PLAYERS,
                "max_players": max_players, "card_size": card_size, "players": {}, "called_numbers": [],
                "available_numbers": card_number_pool(card_size), "created_at": time.time(), "version": 1,
                "message": "Waiting for players to join…", "announcement": "", "announcement_id": "", "announcement_kind": "system",
                "call_mode": call_mode, "auto_interval": auto_interval, "call_started_at": 0, "call_deadline": 0,
                "ready_since": None, "next_auto_call_at": 0}
        reset_prizes(room)
        room["players"][player_id] = {"name": name, "card": unique_card(room), "marked": set(), "version": 1}
        ROOMS[code] = room
        session["room_code"], session["player_id"] = code, player_id
        return jsonify(ok=True, state=public_state(room, player_id))


@app.post("/api/join")
def join_room():
    data = request.get_json(silent=True) or {}
    name, code = clean_name(data.get("name")), (data.get("code") or "").strip().upper()
    if not name: return jsonify(error="Enter your name."), 400
    with LOCK:
        room = ROOMS.get(code)
        if not room: return jsonify(error="Room not found."), 404
        if room["status"] != "lobby": return jsonify(error="This game has already started."), 409
        if len(room["players"]) >= room["max_players"]: return jsonify(error="This room is full."), 409
        player_id = uuid.uuid4().hex
        room["players"][player_id] = {"name": name, "card": unique_card(room), "marked": set(), "version": 1}
        room["message"] = f"{name} joined the room."
        bump(room)
        session["room_code"], session["player_id"] = code, player_id
        return jsonify(ok=True, state=public_state(room, player_id))


@app.get("/api/wait")
def wait_for_change():
    with ROOM_CHANGED:
        room, player = get_room_and_player()
        if not room or not player:
            return jsonify(error="Not connected to a room."), 404
        known = request.args.get("version", default=0, type=int)
        deadline = time.time() + 25
        while room.get("version", 0) == known and time.time() < deadline:
            ROOM_CHANGED.wait(timeout=max(0.1, deadline - time.time()))
            room, player = get_room_and_player()
            if not room or not player:
                return jsonify(error="Not connected to a room."), 404
        if room.get("version", 0) == known:
            return jsonify(ok=True, unchanged=True, version=known)
        return jsonify(ok=True, unchanged=False, state=public_state(room, session["player_id"]))


@app.get("/api/state")
def state():
    with LOCK:
        room, player = get_room_and_player()
        if not room or not player: return jsonify(error="Not connected to a room."), 404
        known = request.args.get("version", type=int)
        known_player = request.args.get("player_version", type=int)
        current_player_version = player.get("version", 1)
        if known == room["version"] and known_player == current_player_version:
            return jsonify(ok=True, unchanged=True, version=known, player_version=known_player)
        return jsonify(ok=True, unchanged=False, state=public_state(room, session["player_id"]))


@app.post("/api/start")
def start_game():
    with LOCK:
        room, player = get_room_and_player()
        if not room or not player: return jsonify(error="Room not found."), 404
        if session["player_id"] != room["host_id"]: return jsonify(error="Only the host can start the game."), 403
        if len(room["players"]) < room["min_players"]: return jsonify(error="At least 2 players are required."), 409
        if room["status"] != "lobby": return jsonify(error="The game has already started."), 409
        room["status"], room["called_numbers"], room["available_numbers"] = "playing", [], card_number_pool(room["card_size"])
        room["call_started_at"], room["call_deadline"], room["ready_since"] = 0, 0, None
        room["next_auto_call_at"] = time.time() + 2
        reset_prizes(room)
        for p in room["players"].values():
            p["marked"] = set()
            p["version"] = p.get("version", 1) + 1
        room["message"], room["announcement"] = ("Game started. Auto caller begins shortly!" if room.get("call_mode") == "auto" else "Game started. Host, call the first number!"), "Game started! Good luck, mga ka-bingo!"
        room["announcement_id"] = uuid.uuid4().hex
        room["announcement_kind"] = "system"
        bump(room)
        return jsonify(ok=True, state=public_state(room, session["player_id"]))


@app.post("/api/call")
def call_number():
    with LOCK:
        room, player = get_room_and_player()
        if not room or not player: return jsonify(error="Room not found."), 404
        if session["player_id"] != room["host_id"]: return jsonify(error="Only the host can call numbers."), 403
        if room["status"] != "playing": return jsonify(error="Start the game first."), 409
        if room.get("call_mode") == "auto": return jsonify(error="Auto caller is active for this room."), 409
        ready = readiness_state(room)
        if room["called_numbers"] and not ready["can_advance"]:
            waiting = [p["name"] for p in ready["players"] if p["status"] == "waiting"]
            return jsonify(error=f"Waiting for {', '.join(waiting)} to mark {ready['number']} ({ready['deadline_remaining']}s maximum)."), 409
        perform_call_locked(room)
        return jsonify(ok=True, finished=room["status"] == "finished", state=public_state(room, session["player_id"]))


@app.post("/api/mark")
def mark_number():
    data = request.get_json(silent=True) or {}
    try: value = int(data.get("number"))
    except (TypeError, ValueError): return jsonify(error="Invalid number."), 400
    with LOCK:
        room, player = get_room_and_player()
        if not room or not player: return jsonify(error="Room not found."), 404
        if room["status"] != "playing": return jsonify(error="The game is not active."), 409
        card_numbers = {cell["value"] for row in player["card"] for cell in row if not cell["free"]}
        if value not in card_numbers: return jsonify(error="That number is not on your card."), 400
        if value not in room["called_numbers"]: return jsonify(error="That number has not been called yet."), 409
        if value in player["marked"]:
            return jsonify(ok=True, already_marked=True, marked_numbers=sorted(player["marked"]), version=room["version"], player_version=player.get("version", 1))
        player["marked"].add(value)
        player["version"] = player.get("version", 1) + 1
        if room["called_numbers"] and value == room["called_numbers"][-1]:
            bump(room)
        return jsonify(ok=True, marked=True, marked_numbers=sorted(player["marked"]), version=room["version"], player_version=player["version"], state=public_state(room, session["player_id"]))


@app.post("/api/mark-all")
def mark_all_called_numbers():
    with LOCK:
        room, player = get_room_and_player()
        if not room or not player:
            return jsonify(error="Room not found."), 404
        if room["status"] != "playing":
            return jsonify(error="The game is not active."), 409
        called = set(room["called_numbers"])
        card_numbers = {
            cell["value"]
            for row in player["card"]
            for cell in row
            if not cell["free"] and cell["value"] in called
        }
        before = len(player["marked"])
        player["marked"].update(card_numbers)
        added_count = len(player["marked"]) - before
        if added_count:
            player["version"] = player.get("version", 1) + 1
            latest = room["called_numbers"][-1] if room["called_numbers"] else None
            if latest in card_numbers:
                bump(room)
        return jsonify(
            ok=True,
            added_count=added_count,
            state=public_state(room, session["player_id"]),
        )


@app.post("/api/bingo")
def claim_bingo():
    with LOCK:
        room, player = get_room_and_player()
        if not room or not player: return jsonify(error="Room not found."), 404
        if room["status"] != "playing": return jsonify(error="The game is not active."), 409
        pid = session["player_id"]
        completed = completed_patterns(player["card"], player["marked"], room["called_numbers"])
        won_now, already = [], []
        call_index = len(room["called_numbers"])
        for key in PRIZE_KEYS:
            prize = room["prizes"][key]
            if not completed[key]:
                continue
            if pid in prize["winners"]:
                already.append(PRIZE_LABELS[key]); continue
            if prize["status"] == "closed":
                continue
            if prize["status"] == "pending" and prize["call_index"] != call_index:
                continue
            prize["status"] = "pending"
            prize["call_index"] = call_index
            prize["winners"].append(pid)
            won_now.append(PRIZE_LABELS[key])
        if not won_now:
            closed_valid = [PRIZE_LABELS[k] for k in PRIZE_KEYS if completed[k] and room["prizes"][k]["status"] == "closed"]
            if closed_valid: return jsonify(error=f"{', '.join(closed_valid)} prize is already frozen."), 409
            if already: return jsonify(error=f"You already won: {', '.join(already)}."), 409
            return jsonify(error="No new completed row, column, diagonal, or full card yet."), 409
        labels = " and ".join(won_now)
        room["message"] = f"{player['name']} wins {labels}! Other matching winners may claim before the next call."
        room["announcement"] = f"Bingo! Congratulations, {player['name']}! {labels} winner! Panalo!"
        room["announcement_id"] = uuid.uuid4().hex
        room["announcement_kind"] = "winner"
        bump(room)
        return jsonify(ok=True, won=won_now, state=public_state(room, pid))


@app.get("/api/audio-test")
def audio_test():
    text = "Announcer ready. Hello mga ka-bingo! English and Tagalog voice is working."
    path = AUDIO_DIR / "voice_test_v6.mp3"
    if not path.exists():
        with AUDIO_LOCK:
            if not path.exists():
                try:
                    generate_voice_file(text, path, rate="-6%", pitch="-4Hz")
                except Exception:
                    return jsonify(error="Natural voice is temporarily unavailable."), 503
    return send_file(path, mimetype="audio/mpeg", conditional=True, max_age=86400)


@app.get("/api/audio/<announcement_id>")
def announcement_audio(announcement_id):
    with LOCK:
        room, player = get_room_and_player()
        if not room or not player or room.get("announcement_id") != announcement_id:
            return jsonify(error="Audio call expired."), 404
        text = room.get("announcement", "")
    path = audio_path_for_text(text)
    if not path.exists():
        with AUDIO_LOCK:
            if not path.exists():
                try:
                    generate_voice_file(text, path, rate="-4%", pitch="-2Hz")
                except Exception:
                    return jsonify(error="Natural voice is temporarily unavailable."), 503
    return send_file(path, mimetype="audio/mpeg", conditional=True, max_age=3600)


@app.post("/api/restart")
def restart_game():
    with LOCK:
        room, player = get_room_and_player()
        if not room or not player: return jsonify(error="Room not found."), 404
        if session["player_id"] != room["host_id"]: return jsonify(error="Only the host can restart."), 403
        room["status"], room["called_numbers"], room["available_numbers"] = "lobby", [], card_number_pool(room["card_size"])
        room["call_started_at"], room["call_deadline"], room["ready_since"], room["next_auto_call_at"] = 0, 0, None, 0
        reset_prizes(room)
        existing = set()
        for p in room["players"].values():
            for _ in range(300):
                card = make_card(room["card_size"])
                if card_key(card) not in existing: break
            existing.add(card_key(card)); p["card"], p["marked"] = card, set(); p["version"] = p.get("version", 1) + 1
        room["message"], room["announcement"] = "New round ready. Waiting for the host.", "New round! Ready na ulit, mga ka-bingo!"
        room["announcement_id"] = uuid.uuid4().hex
        room["announcement_kind"] = "system"
        bump(room)
        return jsonify(ok=True, state=public_state(room, session["player_id"]))


@app.post("/api/leave")
def leave_room():
    with LOCK:
        room, player = get_room_and_player()
        if room and player:
            pid = session.get("player_id"); room["players"].pop(pid, None)
            for prize in room["prizes"].values():
                if pid in prize["winners"]: prize["winners"].remove(pid)
            if not room["players"]: ROOMS.pop(room["code"], None)
            else:
                if pid == room["host_id"]: room["host_id"] = next(iter(room["players"]))
                bump(room)
        session.clear()
        return jsonify(ok=True)


@app.get("/health")
def health(): return jsonify(status="ok")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True, threaded=True)
