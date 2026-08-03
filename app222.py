import asyncio
import hashlib
import os
import random
import string
import threading
import time
import uuid
from pathlib import Path

from openai import OpenAI

from dotenv import load_dotenv





try:
    import edge_tts
except ImportError:  # Allows core game tests before optional voice dependency is installed.
    edge_tts = None
from flask import Flask, jsonify, render_template, request, send_file, make_response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

app = Flask(__name__)
load_dotenv()
api_key = os.environ.get("OPENAI_API_KEY")
app.config['JSON_SORT_KEYS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-only-change-this-secret-key')
AUDIO_SIGNER = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='lucky-bingo-audio-v1')

ROOMS = {}
TOKENS = {}  # reconnect_token -> (room_code, player_id)
LOCK = threading.RLock()
CHANGED = threading.Condition(LOCK)
AUDIO_LOCK = threading.RLock()
ANNOUNCEMENTS = {}  # announcement_id -> {'text': str, 'created_at': float}
MAX_ANNOUNCEMENTS = 1000

MIN_PLAYERS = 2
MAX_PLAYERS = 20
CARD_LAYOUTS = {
    3: [('B', 1, 5), ('N', 6, 10), ('O', 11, 15)],
    4: [('B', 1, 9), ('I', 10, 18), ('G', 19, 27), ('O', 28, 35)],
    5: [('B', 1, 15), ('I', 16, 30), ('N', 31, 45), ('G', 46, 60), ('O', 61, 75)],
}
TOTALS = {3: 15, 4: 35, 5: 75}
PRIZE_KEYS = ('row', 'column', 'diagonal', 'all_out')
PRIZE_LABELS = {'row': 'Row', 'column': 'Column', 'diagonal': 'Diagonal', 'all_out': 'All Out'}
DEFAULT_PRIZES = {'row': 10.0, 'column': 10.0, 'diagonal': 10.0, 'all_out': 30.0}
AUTO_INTERVALS = (3, 5, 8, 10, 15)
MAX_READY_WAIT = 25
PRIMARY_VOICE = os.getenv('BINGO_VOICE', 'fil-PH-AngeloNeural')
FALLBACK_VOICE = os.getenv('BINGO_FALLBACK_VOICE', 'en-PH-JamesNeural')
VOICE_CANDIDATES = tuple(dict.fromkeys((PRIMARY_VOICE, FALLBACK_VOICE)))
AUDIO_DIR = Path(os.getenv('BINGO_AUDIO_DIR', '/tmp/lucky_bingo_audio'))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

