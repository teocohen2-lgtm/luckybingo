import asyncio
import hashlib
import os
import random
import string
import threading
import time
import uuid
from pathlib import Path

try:
    import edge_tts
except ImportError:  # Allows core game tests before optional voice dependency is installed.
    edge_tts = None
from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

ROOMS = {}
TOKENS = {}  # reconnect_token -> (room_code, player_id)
LOCK = threading.RLock()
CHANGED = threading.Condition(LOCK)
AUDIO_LOCK = threading.RLock()

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
VOICE = os.getenv('BINGO_VOICE', 'fil-PH-AngeloNeural')
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
    'Check your card—baka ito na!',
    'Mark it now—huwag puro chika!',
    'Good luck, mga ka-bingo!',
    'Eyes on your card—baka panalo ka na!',
    'No cheating, puro saya lang!',
    'Nice one! Tingnan ang card mo!',
]
SPECIAL = {
    7:'Lucky seven! Swerte na, baka bingo na!',
    10:'Perfect ten! Parang perfect score sa karaoke!',
    22:'Two little ducks—quack quack!',
    40:'Life begins, and bingo continues!',
    55:'Double five—high five ulit!',
    75:'Last ball—bingo na ba, mga kaibigan?'
}


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


def audio_path(text):
    key = hashlib.sha256(f'{VOICE}|{text}'.encode()).hexdigest()[:28]
    return AUDIO_DIR / f'{key}.mp3'


def generate_audio(text, path):
    if edge_tts is None:
        raise RuntimeError('edge-tts is not installed')
    tmp = path.with_suffix('.part.mp3')
    last = None
    for attempt in range(3):
        try:
            if tmp.exists(): tmp.unlink()
            asyncio.run(edge_tts.Communicate(text, VOICE, rate='-4%', pitch='-2Hz').save(str(tmp)))
            if not tmp.exists() or tmp.stat().st_size < 500: raise RuntimeError('empty audio')
            tmp.replace(path)
            return
        except Exception as exc:
            last = exc
            time.sleep(.4 * (attempt + 1))
    raise last or RuntimeError('voice generation failed')


def prewarm(text):
    path = audio_path(text)
    if path.exists(): return
    def run():
        try:
            with AUDIO_LOCK:
                if not path.exists(): generate_audio(text, path)
        except Exception: pass
    threading.Thread(target=run, daemon=True).start()


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
    room['announcement'] = announcement(number, room['card_size'])
    room['announcement_id'] = uuid.uuid4().hex
    room['announcement_kind'] = 'number'
    room['message'] = f'{letter_for(number,room["card_size"])}-{number} called. Waiting for players who have it.'
    prewarm(room['announcement']); bump(room); return True


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
        'announcement_id':room['announcement_id'],'audio_url':f'/api/audio/{room["announcement_id"]}' if room['announcement_id'] else '',
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


def auto_loop():
    while True:
        time.sleep(.4)
        with LOCK:
            current = now()
            for room in list(ROOMS.values()):
                if room['status'] != 'playing' or room['call_mode'] != 'auto': continue
                if not room['called']:
                    if current >= room.get('next_auto_call_at', current + 999): perform_call(room)
                    continue
                r = readiness(room)
                if r['all_ready']:
                    if room['ready_since'] is None:
                        room['ready_since'] = current; bump(room)
                    due = room['ready_since'] + room['auto_interval']
                elif r['timed_out']:
                    due = current
                else:
                    room['ready_since'] = None; continue
                if current >= due: perform_call(room)

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
        room['status']='playing'; room['next_auto_call_at']=now()+2; room['message']='Game started. First ball is coming…'; bump(room)
        return jsonify(ok=True,state=room_state(room,pid))

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
        room['message']=f'{p["name"]} won {names}!'; room['announcement']=f'Congratulations, {p["name"]}! You won {names}. Panalo ka!'; room['announcement_id']=uuid.uuid4().hex; prewarm(room['announcement']); bump(room)
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

@app.get('/api/audio/<announcement_id>')
def audio(announcement_id):
    text=None
    with LOCK:
        for room in ROOMS.values():
            if room.get('announcement_id')==announcement_id:text=room.get('announcement');break
    if not text:return jsonify(error='Audio expired.'),404
    path=audio_path(text)
    try:
        with AUDIO_LOCK:
            if not path.exists():generate_audio(text,path)
        return send_file(path,mimetype='audio/mpeg',conditional=True,max_age=86400)
    except Exception as exc:
        return jsonify(error='Voice temporarily unavailable.',fallback_text=text),503

@app.get('/health')
def health(): return jsonify(ok=True,rooms=len(ROOMS))

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')),threaded=True)