NUMBER_WORDS = {
    1:'one',2:'two',3:'three',4:'four',5:'five',6:'six',7:'seven',8:'eight',9:'nine',10:'ten',
    11:'eleven',12:'twelve',13:'thirteen',14:'fourteen',15:'fifteen',16:'sixteen',17:'seventeen',18:'eighteen',19:'nineteen',20:'twenty',
    21:'twenty-one',22:'twenty-two',23:'twenty-three',24:'twenty-four',25:'twenty-five',26:'twenty-six',27:'twenty-seven',28:'twenty-eight',29:'twenty-nine',30:'thirty',
    31:'thirty-one',32:'thirty-two',33:'thirty-three',34:'thirty-four',35:'thirty-five',36:'thirty-six',37:'thirty-seven',38:'thirty-eight',39:'thirty-nine',40:'forty',
    41:'forty-one',42:'forty-two',43:'forty-three',44:'forty-four',45:'forty-five',46:'forty-six',47:'forty-seven',48:'forty-eight',49:'forty-nine',50:'fifty',
    51:'fifty-one',52:'fifty-two',53:'fifty-three',54:'fifty-four',55:'fifty-five',56:'fifty-six',57:'fifty-seven',58:'fifty-eight',59:'fifty-nine',60:'sixty',
    61:'sixty-one',62:'sixty-two',63:'sixty-three',64:'sixty-four',65:'sixty-five',66:'sixty-six',67:'sixty-seven',68:'sixty-eight',69:'sixty-nine',70:'seventy',
    71:'seventy-one',72:'seventy-two',73:'seventy-three',74:'seventy-four',75:'seventy-five'
}
COMMENTS = [
    "Come on everyone! Tingnan ang card mo, baka ito na!",
    "Check your card! Huwag puro chika, markahan mo na!",
    "Easy lang! Hindi pa tapos ang game, may pag-asa pa!",
    "Good luck everyone! Sana ikaw na ang next winner!",
    "Focus please! Huwag sa katabi, sa card mo lang!",
    "Uy ayieee! Mukhang may kinikilig... baka bingo na!",
    "Wake up guys! Hoy, gising! Hindi ito sleeping competition!",
    "Don't panic! Kalma lang, huminga ka muna!",
    "One more number! Baka ito na ang hinihintay mo!",
    "Who's feeling lucky? Sana hindi drawing ang swerte mo!",
    "Keep smiling! Baka lucky face ang kailangan mo!",
    "Come on kuya! Huwag ka muna sumuko!",
    "Ate, relax lang! Hindi kita kinakalimutan!",
    "No cheating ha! Tingin sa sariling card only!",
    "Let's go! Wag kang sumilip sa kapitbahay!",
    "Uy, sino ang kinakabahan? Kita sa mukha ah!",
    "Don't blink! Isang number lang, pwede magbago ang lahat!",
    "Good vibes only! Walang iyakan mamaya ha!",
    "Hoy! Mark it now! Huwag puro reklamo!",
    "Relax! Hindi pa final round!",
    "Ay naku! Mukhang may gustong manuntok kapag malas!",
    "Smile first! Baka smile lang ang kulang para manalo!",
    "Come on everybody! Palakpakan naman natin ang sarili!",
    "Hoy kuya! Hindi ka nanonood ng sine, bingo 'to!",
    "Wake up ate! Baka lampasan ka ng swerte!",
    "Easy easy! Hindi ito race, pero ang prize naghihintay!",
    "Good luck! Baka ngayong gabi ikaw ang bida!",
    "Focus lang! Wag muna isipin ang ex mo!",
    "Don't cry! Marami pang numbers ang darating!",
    "Keep believing! Darating din ang lucky number mo!",
    "Ayieee! Mukhang may fake smile diyan!",
    "Come on! Wag mong takutin ang card mo!",
    "Who's ready to shout BINGO? Practice lang muna!",
    "Easy lang boss! Hindi kita favorite, random lang talaga!",
    "Hoy! Wag kang magalit sa host, number lang hawak ko!",
    "Let's make some noise! Buhay pa ba kayo diyan?",
    "Relax everyone! Baka ang swerte nahihiya lang!",
    "Check again! Baka nalagpasan mo ang number!",
    "Come on! Konting tiis na lang, malapit na!",
    "Good luck mga ka-bingo! Let's make this exciting!"
]
SPECIAL = {
    1: "Number one! Ikaw ba 'yan o number one lang sa seen zone?",
    2: "Number two! Dalawa na lang... parang ex mo at bago niya!",
    3: "Number three! Tatlo na, pero love life mo zero pa rin!",
    4: "Number four! Four sure may sisigaw ng bingo mamaya!",
    5: "Number five! High five! Kung wala kang jowa, card mo na lang hawakan!",
    6: "Number six! Six pack wala? Six numbers meron!",
    7: "Lucky seven! Aba'y swerte! Baka pati crush mo mag-chat mamaya!",
    8: "Number eight! Walo na! Wag puro tingin sa katabi, cheating 'yan!",
    9: "Number nine! Ayiee... kinakabahan na si ate!",
    10: "Perfect ten! Mas perfect pa kaysa sa filter mo!",
    11: "Double one! Dalawang mata sa card, hindi sa kapitbahay!",
    12: "Twelve! Hoy kuya, gising! Hindi Netflix 'to!",
    13: "Thirteen! Hindi malas! Malas lang sa ex mo!",
    14: "Fourteen! Baka fourteen years ka nang naghihintay mag-bingo!",
    15: "Sweet fifteen! Feeling debut ulit!",
    16: "Sixteen! Smile ka naman, parang iniwan ka!",
    17: "Seventeen! Sana all may lucky number!",
    18: "Eighteen! Adults only ang excitement!",
    19: "Nineteen! Relax lang, hindi ito job interview!",
    20: "Twenty! Ang bilis... parang sweldo na ubos agad!",

    21: "Twenty-one! Blackjack vibes! Pero bingo ang jackpot!",
    22: "Two little ducks! Quack quack! Sino ang mukhang pato ngayon?",
    23: "Twenty-three! Huwag kang mag-practice sumigaw ng bingo kung wala pa!",
    24: "Twenty-four! Twenty-four hours ka bang naka-online?",
    25: "Twenty-five! Quarter life crisis? Bingo muna!",
    26: "Twenty-six! Wag kang sumilip, CCTV si Lord!",
    27: "Twenty-seven! Mukhang umiinit ang card mo ah!",
    28: "Twenty-eight! Huminga ka muna, hindi ka manganganak!",
    29: "Twenty-nine! Isang number na lang? Wag mong usugin!",
    30: "Thirty! Thirty pero feeling baby pa rin!",

    31: "Thirty-one! Wag ka muna umiyak!",
    32: "Thirty-two! Smile! Baka camera ready ang panalo!",
    33: "Thirty-three! Triple vibes! Triple excitement!",
    34: "Thirty-four! Hoy, markahan mo muna bago mag-selfie!",
    35: "Thirty-five! Sino ang nagpapanggap na chill?",
    36: "Thirty-six! Mukhang may tataas ang blood pressure!",
    37: "Thirty-seven! Baka destiny na 'to!",
    38: "Thirty-eight! Wag mong kausapin ang number, di ka sasagutin!",
    39: "Thirty-nine! Konti na lang! Wag mawalan ng pag-asa!",
    40: "Forty! Life begins at forty... pati kaba!",

    41: "Forty-one! Huwag puro screenshot, markahan mo!",
    42: "Forty-two! The answer daw... sana bingo din!",
    43: "Forty-three! Wag ka muna mag-celebrate!",
    44: "Double four! Double kilig kung manalo ka!",
    45: "Forty-five! Baka may umiiyak na sa loob!",
    46: "Forty-six! Wag kang humawak sa swerte ng iba!",
    47: "Forty-seven! Focus! Hindi ka nasa date!",
    48: "Forty-eight! Huwag puro heart react!",
    49: "Forty-nine! Lapit na! Kumalma ka!",
    50: "Fifty! Halfway! Hindi pa tapos ang laban!",

    51: "Fifty-one! Baka ikaw na ang bida!",
    52: "Fifty-two! Kumusta ang blood pressure?",
    53: "Fifty-three! Wag mong takutin ang card!",
    54: "Fifty-four! Mukhang may gusto nang sumigaw!",
    55: "Double five! High five muna!",
    56: "Fifty-six! Wag kang magdasal ng malakas, rinig ng kalaban!",
    57: "Fifty-seven! Smile! Libre lang!",
    58: "Fifty-eight! Hoy, buhay pa ba kayo?",
    59: "Fifty-nine! Last few numbers na!",
    60: "Sixty! Senior vibes pero fighter pa rin!",

    61: "Sixty-one! Konting tiis na lang!",
    62: "Sixty-two! Wag mong titigan, hindi lalabas ulit!",
    63: "Sixty-three! Mukhang may nanlalamig na!",
    64: "Sixty-four! Ayiee... exciting!",
    65: "Sixty-five! Sino ang ready mag-bingo?",
    66: "Double six! Double swerte sana!",
    67: "Sixty-seven! Wag kang kabahan, baka mahalata!",
    68: "Sixty-eight! Huwag kang ma-pressure!",
    69: "Sixty-nine! Nice! Hoy, alam ko'ng ngiti na 'yan! Focus muna sa bingo!",
    70: "Seventy! Sampu na lang! Kaya pa!",

    71: "Seventy-one! Wag kang aalis, malapit na!",
    72: "Seventy-two! Mukhang may winner na!",
    73: "Seventy-three! Last stretch!",
    74: "Seventy-four! Ready na ba sumigaw?",
    75: "Seventy-five! Last ball! Kung wala pa ring bingo... gcash na lang ang kulang!"
}

OPENAI_TTS_MODEL = "gpt-4o-mini-tts"

# Recommended by OpenAI for higher voice quality.
OPENAI_TTS_VOICES = [
    "cedar",
    "marin",
    "onyx",
]


BINGO_VOICE_INSTRUCTIONS = """
Speak like a real Filipino male bingo host talking live to close friends.

Voice and personality:
- Filipino male, approximately 30 to 40 years old.
- Natural Filipino English accent.
- Warm, confident, playful, slightly naughty, and very funny.
- Sound spontaneous, not like a narrator reading written text.
- Speak as though people are reacting live in front of you.
- Mix English and Tagalog naturally.
- Approximately 40 percent English and 60 percent Tagalog.
- Smile while speaking so the smile can be heard in the voice.
- Add realistic emotion, changing intonation, and conversational rhythm.
- Use natural pauses, small breaths, and occasional soft chuckles.
- Expressions such as "ayieee", "naku", "hala", and "haha" should sound natural.
- Tease the players playfully, but never sound insulting or aggressive.
- Clearly emphasize the bingo letter and called number.
- Build a little excitement before the joke.
- Do not use a formal radio-announcer tone.
- Do not sound robotic, overly polished, or like an audiobook.
- Do not rush the number.
- Keep the performance energetic without shouting.

Delivery example:
Start clearly with the letter and number, pause briefly, then deliver the
comment like a live joke directed at the players. Slightly laugh when the
sentence is genuinely funny, rather than laughing after every line.
"""

def now(): return time.time()

def clean_name(value): return ' '.join(str(value or '').strip().split())[:24]

def clean_money(value, default):
    try:
        amount = round(float(value), 2)
        return amount if 0 <= amount <= 1_000_000 else default
    except (TypeError, ValueError):
        return default


def room_code():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        if code not in ROOMS: return code


def bump(room):
    room['version'] += 1
    CHANGED.notify_all()


def letter_for(number, size):
    for letter, lo, hi in CARD_LAYOUTS[size]:
        if lo <= number <= hi: return letter
    return '?'


def pool(size): return list(range(1, TOTALS[size] + 1))


def make_card(size):
    cols = [random.sample(range(lo, hi + 1), size) for _, lo, hi in CARD_LAYOUTS[size]]
    card = [[{'value': cols[c][r], 'free': False} for c in range(size)] for r in range(size)]
    if size % 2:
        mid = size // 2
        card[mid][mid] = {'value': 'FREE', 'free': True}
    return card


def card_key(card): return tuple(cell['value'] for row in card for cell in row if not cell['free'])


def unique_card(room):
    existing = {card_key(p['card']) for p in room['players'].values()}
    for _ in range(1000):
        card = make_card(room['card_size'])
        if card_key(card) not in existing: return card
    return make_card(room['card_size'])


def has_number(player, number):
    return any(not c['free'] and c['value'] == number for row in player['card'] for c in row)


def patterns(player, called):
    card, marked = player['card'], player['marked']
    valid = set(called)
    n = len(card)
    grid = [[c['free'] or (c['value'] in marked and c['value'] in valid) for c in row] for row in card]
    return {
        'row': [i for i in range(n) if all(grid[i])],
        'column': [i for i in range(n) if all(grid[r][i] for r in range(n))],
        'diagonal': [d for d, ok in (
            ('main', all(grid[i][i] for i in range(n))),
            ('reverse', all(grid[i][n-1-i] for i in range(n)))
        ) if ok],
        'all_out': [True] if all(all(row) for row in grid) else []
    }


def announcement(number, size):
    return f"{letter_for(number,size)}. {NUMBER_WORDS[number]}. {SPECIAL.get(number, random.choice(COMMENTS))}"


def audio_path(text, voice):
    key = hashlib.sha256(f'{voice}|{text}'.encode()).hexdigest()[:32]
    return AUDIO_DIR / f'{key}.mp3'


async def _stream_edge_tts(text, voice, tmp_path):
    """Write only audio chunks. This is more reliable than Communicate.save()."""
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate='-4%',
        volume='+0%',
        pitch='-2Hz',
        connect_timeout=12,
        receive_timeout=30,
    )
    audio_bytes = 0
    with tmp_path.open('wb') as file_obj:
        async for chunk in communicate.stream():
            if chunk.get('type') == 'audio':
                data = chunk.get('data') or b''
                file_obj.write(data)
                audio_bytes += len(data)
    if audio_bytes < 500:
        raise RuntimeError(f'No usable audio returned for voice {voice}')


# def generate_audio(text):
#     """
#     Generate a natural server MP3.

#     First choice: Filipino male Angelo.
#     Reliable fallback: English Philippines male James.
#     Both are neural server voices; browser speech synthesis is never used.
#     Speak like a cheerful Filipino male bingo host.

# Voice:
# - Male, around 30-40 years old.
# - Native Filipino accent speaking English naturally.
# - Energetic, playful and funny.
# - Sounds like a live bingo announcer in the Philippines.
# - Smile while speaking.
# - Natural pauses.
# - Slight excitement after every number.
# - Never sound robotic or like reading a script.
# - English with natural Tagalog code-switching.
# - Friendly, warm and conversational.
# - Occasionally laugh softly: "haha", "ayieee", "naku!"
# - Emphasize the called number clearly.
#     """
#     if edge_tts is None:
#         raise RuntimeError('edge-tts is not installed. Run: pip install -r requirements.txt')

#     errors = []
#     for voice in VOICE_CANDIDATES:
#         path = audio_path(text, voice)
#         if path.exists() and path.stat().st_size >= 500:
#             return path, voice

#         tmp = path.with_suffix(f'.{uuid.uuid4().hex}.part.mp3')
#         for attempt in range(3):
#             try:
#                 if tmp.exists():
#                     tmp.unlink()
#                 asyncio.run(_stream_edge_tts(text, voice, tmp))
#                 if not tmp.exists() or tmp.stat().st_size < 500:
#                     raise RuntimeError('Generated audio file is empty')
#                 os.replace(tmp, path)
#                 return path, voice
#             except Exception as exc:
#                 errors.append(f'{voice} attempt {attempt + 1}: {type(exc).__name__}: {exc}')
#                 try:
#                     tmp.unlink(missing_ok=True)
#                 except Exception:
#                     pass
#                 time.sleep(0.6 * (attempt + 1))

#     raise RuntimeError(' | '.join(errors[-6:]) or 'All neural voices failed')



def generate_audio(text):
    """
    Generate an expressive server-side MP3 using prompt-controlled AI speech.

    Returns:
        tuple[Path, str]: Generated MP3 path and selected voice name.
    """
    api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Add it to your environment variables."
        )

    client = OpenAI(api_key=api_key)
    errors = []

    for voice in OPENAI_TTS_VOICES:
        path = audio_path(
            text,
            f"{OPENAI_TTS_MODEL}-{voice}",
        )

        if path.exists() and path.stat().st_size >= 1000:
            return path, voice

        tmp = path.with_suffix(
            f".{uuid.uuid4().hex}.part.mp3"
        )

        for attempt in range(3):
            try:
                tmp.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                tmp.unlink(missing_ok=True)

                with client.audio.speech.with_streaming_response.create(
                    model=OPENAI_TTS_MODEL,
                    voice=voice,
                    input=text,
                    instructions=BINGO_VOICE_INSTRUCTIONS,
                    response_format="mp3",
                ) as response:
                    response.stream_to_file(tmp)

                if not tmp.exists() or tmp.stat().st_size < 1000:
                    raise RuntimeError(
                        "Generated audio file is empty or incomplete."
                    )

                os.replace(tmp, path)
                return path, voice

            except Exception as exc:
                errors.append(
                    f"{voice} attempt {attempt + 1}: "
                    f"{type(exc).__name__}: {exc}"
                )

                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

                time.sleep(0.8 * (attempt + 1))

    raise RuntimeError(
        " | ".join(errors[-6:])
        or "All expressive AI voices failed."
    )

def signed_audio_url(text):
    payload = AUDIO_SIGNER.dumps({'text': text})
    return f'/api/audio/{payload}.mp3'


def prewarm(text):
    """Generate in the background; the audio route can also generate on demand."""
    def run():
        try:
            with AUDIO_LOCK:
                generate_audio(text)
        except Exception:
            app.logger.exception('Background voice prewarm failed')
    threading.Thread(target=run, daemon=True).start()




def register_announcement(room, text, kind='number'):
    """Create a stable audio id that survives later room state changes."""
    announcement_id = uuid.uuid4().hex
    room['announcement'] = text
    room['announcement_id'] = announcement_id
    room['announcement_kind'] = kind
    ANNOUNCEMENTS[announcement_id] = {'text': text, 'created_at': now()}

    # Keep memory bounded while preserving recent calls across all active rooms.
    if len(ANNOUNCEMENTS) > MAX_ANNOUNCEMENTS:
        stale = sorted(ANNOUNCEMENTS.items(), key=lambda item: item[1]['created_at'])
        for old_id, _ in stale[:len(ANNOUNCEMENTS) - MAX_ANNOUNCEMENTS]:
            ANNOUNCEMENTS.pop(old_id, None)

    prewarm(text)
    return announcement_id

def reset_round(room, first=False):
    room['round_number'] = room.get('round_number', 0) + (0 if first else 1)
    room['status'] = 'lobby' if first else 'playing'
    room['called'] = []
    room['available'] = pool(room['card_size'])
    room['call_deadline'] = 0
    room['ready_since'] = None
    room['next_auto_call_at'] = now() + 2 if room['status'] == 'playing' and room['call_mode'] == 'auto' else 0
    room['prizes'] = {
        k: {'status':'open','winners':[],'call_index':None,'amount':room['prize_amounts'][k],'shares':{}}
        for k in PRIZE_KEYS
    }
    for player in room['players'].values():
        player['card'] = unique_card(room)
        player['marked'] = set()
        player['round_claims'] = []
        player['version'] += 1
    room['message'] = 'Waiting for players to join…' if first else f'Round {room["round_number"]} started!'
    room['announcement'] = ''
    room['announcement_id'] = ''
    room['announcement_kind'] = 'system'


def finalize_prize(room, key):
    prize = room['prizes'][key]
    winners = prize['winners']
    if not winners: return
    share = round(prize['amount'] / len(winners), 2)
    # Recalculate so multiple same-call winners split the full pot equally.
    for pid in winners:
        old = prize['shares'].get(pid, 0)
        diff = round(share - old, 2)
        room['players'][pid]['earnings'] = round(room['players'][pid]['earnings'] + diff, 2)
        prize['shares'][pid] = share


def close_pending(room):
    for prize in room['prizes'].values():
        if prize['status'] == 'pending': prize['status'] = 'closed'


def readiness(room):
    latest = room['called'][-1] if room['called'] else None
    rows, required, ready = [], 0, 0
    if latest is not None:
        for pid, p in room['players'].items():
            if not has_number(p, latest): status = 'not_on_card'
            elif latest in p['marked']:
                status = 'marked'; required += 1; ready += 1
            else:
                status = 'waiting'; required += 1
            rows.append({'id':pid,'name':p['name'],'status':status})
    deadline = room.get('call_deadline',0)
    timed_out = bool(deadline and now() >= deadline)
    return {'number':latest,'players':rows,'required':required,'ready':ready,
            'all_ready': latest is None or ready >= required,
            'remaining': max(0, int(deadline-now()+.999)) if deadline else 0,
            'timed_out':timed_out}


def perform_call(room):
    close_pending(room)
    if all(room['prizes'][k]['status'] == 'closed' for k in PRIZE_KEYS):
        room['status'] = 'finished'
        room['message'] = 'All prize categories are complete. Start the next round when ready.'
        bump(room)
        return False
    if not room['available']:
        room['status'] = 'finished'; room['message'] = 'All numbers have been called.'; bump(room); return False
    number = random.choice(room['available'])
    room['available'].remove(number); room['called'].append(number)
    room['call_deadline'] = now() + MAX_READY_WAIT
    room['ready_since'] = None
    text = announcement(number, room['card_size'])
    register_announcement(room, text, 'number')
    room['message'] = f'{letter_for(number,room["card_size"])}-{number} called. Waiting for players who have it.'
    bump(room); return True


def room_state(room, pid):
    p = room['players'][pid]
    prizes = {}
    for k, pr in room['prizes'].items():
        prizes[k] = {'label':PRIZE_LABELS[k],'status':pr['status'],'amount':pr['amount'],
                     'winners':[{'id':w,'name':room['players'][w]['name'],'share':pr['shares'].get(w,0)} for w in pr['winners'] if w in room['players']]}
    leaderboard = sorted([
        {'id':x,'name':q['name'],'earnings':q['earnings'],'wins':q['wins'],'is_host':x==room['host_id']}
        for x,q in room['players'].items()
    ], key=lambda x:(-x['earnings'],-x['wins'],x['name'].lower()))
    return {
        'version':room['version'],'code':room['code'],'status':room['status'],'round_number':room['round_number'],
        'card_size':room['card_size'],'headers':[x[0] for x in CARD_LAYOUTS[room['card_size']]],'total_numbers':TOTALS[room['card_size']],
        'max_players':room['max_players'],'player_id':pid,'is_host':pid==room['host_id'],'player_name':p['name'],
        'card':p['card'],'marked':sorted(p['marked']),'called':room['called'],'last_number':room['called'][-1] if room['called'] else None,
        'remaining_numbers':len(room['available']),'message':room['message'],'announcement':room['announcement'],
        'announcement_id':room['announcement_id'],'audio_url':signed_audio_url(room['announcement']) if room['announcement'] else '',
        'call_mode':room['call_mode'],'auto_interval':room['auto_interval'],'readiness':readiness(room),
        'prizes':prizes,'my_earnings':p['earnings'],'my_wins':p['wins'],'leaderboard':leaderboard,
        'round_history':room['round_history'][-10:],
    }


def resolve_token(token):
    mapping = TOKENS.get(token)
    if not mapping: return None, None, None
    code, pid = mapping
    room = ROOMS.get(code)
    if not room or pid not in room['players']: return None, None, None
    room['players'][pid]['last_seen'] = now()
    return room, room['players'][pid], pid


def require_player():
    token = request.headers.get('X-Reconnect-Token') or request.args.get('token') or (request.get_json(silent=True) or {}).get('token')
    return resolve_token(token)


def advance_auto_room(room, current=None):
    """Advance one automatic room safely.

    This is called both by the server background loop and by a lightweight
    heartbeat from the host browser. The browser heartbeat makes automatic
    calling reliable on Render even after an instance wakes from sleep.
    """
    current = current or now()
    if room['status'] != 'playing' or room['call_mode'] != 'auto':
        return False

    # The first ball must never depend on readiness from a previous call.
    if not room['called']:
        return perform_call(room)

    ready = readiness(room)
    if ready['all_ready']:
        if room['ready_since'] is None:
            room['ready_since'] = current
            bump(room)
            return False
        if current >= room['ready_since'] + room['auto_interval']:
            return perform_call(room)
        return False

    if ready['timed_out']:
        return perform_call(room)

    # Somebody who has the latest number still needs to mark it.
    room['ready_since'] = None
    return False


def auto_loop():
    while True:
        time.sleep(.5)
        with LOCK:
            current = now()
            for room in list(ROOMS.values()):
                try:
                    advance_auto_room(room, current)
                except Exception:
                    app.logger.exception('Automatic caller loop failed for room %s', room.get('code'))


threading.Thread(target=auto_loop, daemon=True).start()

@app.after_request
def no_cache(resp):
    resp.headers['Cache-Control']='no-store, max-age=0'; resp.headers['Pragma']='no-cache'; return resp

@app.get('/')
def index(): return render_template('index.html')

@app.post('/api/create')
def create():
    data=request.get_json(force=True); name=clean_name(data.get('name'))
    if not name: return jsonify(error='Enter your name.'),400
    size=int(data.get('card_size',5)); max_players=int(data.get('max_players',20))
    if size not in CARD_LAYOUTS: return jsonify(error='Invalid card size.'),400
    if not MIN_PLAYERS<=max_players<=MAX_PLAYERS: return jsonify(error='Players must be 2 to 20.'),400
    mode=data.get('call_mode','auto'); mode=mode if mode in ('auto','manual') else 'auto'
    interval=int(data.get('auto_interval',5)); interval=interval if interval in AUTO_INTERVALS else 5
    amounts={k:clean_money(data.get('prizes',{}).get(k),DEFAULT_PRIZES[k]) for k in PRIZE_KEYS}
    with LOCK:
        code=room_code(); pid=uuid.uuid4().hex; token=uuid.uuid4().hex+uuid.uuid4().hex
        room={'code':code,'host_id':pid,'status':'lobby','card_size':size,'max_players':max_players,'players':{},
              'call_mode':mode,'auto_interval':interval,'prize_amounts':amounts,'version':1,'round_number':1,'round_history':[]}
        room['players'][pid]={'name':name,'card':make_card(size),'marked':set(),'earnings':0.0,'wins':0,'round_claims':[],'version':1,'last_seen':now()}
        ROOMS[code]=room; TOKENS[token]=(code,pid); reset_round(room,first=True)
        return jsonify(ok=True,token=token,state=room_state(room,pid))

@app.post('/api/join')
def join():
    data=request.get_json(force=True); name=clean_name(data.get('name')); code=str(data.get('code','')).upper().strip()
    if not name: return jsonify(error='Enter your name.'),400
    with LOCK:
        room=ROOMS.get(code)
        if not room: return jsonify(error='Room not found.'),404
        if len(room['players'])>=room['max_players']: return jsonify(error='Room is full.'),409
        if room['status'] not in ('lobby','finished'): return jsonify(error='Round already in progress.'),409
        pid=uuid.uuid4().hex; token=uuid.uuid4().hex+uuid.uuid4().hex
        room['players'][pid]={'name':name,'card':unique_card(room),'marked':set(),'earnings':0.0,'wins':0,'round_claims':[],'version':1,'last_seen':now()}
        TOKENS[token]=(code,pid); room['message']=f'{name} joined.'; bump(room)
        return jsonify(ok=True,token=token,state=room_state(room,pid))

@app.post('/api/restore')
def restore():
    room,p,pid=require_player()
    if not room: return jsonify(error='Room session expired.'),404
    with LOCK: return jsonify(ok=True,state=room_state(room,pid))

@app.get('/api/wait')
def wait():
    room,p,pid=require_player()
    if not room: return jsonify(error='Room session expired.'),404
    known=request.args.get('version',0,type=int); end=now()+25
    with CHANGED:
        while room['version']==known and now()<end:
            CHANGED.wait(timeout=max(.1,end-now()))
            room,p,pid=resolve_token(request.args.get('token'))
            if not room: return jsonify(error='Room session expired.'),404
        return jsonify(ok=True,unchanged=room['version']==known,state=None if room['version']==known else room_state(room,pid),version=room['version'])

@app.post('/api/start')
def start():
    room,p,pid=require_player()
    if not room: return jsonify(error='Not connected.'),404
    with LOCK:
        if pid!=room['host_id']: return jsonify(error='Host only.'),403
        if len(room['players'])<MIN_PLAYERS: return jsonify(error='At least 2 players are required.'),409
        if room['status']!='lobby': return jsonify(error='Round already started.'),409
        room['status']='playing'
        room['ready_since']=None
        room['message']='Game started. First ball is coming…'
        bump(room)

        # Call the first ball immediately in automatic mode. Previously the
        # first call depended only on a background thread, which could fail to
        # wake reliably on hosted workers.
        if room['call_mode']=='auto':
            perform_call(room)

        return jsonify(ok=True,state=room_state(room,pid))

@app.post('/api/auto-tick')
def auto_tick():
    """Host heartbeat fallback for Render/free-instance scheduling."""
    room,p,pid=require_player()
    if not room:
        return jsonify(error='Not connected.'),404
    with LOCK:
        if pid!=room['host_id']:
            return jsonify(error='Host only.'),403
        changed=advance_auto_room(room)
        return jsonify(ok=True,called=bool(changed),state=room_state(room,pid))


@app.post('/api/call')
def call():
    room,p,pid=require_player()
    if not room:return jsonify(error='Not connected.'),404
    with LOCK:
        if pid!=room['host_id']:return jsonify(error='Host only.'),403
        if room['status']!='playing':return jsonify(error='Game not playing.'),409
        r=readiness(room)
        if room['called'] and not (r['all_ready'] or r['timed_out']):return jsonify(error='Waiting for players who have the latest number.'),409
        perform_call(room); return jsonify(ok=True,state=room_state(room,pid))

@app.post('/api/mark')
def mark():
    room,p,pid=require_player(); data=request.get_json(force=True)
    if not room:return jsonify(error='Not connected.'),404
    try:number=int(data.get('number'))
    except:return jsonify(error='Invalid number.'),400
    with LOCK:
        if number not in room['called']:return jsonify(error='That number has not been called.'),409
        if not has_number(p,number):return jsonify(error='Number is not on your card.'),409
        p['marked'].add(number); p['version']+=1; bump(room)
        return jsonify(ok=True,state=room_state(room,pid))

@app.post('/api/catch-up')
def catch_up():
    room,p,pid=require_player()
    if not room:return jsonify(error='Not connected.'),404
    with LOCK:
        before=len(p['marked'])
        for n in room['called']:
            if has_number(p,n):p['marked'].add(n)
        p['version']+=1; bump(room)
        return jsonify(ok=True,marked=len(p['marked'])-before,state=room_state(room,pid))

@app.post('/api/bingo')
def bingo():
    room,p,pid=require_player()
    if not room:return jsonify(error='Not connected.'),404
    with LOCK:
        found=patterns(p,room['called']); awarded=[]
        for key in PRIZE_KEYS:
            prize=room['prizes'][key]
            if found[key] and prize['status']!='closed' and pid not in prize['winners']:
                # first winner opens a same-call sharing window; later same-call winners can join until next ball.
                if prize['status']=='open': prize['status']='pending'; prize['call_index']=len(room['called'])
                if prize['call_index']==len(room['called']):
                    prize['winners'].append(pid); p['wins']+=1; p['round_claims'].append(key); awarded.append(key); finalize_prize(room,key)
        if not awarded:return jsonify(error='No new valid prize pattern yet.'),409
        names=', '.join(PRIZE_LABELS[k] for k in awarded)
        room['message']=f'{p["name"]} won {names}!'
        register_announcement(room, f'Congratulations, {p["name"]}! You won {names}. Panalo ka!', 'winner')
        bump(room)
        return jsonify(ok=True,awarded=awarded,state=room_state(room,pid))

@app.post('/api/next-round')
def next_round():
    room,p,pid=require_player()
    if not room:return jsonify(error='Not connected.'),404
    with LOCK:
        if pid!=room['host_id']:return jsonify(error='Host only.'),403
        # Save current round summary visible to everyone.
        summary={'round':room['round_number'],'prizes':{}}
        for k,pr in room['prizes'].items():
            summary['prizes'][k]=[{'name':room['players'][w]['name'],'share':pr['shares'].get(w,0)} for w in pr['winners'] if w in room['players']]
        room['round_history'].append(summary)
        reset_round(room,first=False); bump(room)
        return jsonify(ok=True,state=room_state(room,pid))

@app.post('/api/leave')
def leave():
    token=(request.get_json(silent=True) or {}).get('token'); room,p,pid=resolve_token(token)
    if not room:return jsonify(ok=True)
    with LOCK:
        name=p['name']; del room['players'][pid]; TOKENS.pop(token,None)
        if not room['players']:
            ROOMS.pop(room['code'],None)
        else:
            if pid==room['host_id']: room['host_id']=next(iter(room['players']))
            room['message']=f'{name} left the room.'; bump(room)
    return jsonify(ok=True)

@app.get('/api/audio/<path:signed_payload>.mp3')
def audio(signed_payload):
    """
    The text is signed into the URL itself. Audio therefore does not depend on
    ANNOUNCEMENTS, Flask process memory, polling state, or the newest room call.
    """
    try:
        data = AUDIO_SIGNER.loads(signed_payload, max_age=60 * 60 * 24)
        text = str(data.get('text', '')).strip()
        if not text:
            raise BadSignature('Missing announcement text')
    except SignatureExpired:
        return jsonify(error='Voice link expired. Repeat the latest call.'), 410
    except BadSignature:
        return jsonify(error='Invalid voice link.'), 404

    try:
        with AUDIO_LOCK:
            path, used_voice = generate_audio(text)
        response = send_file(path, mimetype='audio/mpeg', conditional=True, max_age=86400)
        response.headers['Cache-Control'] = 'public, max-age=86400, immutable'
        response.headers['X-Bingo-Voice'] = used_voice
        return response
    except Exception as exc:
        app.logger.exception('Neural voice generation failed')
        # Keep the exact technical detail in local/Render logs and expose a short
        # diagnostic so the browser does not misleadingly report a 404.
        return jsonify(
            error='Natural voice generation failed.',
            detail=str(exc)[:600],
            primary_voice=PRIMARY_VOICE,
            fallback_voice=FALLBACK_VOICE,
        ), 503


@app.get('/api/voice-health')
def voice_health():
    return jsonify(
        ok=edge_tts is not None,
        edge_tts_installed=edge_tts is not None,
        primary_voice=PRIMARY_VOICE,
        fallback_voice=FALLBACK_VOICE,
        audio_directory=str(AUDIO_DIR),
    )


@app.get('/favicon.ico')
def favicon():
    return make_response('', 204)


@app.get('/health')
def health(): return jsonify(ok=True,rooms=len(ROOMS))

if __name__=='__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT','5000')), threaded=True, use_reloader=False)
