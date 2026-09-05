import asyncio
import logging
import random
import json
import os
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, PreCheckoutQuery, 
    LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "YOUR_BOT_TOKEN"
GROUP_ID = -1004402038932  # ID группы для раздачи звезд
ADMINS = [123456789, 987654321]  # ID администраторов

REQUIRED_CHANNELS = [
    {"url": "https://t.me/ADAMSTARRCHAT", "username": "@ADAMSTARRCHAT"},
    {"url": "https://t.me/ADAMSTARRGAME", "username": "@ADAMSTARRGAME"}
]

WAGER_MULTIPLIER = 2.5  # Вейджер x2.5

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Файлы для хранения данных
DATA_FILE = "bot_data.json"
WITHDRAW_FILE = "withdraw_requests.json"
DEPOSIT_FILE = "deposit_history.json"
ADMIN_LOG_FILE = "admin_log.json"

# ========== СОСТОЯНИЯ FSM ==========
class WithdrawStates(StatesGroup):
    choosing_method = State()
    choosing_gift = State()
    entering_amount = State()

class AdminStates(StatesGroup):
    waiting_user_id = State()
    waiting_amount = State()
    waiting_reason = State()

# ========== БАЗА ДАННЫХ ==========
class BotData:
    def __init__(self):
        self.users: Dict[int, Dict] = {}
        self.withdraw_requests: List[Dict] = []
        self.deposit_history: List[Dict] = []
        self.admin_logs: List[Dict] = []
        self.load()
        
    def load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.users = {int(k): v for k, v in data.get('users', {}).items()}
            except:
                pass
        if os.path.exists(WITHDRAW_FILE):
            try:
                with open(WITHDRAW_FILE, 'r', encoding='utf-8') as f:
                    self.withdraw_requests = json.load(f)
            except:
                pass
        if os.path.exists(DEPOSIT_FILE):
            try:
                with open(DEPOSIT_FILE, 'r', encoding='utf-8') as f:
                    self.deposit_history = json.load(f)
            except:
                pass
        if os.path.exists(ADMIN_LOG_FILE):
            try:
                with open(ADMIN_LOG_FILE, 'r', encoding='utf-8') as f:
                    self.admin_logs = json.load(f)
            except:
                pass
    
    def save(self):
        data = {'users': {str(k): v for k, v in self.users.items()}}
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        with open(WITHDRAW_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.withdraw_requests, f, ensure_ascii=False, indent=2)
        with open(DEPOSIT_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.deposit_history, f, ensure_ascii=False, indent=2)
        with open(ADMIN_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.admin_logs, f, ensure_ascii=False, indent=2)
    
    def get_user(self, user_id: int) -> Dict:
        if user_id not in self.users:
            self.users[user_id] = {
                'balance': 0,
                'total_earned': 0,
                'deposit_total': 0,  # Сумма депозитов (для вейджера)
                'wager_required': 0,  # Требуемый вейджер
                'wager_progress': 0,  # Прогресс вейджера
                'last_daily_bonus': None,
                'last_hourly_bonus': None,
                'game_history': [],  # История игр
                'deposit_history': [],  # История пополнений
                'withdraw_history': [],  # История выводов
                'username': None,
                'first_name': None,
                'banned': False,
                'ban_reason': ''
            }
            self.save()
        return self.users[user_id]
    
    def add_balance(self, user_id: int, amount: int, source: str = 'unknown'):
        user = self.get_user(user_id)
        user['balance'] += amount
        if amount > 0:
            user['total_earned'] += amount
        self.save()
        
        # Логируем изменение баланса
        self.admin_logs.append({
            'timestamp': datetime.now().isoformat(),
            'user_id': user_id,
            'amount': amount,
            'source': source,
            'balance_after': user['balance']
        })
        if len(self.admin_logs) > 1000:
            self.admin_logs = self.admin_logs[-1000:]
        self.save()
    
    def get_balance(self, user_id: int) -> int:
        return self.get_user(user_id)['balance']
    
    def add_deposit(self, user_id: int, amount: int):
        user = self.get_user(user_id)
        user['deposit_total'] += amount
        user['wager_required'] += int(amount * WAGER_MULTIPLIER)
        user['deposit_history'].append({
            'amount': amount,
            'date': datetime.now().isoformat(),
            'wager_added': int(amount * WAGER_MULTIPLIER)
        })
        self.deposit_history.append({
            'user_id': user_id,
            'amount': amount,
            'date': datetime.now().isoformat()
        })
        self.add_balance(user_id, amount, 'deposit')
        self.save()
    
    def add_wager_progress(self, user_id: int, bet_amount: int):
        user = self.get_user(user_id)
        if user['wager_required'] > 0:
            user['wager_progress'] += bet_amount
            if user['wager_progress'] >= user['wager_required']:
                user['wager_progress'] = user['wager_required']
        self.save()
    
    def get_withdrawable_balance(self, user_id: int) -> int:
        user = self.get_user(user_id)
        if user['wager_required'] == 0:
            return user['balance']
        
        # Если вейджер выполнен - можно вывести всё
        if user['wager_progress'] >= user['wager_required']:
            return user['balance']
        
        # Иначе можно вывести только выигрыш (то, что сверх депозита)
        # Депозит заблокирован до выполнения вейджера
        return max(0, user['balance'] - user['deposit_total'])
    
    def add_game_history(self, user_id: int, game_name: str, bet: int, win: int, result: str):
        user = self.get_user(user_id)
        user['game_history'].insert(0, {
            'game': game_name,
            'bet': bet,
            'win': win,
            'result': result,  # 'win' or 'lose'
            'date': datetime.now().isoformat()
        })
        if len(user['game_history']) > 100:
            user['game_history'] = user['game_history'][:100]
        
        # Обновляем прогресс вейджера
        self.add_wager_progress(user_id, bet)
        self.save()

db = BotData()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def fmt(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

async def check_subscription(user_id: int) -> tuple:
    """Проверяет подписку на все обязательные каналы"""
    not_subscribed = []
    for channel in REQUIRED_CHANNELS:
        try:
            username = channel['username'].replace('@', '')
            member = await bot.get_chat_member(f"@{username}", user_id)
            if member.status in ['left', 'kicked']:
                not_subscribed.append(channel['url'])
        except:
            not_subscribed.append(channel['url'])
    return not_subscribed

async def send_gift_to_user(bot_token: str, user_id: int, gift_id: str, text: str = None):
    """Отправляет подарок пользователю через Telegram Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendGift"
    payload = {
        "user_id": user_id,
        "gift_id": gift_id,
    }
    if text:
        payload["text"] = text
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            return await response.json()

def get_subscription_keyboard():
    """Клавиатура для подписки на каналы"""
    kb = []
    for channel in REQUIRED_CHANNELS:
        kb.append([InlineKeyboardButton(text=f"📢 Подписаться на {channel['username']}", url=channel['url'])])
    kb.append([InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ========== ИГРЫ ==========

# --- СЛОТЫ ---
async def play_slots(msg: Message, uid: int, bet: int):
    user = db.get_user(uid)
    if user['balance'] < bet:
        return {'ok': False, 'msg': '❌ Не хватает звезд'}
    
    db.add_balance(uid, -bet, 'game_bet')
    
    slots_msg = await msg.answer_dice(emoji='🎰')
    slots_value = slots_msg.dice.value
    
    if slots_value == 64:
        mult = 10
        win = bet * mult
        db.add_balance(uid, win, 'game_win')
        db.add_game_history(uid, 'Слоты', bet, win, 'win')
        return {'ok': True, 'win': True, 'value': slots_value, 'mult': mult, 'amount': win, 'balance': db.get_balance(uid)}
    elif slots_value in [1, 22, 43]:
        mult = 5
        win = bet * mult
        db.add_balance(uid, win, 'game_win')
        db.add_game_history(uid, 'Слоты', bet, win, 'win')
        return {'ok': True, 'win': True, 'value': slots_value, 'mult': mult, 'amount': win, 'balance': db.get_balance(uid)}
    else:
        db.add_game_history(uid, 'Слоты', bet, 0, 'lose')
        return {'ok': True, 'win': False, 'value': slots_value, 'amount': bet, 'balance': db.get_balance(uid)}

# --- ФУТБОЛ ---
async def play_football(msg: Message, uid: int, bet: int, choice: str):
    user = db.get_user(uid)
    if user['balance'] < bet:
        return {'ok': False, 'msg': '❌ Не хватает звезд'}
    
    db.add_balance(uid, -bet, 'game_bet')
    
    football_msg = await msg.answer_dice(emoji='⚽')
    football_value = football_msg.dice.value
    
    is_goal = football_value >= 4
    result = 'гол' if is_goal else 'мимо'
    win = (choice == 'гол' and is_goal) or (choice == 'мимо' and not is_goal)
    
    if win:
        win_amount = bet * 2
        db.add_balance(uid, win_amount, 'game_win')
        db.add_game_history(uid, 'Футбол', bet, win_amount, 'win')
        return {
            'ok': True, 'win': True, 'result': result, 'amount': win_amount,
            'balance': db.get_balance(uid), 'value': football_value,
            'is_goal': is_goal
        }
    else:
        db.add_game_history(uid, 'Футбол', bet, 0, 'lose')
        return {
            'ok': True, 'win': False, 'result': result, 'amount': bet,
            'balance': db.get_balance(uid), 'value': football_value,
            'is_goal': is_goal
        }

# --- БАСКЕТБОЛ ---
async def play_basketball(msg: Message, uid: int, bet: int, choice: str):
    user = db.get_user(uid)
    if user['balance'] < bet:
        return {'ok': False, 'msg': '❌ Не хватает звезд'}
    
    db.add_balance(uid, -bet, 'game_bet')
    
    basketball_msg = await msg.answer_dice(emoji='🏀')
    basketball_value = basketball_msg.dice.value
    
    is_goal = basketball_value >= 4
    result = 'гол' if is_goal else 'мимо'
    win = (choice == 'гол' and is_goal) or (choice == 'мимо' and not is_goal)
    
    if win:
        win_amount = bet * 2
        db.add_balance(uid, win_amount, 'game_win')
        db.add_game_history(uid, 'Баскетбол', bet, win_amount, 'win')
        return {
            'ok': True, 'win': True, 'result': result, 'amount': win_amount,
            'balance': db.get_balance(uid), 'value': basketball_value,
            'is_goal': is_goal
        }
    else:
        db.add_game_history(uid, 'Баскетбол', bet, 0, 'lose')
        return {
            'ok': True, 'win': False, 'result': result, 'amount': bet,
            'balance': db.get_balance(uid), 'value': basketball_value,
            'is_goal': is_goal
        }

# --- ДАРТС ---
sectors = {
    'ц': {'emoji': '🎯', 'mult': 5.8, 'name': 'ЦЕНТР'},
    'к': {'emoji': '🔴', 'mult': 1.94, 'name': 'КРАСНОЕ'},
    'б': {'emoji': '⚪', 'mult': 2.9, 'name': 'БЕЛОЕ'},
    'м': {'emoji': '💢', 'mult': 5.8, 'name': 'МИМО'}
}

async def play_dart(msg: Message, uid: int, bet: int, choice: str):
    user = db.get_user(uid)
    if user['balance'] < bet:
        return {'ok': False, 'msg': '❌ Не хватает звезд'}
    
    db.add_balance(uid, -bet, 'game_bet')
    
    dart_msg = await msg.answer_dice(emoji='🎯')
    dart_value = dart_msg.dice.value
    
    if dart_value == 1:
        result = 'м'
    elif dart_value == 2:
        result = 'к'
    elif dart_value == 3:
        result = 'б'
    elif dart_value == 4:
        result = 'к'
    elif dart_value == 5:
        result = 'б'
    elif dart_value == 6:
        result = 'ц'
    
    sector = sectors[result]
    win = (choice == result)
    
    if win:
        win_amount = int(bet * sector['mult'])
        db.add_balance(uid, win_amount, 'game_win')
        db.add_game_history(uid, 'Дартс', bet, win_amount, 'win')
        return {
            'ok': True, 'win': True, 'result': result, 'amount': win_amount,
            'balance': db.get_balance(uid), 'value': dart_value,
            'sector': sector
        }
    else:
        db.add_game_history(uid, 'Дартс', bet, 0, 'lose')
        return {
            'ok': True, 'win': False, 'result': result, 'amount': bet,
            'balance': db.get_balance(uid), 'value': dart_value,
            'sector': sector
        }

# --- МИНЫ ---
class MinesGame:
    def __init__(self):
        self.games = {}
        self.mults = {
            1: [1.01, 1.05, 1.10, 1.15, 1.21, 1.27, 1.34, 1.41, 1.48, 1.56, 1.64, 1.72, 1.81, 1.90, 2.00, 2.10, 2.21, 2.32, 2.44, 2.56, 2.69, 2.82, 2.96, 3.11],
            2: [1.05, 1.15, 1.26, 1.39, 1.53, 1.68, 1.85, 2.04, 2.24, 2.46, 2.71, 2.98, 3.28, 3.61, 3.97, 4.37, 4.81, 5.29, 5.82, 6.40, 7.04, 7.74, 8.51, 9.36],
            3: [1.10, 1.26, 1.45, 1.68, 1.94, 2.24, 2.59, 3.00, 3.47, 4.01, 4.64, 5.37, 6.21, 7.19, 8.32, 9.63, 11.14, 12.89, 14.92, 17.26, 19.97, 23.11, 26.74, 30.94],
            4: [1.15, 1.39, 1.68, 2.04, 2.47, 3.00, 3.64, 4.41, 5.35, 6.49, 7.87, 9.55, 11.58, 14.05, 17.04, 20.67, 25.08, 30.42, 36.90, 44.76, 54.30, 65.86, 79.89, 96.91],
            5: [1.21, 1.53, 1.94, 2.47, 3.14, 3.99, 5.07, 6.45, 8.20, 10.43, 13.26, 16.86, 21.44, 27.26, 34.66, 44.07, 56.04, 71.25, 90.60, 115.20, 146.48, 186.25, 236.83, 301.13],
            6: [1.27, 1.68, 2.24, 3.00, 4.01, 5.37, 7.19, 9.63, 12.89, 17.26, 23.11, 30.94, 41.43, 55.47, 74.27, 99.44, 133.14, 178.25, 238.65, 319.54, 427.86, 572.90, 767.09, 1027.23]
        }
    
    def start(self, uid: int, bet: int, mines: int = 1):
        if uid in self.games:
            return {'ok': False, 'msg': '❌ Игра уже идёт'}
        
        if mines < 1 or mines > 6:
            return {'ok': False, 'msg': '❌ Мин от 1 до 6'}
        
        user = db.get_user(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': '❌ Не хватает звезд'}
        
        db.add_balance(uid, -bet, 'game_bet')
        
        field = [['⬜']*5 for _ in range(5)]
        mpos = []
        while len(mpos) < mines:
            p = (random.randint(0,4), random.randint(0,4))
            if p not in mpos:
                mpos.append(p)
        
        self.games[uid] = {
            'bet': bet,
            'field': field,
            'mines': mpos,
            'count': mines,
            'opened': [],
            'mult': 1.0,
            'mults': self.mults[mines],
            'won': 0,
            'bal': db.get_balance(uid)
        }
        return {'ok': True, 'data': self.games[uid]}
    
    def open(self, uid: int, r: int, c: int):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if (r,c) in g['opened']:
            return {'ok': False, 'msg': '❌ Уже открыто'}
        
        if (r,c) in g['mines']:
            for rr,cc in g['mines']:
                g['field'][rr][cc] = '💣'
            g['field'][r][c] = '💥'
            opened = len(g['opened'])
            db.add_game_history(uid, 'Мины', g['bet'], 0, 'lose')
            del self.games[uid]
            return {'ok': True, 'over': True, 'win': False, 'field': g['field'], 'opened': opened, 'bet': g['bet']}
        
        g['opened'].append((r,c))
        g['field'][r][c] = '💰'
        opened = len(g['opened'])
        g['mult'] = g['mults'][opened-1] if opened-1 < len(g['mults']) else 2.5
        g['won'] = int(g['bet'] * g['mult'])
        
        if opened >= 25 - g['count']:
            db.add_balance(uid, g['won'], 'game_win')
            db.add_game_history(uid, 'Мины', g['bet'], g['won'], 'win')
            for rr,cc in g['mines']:
                g['field'][rr][cc] = '💣'
            field = [row[:] for row in g['field']]
            del self.games[uid]
            return {'ok': True, 'over': True, 'win': True, 'field': field, 'opened': opened, 'won': g['won'], 'mult': g['mult'], 'balance': db.get_balance(uid)}
        
        return {'ok': True, 'over': False, 'field': g['field'], 'opened': opened, 'mult': g['mult'], 'won': g['won'], 'max': 25 - g['count']}
    
    def cashout(self, uid: int):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if not g['opened']:
            return {'ok': False, 'msg': '❌ Сначала открой клетку'}
        
        db.add_balance(uid, g['won'], 'game_win')
        db.add_game_history(uid, 'Мины', g['bet'], g['won'], 'win')
        for rr,cc in g['mines']:
            g['field'][rr][cc] = '💣'
        field = [row[:] for row in g['field']]
        won = g['won']
        opened = len(g['opened'])
        mult = g['mult']
        del self.games[uid]
        return {'ok': True, 'won': won, 'balance': db.get_balance(uid), 'field': field, 'opened': opened, 'mult': mult}
    
    def cancel(self, uid: int):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        g = self.games[uid]
        db.add_balance(uid, g['bet'], 'game_cancel')
        del self.games[uid]
        return {'ok': True, 'msg': f'✅ Отмена, {fmt(g["bet"])} возвращено'}
    
    def get_kb(self, uid: int, field, active=True):
        kb = []
        for i in range(5):
            row = []
            for j in range(5):
                if field[i][j] in ['💰','💣','💥']:
                    row.append(InlineKeyboardButton(text=field[i][j], callback_data="ignore"))
                else:
                    row.append(InlineKeyboardButton(text="❓" if active else "⬛", callback_data=f"mines_{uid}_{i}_{j}"))
            kb.append(row)
        if active:
            kb.append([InlineKeyboardButton(text="🏆 Забрать", callback_data=f"cashout_{uid}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

mines_game = MinesGame()

# --- БАШНЯ ---
class TowerGame:
    def __init__(self):
        self.games = {}
        self.base = [1.2, 1.5, 2.0, 2.5, 3.2, 4.0, 5.0, 6.0, 7.0]
    
    def mults(self, mines):
        if mines == 1:
            return self.base
        elif mines == 2:
            return [round(x * 1.4, 2) for x in self.base]
        elif mines == 3:
            return [round(x * 1.8, 2) for x in self.base]
        elif mines == 4:
            return [round(x * 2.2, 2) for x in self.base]
        return self.base
    
    def start(self, uid: int, bet: int, mines: int = 1):
        if uid in self.games:
            return {'ok': False, 'msg': '❌ Игра уже идёт'}
        
        if mines < 1 or mines > 4:
            return {'ok': False, 'msg': '❌ Мин от 1 до 4'}
        
        user = db.get_user(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': '❌ Не хватает звезд'}
        
        db.add_balance(uid, -bet, 'game_bet')
        
        row = {
            'cells': ['⬜']*5,
            'mines': random.sample(range(5), mines),
            'revealed': False
        }
        
        self.games[uid] = {
            'bet': bet,
            'mines': mines,
            'row': 0,
            'rows': [row],
            'opened': [],
            'mult': 1.0,
            'mults': self.mults(mines),
            'bal': db.get_balance(uid),
            'won': 0,
            'total_rows': 9
        }
        return {'ok': True, 'data': self.games[uid]}
    
    def open(self, uid: int, r: int, c: int):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if r != g['row']:
            return {'ok': False, 'msg': '❌ Можно открывать только текущий ряд'}
        
        if f"{r}_{c}" in g['opened']:
            return {'ok': False, 'msg': '❌ Уже открыто'}
        
        row = g['rows'][r]
        
        if c in row['mines']:
            for i in range(5):
                row['cells'][i] = '💣' if i in row['mines'] else '⬛'
            row['cells'][c] = '💥'
            db.add_game_history(uid, 'Башня', g['bet'], 0, 'lose')
            del self.games[uid]
            return {'ok': True, 'over': True, 'mine': True, 'row_data': row, 'bet': g['bet']}
        
        g['opened'].append(f"{r}_{c}")
        row['cells'][c] = '🟩'
        g['mult'] = g['mults'][r]
        g['won'] = int(g['bet'] * g['mult'])
        
        if r >= g['total_rows'] - 1:
            db.add_balance(uid, g['won'], 'game_win')
            db.add_game_history(uid, 'Башня', g['bet'], g['won'], 'win')
            del self.games[uid]
            return {'ok': True, 'over': True, 'win': True, 'won': g['won'], 'mult': g['mult'], 'rows': r+1, 'balance': db.get_balance(uid)}
        
        g['row'] += 1
        if len(g['rows']) <= g['row']:
            g['rows'].append({
                'cells': ['⬜']*5,
                'mines': random.sample(range(5), g['mines']),
                'revealed': False
            })
        
        return {'ok': True, 'over': False, 'row': r, 'col': c, 'next': g['row'], 'mult': g['mult'], 'won': g['won']}
    
    def cashout(self, uid: int):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if not g['opened']:
            return {'ok': False, 'msg': '❌ Сначала открой клетку'}
        
        db.add_balance(uid, g['won'], 'game_win')
        db.add_game_history(uid, 'Башня', g['bet'], g['won'], 'win')
        del self.games[uid]
        return {'ok': True, 'won': g['won'], 'mult': g['mult'], 'rows': g['row'], 'balance': db.get_balance(uid)}
    
    def cancel(self, uid: int):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        g = self.games[uid]
        db.add_balance(uid, g['bet'], 'game_cancel')
        del self.games[uid]
        return {'ok': True, 'msg': f'✅ Отмена, {fmt(g["bet"])} возвращено'}
    
    def get_kb(self, uid: int, g):
        kb = []
        for r in range(g['total_rows'] - 1, -1, -1):
            if r < len(g['rows']):
                row = g['rows'][r]
                btns = []
                if r > g['row']:
                    for c in range(5):
                        btns.append(InlineKeyboardButton(text="⬛", callback_data="ignore"))
                elif r == g['row']:
                    for c in range(5):
                        if f"{r}_{c}" in g['opened']:
                            btns.append(InlineKeyboardButton(text="💰", callback_data="ignore"))
                        else:
                            btns.append(InlineKeyboardButton(text="❓", callback_data=f"tower_{uid}_{r}_{c}"))
                else:
                    for c in range(5):
                        if f"{r}_{c}" in g['opened']:
                            btns.append(InlineKeyboardButton(text="💰", callback_data="ignore"))
                        else:
                            btns.append(InlineKeyboardButton(text="⬛", callback_data="ignore"))
                kb.append(btns)
            else:
                btns = [InlineKeyboardButton(text="⬛", callback_data="ignore") for _ in range(5)]
                kb.append(btns)
        
        if g['opened']:
            kb.append([InlineKeyboardButton(text="🏆 Забрать", callback_data=f"tower_cash_{uid}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

tower_game = TowerGame()

# --- ПИРАМИДА ---
class PyramidGame:
    def __init__(self):
        self.games = {}
        self.multipliers = {
            1: [1.7, 2.3, 3.1, 4.2, 5.7, 7.7, 10.4, 14.0, 18.9, 25.5, 34.4, 46.4],
            2: [1.5, 2.0, 2.7, 3.6, 4.9, 6.6, 8.9, 12.0, 16.2, 21.9, 29.6, 40.0],
            3: [1.31, 1.74, 2.32, 3.10, 4.13, 5.51, 7.34, 9.79, 13.05, 17.40, 23.20, 30.93]
        }
    
    def start(self, uid: int, bet: int, doors: int = 3):
        if uid in self.games:
            return {'ok': False, 'msg': '❌ Игра уже идёт'}
        
        if doors < 1 or doors > 3:
            return {'ok': False, 'msg': '❌ Дверей от 1 до 3'}
        
        user = db.get_user(uid)
        if user['balance'] < bet:
            return {'ok': False, 'msg': '❌ Не хватает звезд'}
        
        db.add_balance(uid, -bet, 'game_bet')
        
        def gen_level():
            cells = ['🚪'] * 4
            safe = random.sample(range(4), doors)
            return {'cells': cells, 'safe': safe, 'revealed': False}
        
        self.games[uid] = {
            'bet': bet,
            'doors': doors,
            'level': 0,
            'levels': [gen_level()],
            'opened': [],
            'mult': 1.0,
            'mults': self.multipliers[doors],
            'bal': db.get_balance(uid),
            'won': 0
        }
        return {'ok': True, 'data': self.games[uid]}
    
    def open(self, uid: int, level_idx: int, cell_idx: int):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if level_idx != g['level']:
            return {'ok': False, 'msg': '❌ Можно открывать только текущий уровень'}
        
        if f"{level_idx}_{cell_idx}" in g['opened']:
            return {'ok': False, 'msg': '❌ Уже открыто'}
        
        level = g['levels'][level_idx]
        
        if cell_idx not in level['safe']:
            for i in range(4):
                level['cells'][i] = '💀' if i not in level['safe'] else '🚪'
            level['cells'][cell_idx] = '💥'
            db.add_game_history(uid, 'Пирамида', g['bet'], 0, 'lose')
            del self.games[uid]
            return {'ok': True, 'over': True, 'win': False, 'level': level_idx+1, 'bet': g['bet']}
        
        g['opened'].append(f"{level_idx}_{cell_idx}")
        level['cells'][cell_idx] = '✅'
        level['revealed'] = True
        g['mult'] = g['mults'][level_idx]
        g['won'] = int(g['bet'] * g['mult'])
        
        if level_idx >= 11:
            db.add_balance(uid, g['won'], 'game_win')
            db.add_game_history(uid, 'Пирамида', g['bet'], g['won'], 'win')
            del self.games[uid]
            return {'ok': True, 'over': True, 'win': True, 'won': g['won'], 'mult': g['mult'], 'level': level_idx+1, 'balance': db.get_balance(uid)}
        
        g['level'] += 1
        if len(g['levels']) <= g['level']:
            def gen_level():
                cells = ['🚪'] * 4
                safe = random.sample(range(4), g['doors'])
                return {'cells': cells, 'safe': safe, 'revealed': False}
            g['levels'].append(gen_level())
        
        return {'ok': True, 'over': False, 'level': level_idx+1, 'next': g['level']+1, 'mult': g['mult'], 'won': g['won'], 'max_level': 12}
    
    def cashout(self, uid: int):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        
        g = self.games[uid]
        if g['level'] == 0:
            return {'ok': False, 'msg': '❌ Сначала открой дверь'}
        
        db.add_balance(uid, g['won'], 'game_win')
        db.add_game_history(uid, 'Пирамида', g['bet'], g['won'], 'win')
        del self.games[uid]
        return {'ok': True, 'won': g['won'], 'mult': g['mult'], 'level': g['level'], 'balance': db.get_balance(uid)}
    
    def cancel(self, uid: int):
        if uid not in self.games:
            return {'ok': False, 'msg': '❌ Нет игры'}
        g = self.games[uid]
        db.add_balance(uid, g['bet'], 'game_cancel')
        del self.games[uid]
        return {'ok': True, 'msg': f'✅ Отмена, {fmt(g["bet"])} возвращено'}
    
    def get_kb(self, uid: int, g):
        kb = []
        current_level = g['levels'][g['level']]
        row1, row2 = [], []
        for i in range(4):
            if f"{g['level']}_{i}" in g['opened']:
                btn = InlineKeyboardButton(text="✅", callback_data="ignore")
            else:
                btn = InlineKeyboardButton(text="🚪", callback_data=f"pyramid_{uid}_{g['level']}_{i}")
            if i < 2:
                row1.append(btn)
            else:
                row2.append(btn)
        kb.append(row1)
        kb.append(row2)
        if g['opened']:
            kb.append([InlineKeyboardButton(text="🏆 Забрать", callback_data=f"pyramid_cash_{uid}")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

pyramid_game = PyramidGame()

# ========== ОБРАБОТЧИКИ КОМАНД ==========

# --- БАЛАНС И ОСНОВНОЕ МЕНЮ ---
async def show_balance(msg: Message):
    user = db.get_user(msg.from_user.id)
    balance = user['balance']
    withdrawable = db.get_withdrawable_balance(msg.from_user.id)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Пополнить", callback_data="deposit"),
         InlineKeyboardButton(text="💳 Вывод", callback_data="withdraw")],
        [InlineKeyboardButton(text="📊 Вейджер", callback_data="wager_info")]
    ])
    
    wager_text = ""
    if user['wager_required'] > 0:
        progress = min(100, int(user['wager_progress'] / user['wager_required'] * 100))
        wager_text = f"\n📊 Вейджер: {progress}% ({fmt(user['wager_progress'])}/{fmt(user['wager_required'])})"
        wager_text += f"\n💳 Доступно к выводу: {fmt(withdrawable)} ⭐"
    
    await msg.reply(
        f"💰 Твой баланс: {balance} ⭐\n"
        f"📊 Всего заработано: {user['total_earned']} ⭐"
        f"{wager_text}",
        reply_markup=kb
    )

# --- ВЕЙДЖЕР ---
async def show_wager_info(msg: Message):
    user = db.get_user(msg.from_user.id)
    if user['wager_required'] == 0:
        await msg.reply("📊 У тебя нет активного вейджера.\nВсе средства доступны для вывода!")
        return
    
    progress = min(100, int(user['wager_progress'] / user['wager_required'] * 100))
    remaining = user['wager_required'] - user['wager_progress']
    
    await msg.reply(
        f"📊 ИНФОРМАЦИЯ О ВЕЙДЖЕРЕ\n\n"
        f"💰 Депозитов: {fmt(user['deposit_total'])} ⭐\n"
        f"🎯 Требуется отыграть: {fmt(user['wager_required'])} ⭐\n"
        f"📈 Прогресс: {fmt(user['wager_progress'])} ⭐\n"
        f"📊 Выполнено: {progress}%\n"
        f"⏳ Осталось: {fmt(remaining)} ⭐\n\n"
        f"💡 Вейджер x{WAGER_MULTIPLIER} от суммы депозита"
    )

# --- ПОПОЛНЕНИЕ ---
async def deposit_stars(msg: Message):
    await msg.reply(
        "💳 Пополнение баланса\n\n"
        "Выберите сумму:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="10 ⭐", callback_data="buy_10"),
             InlineKeyboardButton(text="25 ⭐", callback_data="buy_25")],
            [InlineKeyboardButton(text="50 ⭐", callback_data="buy_50"),
             InlineKeyboardButton(text="100 ⭐", callback_data="buy_100")],
            [InlineKeyboardButton(text="250 ⭐", callback_data="buy_250"),
             InlineKeyboardButton(text="500 ⭐", callback_data="buy_500")]
        ])
    )

async def process_deposit(user_id: int, amount: int):
    """Обработка депозита с вейджером"""
    db.add_deposit(user_id, amount)
    await bot.send_message(
        user_id,
        f"✅ Пополнено: +{amount} ⭐\n"
        f"💰 Баланс: {db.get_balance(user_id)} ⭐\n\n"
        f"📊 Вейджер x{WAGER_MULTIPLIER}: нужно отыграть {int(amount * WAGER_MULTIPLIER)} ⭐"
    )

# --- ВЫВОД ---
async def start_withdraw(msg: Message, state: FSMContext):
    user = db.get_user(msg.from_user.id)
    withdrawable = db.get_withdrawable_balance(msg.from_user.id)
    
    if withdrawable < 15:
        await msg.reply(f"❌ Минимальная сумма для вывода: 15 ⭐\nДоступно к выводу: {withdrawable} ⭐")
        return
    
    await state.set_state(WithdrawStates.choosing_method)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Подарком", callback_data="withdraw_gift")],
        [InlineKeyboardButton(text="⭐ На баланс", callback_data="withdraw_balance")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="withdraw_cancel")]
    ])
    await msg.reply(f"💳 Выберите способ вывода\nДоступно: {withdrawable} ⭐", reply_markup=kb)

async def withdraw_gift(msg: Message, state: FSMContext):
    await state.set_state(WithdrawStates.choosing_gift)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Подарок 1 (10⭐)", callback_data="gift_1")],
        [InlineKeyboardButton(text="🎊 Подарок 2 (25⭐)", callback_data="gift_2")],
        [InlineKeyboardButton(text="🎉 Подарок 3 (50⭐)", callback_data="gift_3")],
        [InlineKeyboardButton(text="🌟 Подарок 4 (100⭐)", callback_data="gift_4")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="withdraw_cancel")]
    ])
    await msg.reply("🎁 Выберите подарок:", reply_markup=kb)

async def withdraw_balance_start(msg: Message, state: FSMContext):
    withdrawable = db.get_withdrawable_balance(msg.from_user.id)
    await state.set_state(WithdrawStates.entering_amount)
    await msg.reply(f"💰 Введите сумму для вывода (доступно: {withdrawable} ⭐):")

async def process_withdraw_amount(msg: Message, state: FSMContext):
    try:
        amount = int(msg.text.strip())
    except:
        await msg.reply("❌ Введите число")
        return
    
    user = db.get_user(msg.from_user.id)
    withdrawable = db.get_withdrawable_balance(msg.from_user.id)
    
    if amount < 15:
        await msg.reply("❌ Минимальная сумма для вывода: 15 ⭐")
        return
    
    if amount > withdrawable:
        await msg.reply(f"❌ Недостаточно доступных средств. Доступно: {withdrawable} ⭐")
        return
    
    # Создаём заявку
    request = {
        'id': len(db.withdraw_requests) + 1,
        'user_id': msg.from_user.id,
        'username': msg.from_user.username or str(msg.from_user.id),
        'amount': amount,
        'status': 'pending',
        'created_at': datetime.now().isoformat()
    }
    db.withdraw_requests.append(request)
    db.save()
    
    # Отправляем админам
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{request['id']}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{request['id']}")]
    ])
    
    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                f"📝 Новая заявка на вывод #{request['id']}\n"
                f"👤 ID: {msg.from_user.id}\n"
                f"👤 Username: @{msg.from_user.username or 'Нет'}\n"
                f"💰 Сумма: {amount} ⭐",
                reply_markup=kb
            )
        except:
            pass
    
    await msg.reply(f"✅ Заявка на вывод #{request['id']} отправлена администрации!")
    await state.clear()

# --- ДАЙЛИ БОНУС ---
async def daily_bonus(msg: Message):
    user = db.get_user(msg.from_user.id)
    
    # Проверяем описание профиля
    try:
        chat = await bot.get_chat(msg.from_user.id)
        bio = chat.bio or ""
    except:
        bio = ""
    
    if "@ADAMSTARRBOT" not in bio:
        await msg.reply(
            "❌ Ежедневный бонус недоступен!\n\n"
            "Добавьте в описание профиля:\n"
            "<code>@ADAMSTARRBOT - лучший игровой бот на телеграм звезды 🌟</code>\n\n"
            "После добавления нажмите 'ежед' снова.",
            parse_mode="HTML"
        )
        return
    
    # Проверяем, получал ли бонус сегодня
    last = user.get('last_daily_bonus')
    if last:
        last_date = datetime.fromisoformat(last).date()
        today = datetime.now().date()
        if last_date == today:
            await msg.reply("⏰ Ты уже получил ежедневный бонус сегодня!")
            return
    
    # Выдаём бонус
    bonus = random.randint(1, 10)
    db.add_balance(msg.from_user.id, bonus, 'daily_bonus')
    user['last_daily_bonus'] = datetime.now().isoformat()
    db.save()
    
    await msg.reply(f"🎁 ЕЖЕДНЕВНЫЙ БОНУС!\n✅ +{bonus} ⭐\n💰 Баланс: {db.get_balance(msg.from_user.id)} ⭐")

# --- ИСТОРИЯ ИГР ---
async def show_game_history(msg: Message, page: int = 0):
    user = db.get_user(msg.from_user.id)
    history = user.get('game_history', [])
    
    if not history:
        await msg.reply("📭 У тебя пока нет истории игр")
        return
    
    items_per_page = 5
    total_pages = (len(history) + items_per_page - 1) // items_per_page
    
    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0
    
    start = page * items_per_page
    end = min(start + items_per_page, len(history))
    current = history[start:end]
    
    text = f"📜 ИСТОРИЯ ИГР\nСтраница {page + 1}/{total_pages}\n\n"
    
    for i, game in enumerate(current, start + 1):
        result_emoji = "✅" if game['result'] == 'win' else "❌"
        win_text = f"+{fmt(game['win'])}" if game['result'] == 'win' else f"-{fmt(game['bet'])}"
        date = datetime.fromisoformat(game['date']).strftime("%d.%m %H:%M")
        text += f"{i}. {game['game']} {result_emoji}\n"
        text += f"   💰 {win_text} ⭐ | {date}\n\n"
    
    kb = []
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"history_page_{page-1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="ignore"))
    if page + 1 < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"history_page_{page+1}"))
    if nav_buttons:
        kb.append(nav_buttons)
    
    await msg.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb) if kb else None)

# --- ЧАСОВАЯ РАЗДАЧА ---
async def hourly_distribution():
    while True:
        await asyncio.sleep(3600)
        
        try:
            members = []
            async for member in bot.get_chat_members(GROUP_ID):
                if not member.user.is_bot:
                    members.append(member.user.id)
            
            if len(members) < 2:
                continue
            
            count = random.randint(1, 2)
            winners = random.sample(members, min(count, len(members)))
            
            for winner_id in winners:
                stars = random.randint(5, 10)
                db.add_balance(winner_id, stars, 'hourly_bonus')
                
                try:
                    await bot.send_message(
                        winner_id,
                        f"🎉 Ты получил {stars} ⭐ за активность в группе!"
                    )
                except:
                    pass
                
        except Exception as e:
            logger.error(f"Ошибка в hourly_distribution: {e}")

# ========== АДМИН КОМАНДЫ ==========

# --- ВЫДАТЬ ---
async def admin_give(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    
    parts = msg.text.split()
    if len(parts) >= 3:
        try:
            user_id = int(parts[1])
            amount = int(parts[2])
            reason = ' '.join(parts[3:]) if len(parts) > 3 else 'Без причины'
            
            db.add_balance(user_id, amount, f'admin_give_{reason}')
            user = db.get_user(user_id)
            
            # Логируем
            db.admin_logs.append({
                'timestamp': datetime.now().isoformat(),
                'admin_id': msg.from_user.id,
                'action': 'give',
                'user_id': user_id,
                'amount': amount,
                'reason': reason
            })
            db.save()
            
            await msg.reply(f"✅ Выдано {amount} ⭐ пользователю {user_id}\nПричина: {reason}")
            try:
                await bot.send_message(user_id, f"💰 Администратор выдал вам {amount} ⭐\nПричина: {reason}")
            except:
                pass
            return
        except:
            await msg.reply("❌ выдать [id] [сумма] [причина]")
            return
    
    await msg.reply("❌ выдать [id] [сумма] [причина]")

# --- ЗАБРАТЬ ---
async def admin_take(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    parts = msg.text.split()
    if len(parts) >= 3:
        try:
            user_id = int(parts[1])
            amount = int(parts[2])
            reason = ' '.join(parts[3:]) if len(parts) > 3 else 'Без причины'
            
            user = db.get_user(user_id)
            if user['balance'] < amount:
                await msg.reply(f"❌ У пользователя {user['balance']} ⭐, а вы пытаетесь забрать {amount}")
                return
            
            db.add_balance(user_id, -amount, f'admin_take_{reason}')
            
            db.admin_logs.append({
                'timestamp': datetime.now().isoformat(),
                'admin_id': msg.from_user.id,
                'action': 'take',
                'user_id': user_id,
                'amount': amount,
                'reason': reason
            })
            db.save()
            
            await msg.reply(f"✅ Забрано {amount} ⭐ у пользователя {user_id}\nПричина: {reason}")
            try:
                await bot.send_message(user_id, f"💸 Администратор забрал {amount} ⭐\nПричина: {reason}")
            except:
                pass
            return
        except:
            await msg.reply("❌ забрать [id] [сумма] [причина]")
            return
    
    await msg.reply("❌ забрать [id] [сумма] [причина]")

# --- БАН ---
async def admin_ban(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    parts = msg.text.split()
    if len(parts) >= 2:
        try:
            user_id = int(parts[1])
            reason = ' '.join(parts[2:]) if len(parts) > 2 else 'Без причины'
            
            user = db.get_user(user_id)
            user['banned'] = True
            user['ban_reason'] = reason
            db.save()
            
            db.admin_logs.append({
                'timestamp': datetime.now().isoformat(),
                'admin_id': msg.from_user.id,
                'action': 'ban',
                'user_id': user_id,
                'reason': reason
            })
            db.save()
            
            await msg.reply(f"⛔ Пользователь {user_id} забанен\nПричина: {reason}")
            try:
                await bot.send_message(user_id, f"⛔ Вы забанены!\nПричина: {reason}")
            except:
                pass
            return
        except:
            await msg.reply("❌ бан [id] [причина]")
            return
    
    await msg.reply("❌ бан [id] [причина]")

# --- РАЗБАН ---
async def admin_unban(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    parts = msg.text.split()
    if len(parts) >= 2:
        try:
            user_id = int(parts[1])
            
            user = db.get_user(user_id)
            user['banned'] = False
            user['ban_reason'] = ''
            db.save()
            
            db.admin_logs.append({
                'timestamp': datetime.now().isoformat(),
                'admin_id': msg.from_user.id,
                'action': 'unban',
                'user_id': user_id
            })
            db.save()
            
            await msg.reply(f"✅ Пользователь {user_id} разбанен")
            try:
                await bot.send_message(user_id, f"✅ Вы разбанены!")
            except:
                pass
            return
        except:
            await msg.reply("❌ разбан [id]")
            return
    
    await msg.reply("❌ разбан [id]")

# --- СТАТ ---
async def admin_stats(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    total_users = len(db.users)
    total_balance = sum(u['balance'] for u in db.users.values())
    total_deposits = sum(u['deposit_total'] for u in db.users.values())
    total_withdraws = len(db.withdraw_requests)
    pending_withdraws = len([r for r in db.withdraw_requests if r['status'] == 'pending'])
    
    await msg.reply(
        f"📊 СТАТИСТИКА БОТА\n\n"
        f"👤 Всего пользователей: {total_users}\n"
        f"💰 Общий баланс: {fmt(total_balance)} ⭐\n"
        f"💳 Всего депозитов: {fmt(total_deposits)} ⭐\n"
        f"📤 Заявок на вывод: {total_withdraws}\n"
        f"⏳ Ожидают: {pending_withdraws}\n\n"
        f"📈 Вейджер: x{WAGER_MULTIPLIER}"
    )

# --- ИСТОР [ID] ---
async def admin_user_history(msg: Message, page: int = 0):
    if not is_admin(msg.from_user.id):
        return
    
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("❌ истор [id]")
        return
    
    try:
        user_id = int(parts[1])
    except:
        await msg.reply("❌ Неверный ID")
        return
    
    user = db.get_user(user_id)
    history = user.get('game_history', [])
    
    if not history:
        await msg.reply(f"📭 У пользователя {user_id} нет истории игр")
        return
    
    items_per_page = 5
    total_pages = (len(history) + items_per_page - 1) // items_per_page
    
    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0
    
    start = page * items_per_page
    end = min(start + items_per_page, len(history))
    current = history[start:end]
    
    try:
        chat = await bot.get_chat(user_id)
        name = chat.full_name
    except:
        name = f"ID {user_id}"
    
    text = f"📜 ИСТОРИЯ ИГР ПОЛЬЗОВАТЕЛЯ\n👤 {name}\n🆔 {user_id}\nСтраница {page + 1}/{total_pages}\n\n"
    
    for i, game in enumerate(current, start + 1):
        result_emoji = "✅" if game['result'] == 'win' else "❌"
        win_text = f"+{fmt(game['win'])}" if game['result'] == 'win' else f"-{fmt(game['bet'])}"
        date = datetime.fromisoformat(game['date']).strftime("%d.%m %H:%M")
        text += f"{i}. {game['game']} {result_emoji}\n"
        text += f"   💰 {win_text} ⭐ | {date}\n\n"
    
    kb = []
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_history_page_{user_id}_{page-1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="ignore"))
    if page + 1 < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_history_page_{user_id}_{page+1}"))
    if nav_buttons:
        kb.append(nav_buttons)
    
    await msg.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb) if kb else None)

# --- ПОП [ID] ---
async def admin_deposit_history(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("❌ поп [id]")
        return
    
    try:
        user_id = int(parts[1])
    except:
        await msg.reply("❌ Неверный ID")
        return
    
    user = db.get_user(user_id)
    history = user.get('deposit_history', [])
    
    if not history:
        await msg.reply(f"📭 У пользователя {user_id} нет пополнений")
        return
    
    try:
        chat = await bot.get_chat(user_id)
        name = chat.full_name
    except:
        name = f"ID {user_id}"
    
    text = f"📊 ИСТОРИЯ ПОПОЛНЕНИЙ\n👤 {name}\n🆔 {user_id}\n\n"
    text += f"💰 Всего депозитов: {fmt(user['deposit_total'])} ⭐\n"
    text += f"🎯 Требуется отыграть: {fmt(user['wager_required'])} ⭐\n"
    text += f"📈 Прогресс: {fmt(user['wager_progress'])} ⭐\n\n"
    
    for i, dep in enumerate(history[:20], 1):
        date = datetime.fromisoformat(dep['date']).strftime("%d.%m %H:%M")
        text += f"{i}. +{fmt(dep['amount'])} ⭐ | {date}\n"
        text += f"   📊 Вейджер +{fmt(dep['wager_added'])}\n\n"
    
    await msg.reply(text)

# --- ВЫВ [история выводов] ---
async def admin_withdraw_history(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("❌ выв [id]")
        return
    
    try:
        user_id = int(parts[1])
    except:
        await msg.reply("❌ Неверный ID")
        return
    
    user = db.get_user(user_id)
    history = user.get('withdraw_history', [])
    
    if not history:
        await msg.reply(f"📭 У пользователя {user_id} нет выводов")
        return
    
    try:
        chat = await bot.get_chat(user_id)
        name = chat.full_name
    except:
        name = f"ID {user_id}"
    
    text = f"💳 ИСТОРИЯ ВЫВОДОВ\n👤 {name}\n🆔 {user_id}\n\n"
    
    for i, wd in enumerate(history[:20], 1):
        date = datetime.fromisoformat(wd['date']).strftime("%d.%m %H:%M")
        status_emoji = "✅" if wd['status'] == 'approved' else "❌" if wd['status'] == 'rejected' else "⏳"
        text += f"{i}. {status_emoji} {fmt(wd['amount'])} ⭐ | {date}\n"
        text += f"   Статус: {wd['status']}\n\n"
    
    await msg.reply(text)

# ========== ОБРАБОТЧИКИ CALLBACK ==========

async def callback_handler(cb: CallbackQuery, state: FSMContext):
    data = cb.data
    uid = cb.from_user.id
    
    try:
        # Проверка подписки
        if data == "check_sub":
            not_subscribed = await check_subscription(uid)
            if not not_subscribed:
                await cb.message.edit_text("✅ Вы подписаны на все каналы! Теперь вы можете пользоваться ботом.")
                await cb.answer("✅ Подписка подтверждена!")
            else:
                await cb.message.edit_text(
                    "❌ Вы не подписаны на все каналы!\n\n"
                    "Подпишитесь и нажмите 'Проверить подписку'",
                    reply_markup=get_subscription_keyboard()
                )
                await cb.answer("❌ Вы не подписаны на все каналы", show_alert=True)
            return
        
        # Пополнение
        if data.startswith("buy_"):
            amount = int(data.split('_')[1])
            await process_deposit(uid, amount)
            await cb.message.edit_text(f"✅ Пополнено: +{amount} ⭐\n💰 Баланс: {db.get_balance(uid)} ⭐")
            await cb.answer("✅ Баланс пополнен!")
            return
        
        # Вейджер
        if data == "wager_info":
            await show_wager_info(cb.message)
            await cb.answer()
            return
        
        # Вывод
        if data == "withdraw":
            await start_withdraw(cb.message, state)
            await cb.answer()
            return
        
        if data == "withdraw_gift":
            await withdraw_gift(cb.message, state)
            await cb.answer()
            return
        
        if data == "withdraw_balance":
            await withdraw_balance_start(cb.message, state)
            await cb.answer()
            return
        
        if data == "withdraw_cancel":
            await state.clear()
            await cb.message.edit_text("❌ Вывод отменён")
            await cb.answer()
            return
        
        if data.startswith("gift_"):
            gift_id = data.split('_')[1]
            # Теперь нам нужен словарь, где ключ - это ID для кнопки,
            # а значение - реальный gift_id из Telegram
            gifts = {
                '1': 'gift_real_id_1', # Замените на реальные ID
                '2': 'gift_real_id_2',
                '3': 'gift_real_id_3',
                '4': 'gift_real_id_4'
            }
            real_gift_id = gifts.get(gift_id)
    
            if db.get_withdrawable_balance(uid) < 15: # Можно проверять по цене подарка
                await cb.answer("❌ Недостаточно средств!", show_alert=True)
                return
        
            # Отправляем подарок
            result = await send_gift_to_user(BOT_TOKEN, uid, real_gift_id, "Спасибо за игру в нашем боте!")
    
            if result.get('ok'):
                # Списать звёзды, только если подарок успешно отправлен
                db.add_balance(uid, -15, 'gift_withdraw') 
                await cb.message.edit_text("🎁 Подарок успешно отправлен!")
            else:
                await cb.message.edit_text(f"❌ Ошибка при отправке подарка: {result.get('description')}")
                
        # Одобрение/отклонение заявок
        if data.startswith("approve_"):
            if not is_admin(uid):
                await cb.answer("❌ Нет прав", show_alert=True)
                return
            
            req_id = int(data.split('_')[1])
            for req in db.withdraw_requests:
                if req['id'] == req_id and req['status'] == 'pending':
                    req['status'] = 'approved'
                    db.save()
                    
                    db.add_balance(req['user_id'], -req['amount'], 'withdraw_approved')
                    user = db.get_user(req['user_id'])
                    user['withdraw_history'].append({
                        'amount': req['amount'],
                        'date': datetime.now().isoformat(),
                        'status': 'approved'
                    })
                    db.save()
                    
                    await cb.message.edit_text(
                        f"✅ Заявка #{req_id} одобрена!\n"
                        f"👤 ID: {req['user_id']}\n"
                        f"💰 Сумма: {req['amount']} ⭐"
                    )
                    
                    try:
                        await bot.send_message(
                            req['user_id'],
                            f"✅ Ваша заявка на вывод #{req_id} одобрена!\n"
                            f"💰 Сумма: {req['amount']} ⭐"
                        )
                    except:
                        pass
                    
                    await cb.answer("✅ Одобрено")
                    return
            
            await cb.answer("❌ Заявка не найдена", show_alert=True)
            return
        
        if data.startswith("reject_"):
            if not is_admin(uid):
                await cb.answer("❌ Нет прав", show_alert=True)
                return
            
            req_id = int(data.split('_')[1])
            for req in db.withdraw_requests:
                if req['id'] == req_id and req['status'] == 'pending':
                    req['status'] = 'rejected'
                    db.save()
                    
                    user = db.get_user(req['user_id'])
                    user['withdraw_history'].append({
                        'amount': req['amount'],
                        'date': datetime.now().isoformat(),
                        'status': 'rejected'
                    })
                    db.save()
                    
                    await cb.message.edit_text(
                        f"❌ Заявка #{req_id} отклонена!\n"
                        f"👤 ID: {req['user_id']}\n"
                        f"💰 Сумма: {req['amount']} ⭐"
                    )
                    
                    try:
                        await bot.send_message(
                            req['user_id'],
                            f"❌ Ваша заявка на вывод #{req_id} отклонена."
                        )
                    except:
                        pass
                    
                    await cb.answer("❌ Отклонено")
                    return
            
            await cb.answer("❌ Заявка не найдена", show_alert=True)
            return
        
        # История игр (пагинация)
        if data.startswith("history_page_"):
            page = int(data.split('_')[2])
            await show_game_history(cb.message, page)
            await cb.answer()
            return
        
        # Админ история (пагинация)
        if data.startswith("admin_history_page_"):
            parts = data.split('_')
            user_id = int(parts[3])
            page = int(parts[4])
            
            # Создаём фейк-сообщение для админской истории
            class FakeMessage:
                def __init__(self, text):
                    self.text = text
                    self.reply = None
                    self.from_user = cb.from_user
                    self.chat = cb.message.chat
            
            fake_msg = FakeMessage(f"истор {user_id}")
            # Передаём фейк-сообщение, но обрабатываем страницу отдельно
            user = db.get_user(user_id)
            history = user.get('game_history', [])
            
            if not history:
                await cb.message.edit_text(f"📭 У пользователя {user_id} нет истории игр")
                await cb.answer()
                return
            
            items_per_page = 5
            total_pages = (len(history) + items_per_page - 1) // items_per_page
            
            if page >= total_pages:
                page = total_pages - 1
            if page < 0:
                page = 0
            
            start = page * items_per_page
            end = min(start + items_per_page, len(history))
            current = history[start:end]
            
            try:
                chat = await bot.get_chat(user_id)
                name = chat.full_name
            except:
                name = f"ID {user_id}"
            
            text = f"📜 ИСТОРИЯ ИГР ПОЛЬЗОВАТЕЛЯ\n👤 {name}\n🆔 {user_id}\nСтраница {page + 1}/{total_pages}\n\n"
            
            for i, game in enumerate(current, start + 1):
                result_emoji = "✅" if game['result'] == 'win' else "❌"
                win_text = f"+{fmt(game['win'])}" if game['result'] == 'win' else f"-{fmt(game['bet'])}"
                date = datetime.fromisoformat(game['date']).strftime("%d.%m %H:%M")
                text += f"{i}. {game['game']} {result_emoji}\n"
                text += f"   💰 {win_text} ⭐ | {date}\n\n"
            
            kb = []
            nav_buttons = []
            if page > 0:
                nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_history_page_{user_id}_{page-1}"))
            nav_buttons.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="ignore"))
            if page + 1 < total_pages:
                nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_history_page_{user_id}_{page+1}"))
            if nav_buttons:
                kb.append(nav_buttons)
            
            await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb) if kb else None)
            await cb.answer()
            return
        
        # Игры (мины, башня, пирамида)
        if data.startswith("mines_"):
            parts = data.split('_')
            user_id, r, c = int(parts[1]), int(parts[2]), int(parts[3])
            if uid != user_id:
                await cb.answer("❌ Не твоя игра", show_alert=True)
                return
            
            res = mines_game.open(user_id, r, c)
            if res['ok']:
                if res.get('over'):
                    if res.get('win'):
                        await cb.message.edit_text(
                            f"🏆 Победа!\n"
                            f"+{fmt(res['won'])} ⭐\n"
                            f"📈 x{res['mult']:.2f}\n"
                            f"🎯 {res['opened']} клеток\n"
                            f"💰 {fmt(res['balance'])} ⭐",
                            reply_markup=mines_game.get_kb(user_id, res['field'], False)
                        )
                    else:
                        await cb.message.edit_text(
                            f"💥 Бум!\n"
                            f"❌ -{fmt(res['bet'])} ⭐\n"
                            f"🎯 {res['opened']} клеток",
                            reply_markup=mines_game.get_kb(user_id, res['field'], False)
                        )
                else:
                    g = mines_game.games.get(user_id)
                    if g:
                        await cb.message.edit_text(
                            f"💣 МИНЫ | 💣 {g['count']} мин\n"
                            f"💰 Ставка: {fmt(g['bet'])} ⭐\n"
                            f"🎯 {res['opened']}/{res['max']} | 📈 x{res['mult']:.2f}\n"
                            f"💎 {fmt(res['won'])} ⭐\n\n"
                            f"Клетка:",
                            reply_markup=mines_game.get_kb(user_id, res['field'])
                        )
            await cb.answer()
            return
        
        if data.startswith("cashout_"):
            user_id = int(data.split('_')[1])
            if uid != user_id:
                await cb.answer("❌ Не твоя игра", show_alert=True)
                return
            
            res = mines_game.cashout(user_id)
            if res['ok']:
                await cb.message.edit_text(
                    f"🏆 Выигрыш +{fmt(res['won'])} ⭐\n"
                    f"🎯 {res['opened']} клеток | 📈 x{res['mult']:.2f}\n"
                    f"💰 {fmt(res['balance'])} ⭐",
                    reply_markup=mines_game.get_kb(user_id, res['field'], False)
                )
            await cb.answer()
            return
        
        if data.startswith("tower_"):
            if data.startswith("tower_cash_"):
                user_id = int(data.split('_')[2])
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
                
                res = tower_game.cashout(user_id)
                if res['ok']:
                    await cb.message.edit_text(
                        f"🏆 Победа +{fmt(res['won'])} ⭐\n"
                        f"📈 x{res['mult']:.1f}\n"
                        f"🎯 {res['rows']}/9\n"
                        f"💰 {fmt(res['balance'])} ⭐"
                    )
                await cb.answer()
                return
            
            parts = data.split('_')
            user_id, r, c = int(parts[1]), int(parts[2]), int(parts[3])
            if uid != user_id:
                await cb.answer("❌ Не твоя игра", show_alert=True)
                return
            
            res = tower_game.open(user_id, r, c)
            if res['ok']:
                if res.get('over'):
                    if res.get('mine'):
                        await cb.message.edit_text(
                            f"💥 Бум! Ты наступил на мину!\n"
                            f"❌ -{fmt(res['bet'])} ⭐"
                        )
                    else:
                        await cb.message.edit_text(
                            f"🏆 МАКСИМУМ! Башня пройдена!\n"
                            f"+{fmt(res['won'])} ⭐\n"
                            f"📈 x{res['mult']:.1f}\n"
                            f"🎯 {res['rows']}/9\n"
                            f"💰 {fmt(res['balance'])} ⭐"
                        )
                else:
                    g = tower_game.games.get(user_id)
                    if g:
                        await cb.message.edit_text(
                            f"🏗️ БАШНЯ | {g['row']+1}/9 | 💣 {g['mines']} мин\n"
                            f"💰 Ставка: {fmt(g['bet'])} ⭐\n"
                            f"📈 x{res['mult']:.1f}\n"
                            f"💎 {fmt(res['won'])} ⭐\n\n"
                            f"Выбери клетку:",
                            reply_markup=tower_game.get_kb(user_id, g)
                        )
            await cb.answer()
            return
        
        if data.startswith("pyramid_"):
            if data.startswith("pyramid_cash_"):
                user_id = int(data.split('_')[2])
                if uid != user_id:
                    await cb.answer("❌ Не твоя игра", show_alert=True)
                    return
                
                res = pyramid_game.cashout(user_id)
                if res['ok']:
                    await cb.message.edit_text(
                        f"🏆 Победа +{fmt(res['won'])} ⭐\n"
                        f"📈 x{res['mult']:.1f}\n"
                        f"🎯 {res['level']}/12\n"
                        f"💰 {fmt(res['balance'])} ⭐"
                    )
                await cb.answer()
                return
            
            parts = data.split('_')
            user_id, level, cell = int(parts[1]), int(parts[2]), int(parts[3])
            if uid != user_id:
                await cb.answer("❌ Не твоя игра", show_alert=True)
                return
            
            res = pyramid_game.open(user_id, level, cell)
            if res['ok']:
                if res.get('over'):
                    if res.get('win'):
                        await cb.message.edit_text(
                            f"🏆 МАКСИМУМ!\n"
                            f"+{fmt(res['won'])} ⭐\n"
                            f"📈 x{res['mult']:.2f}\n"
                            f"🎯 {res['level']}/12\n"
                            f"💰 {fmt(res['balance'])} ⭐"
                        )
                    else:
                        await cb.message.edit_text(
                            f"💥 Ловушка!\n"
                            f"❌ -{fmt(res['bet'])} ⭐\n"
                            f"🎯 {res['level']}/12"
                        )
                else:
                    g = pyramid_game.games.get(user_id)
                    if g:
                        await cb.message.edit_text(
                            f"🔺 ПИРАМИДА | {g['level']+1}/12 | 🚪 {g['doors']} двери\n"
                            f"💰 Ставка: {fmt(g['bet'])} ⭐\n"
                            f"📈 x{res['mult']:.2f}\n"
                            f"💎 {fmt(res['won'])} ⭐\n\n"
                            f"Выбери дверь:",
                            reply_markup=pyramid_game.get_kb(user_id, g)
                        )
            await cb.answer()
            return
        
        # Игровые кнопки с выбором
        if data.startswith("fb_"):
            parts = data.split('_')
            bet, choice = int(parts[1]), parts[2]
            res = await play_football(cb.message, uid, bet, choice)
            if res['ok']:
                if res['win']:
                    await cb.message.edit_text(
                        f"⚽ Футбол\n"
                        f"{'⚽ ГОЛ!' if res['is_goal'] else '🥅 МИМО!'} [{res['value']}]\n"
                        f"✅ +{fmt(res['amount'])} ⭐\n"
                        f"💰 {fmt(res['balance'])} ⭐"
                    )
                else:
                    await cb.message.edit_text(
                        f"⚽ Футбол\n"
                        f"{'⚽ ГОЛ!' if res['is_goal'] else '🥅 МИМО!'} [{res['value']}]\n"
                        f"❌ -{fmt(res['amount'])} ⭐\n"
                        f"💰 {fmt(res['balance'])} ⭐"
                    )
            await cb.answer()
            return
        
        if data.startswith("bb_"):
            parts = data.split('_')
            bet, choice = int(parts[1]), parts[2]
            res = await play_basketball(cb.message, uid, bet, choice)
            if res['ok']:
                if res['win']:
                    await cb.message.edit_text(
                        f"🏀 Баскетбол\n"
                        f"{'🏀 ГОЛ!' if res['is_goal'] else '🧺 МИМО!'} [{res['value']}]\n"
                        f"✅ +{fmt(res['amount'])} ⭐\n"
                        f"💰 {fmt(res['balance'])} ⭐"
                    )
                else:
                    await cb.message.edit_text(
                        f"🏀 Баскетбол\n"
                        f"{'🏀 ГОЛ!' if res['is_goal'] else '🧺 МИМО!'} [{res['value']}]\n"
                        f"❌ -{fmt(res['amount'])} ⭐\n"
                        f"💰 {fmt(res['balance'])} ⭐"
                    )
            await cb.answer()
            return
        
        if data.startswith("dart_"):
            parts = data.split('_')
            bet, choice = int(parts[1]), parts[2]
            res = await play_dart(cb.message, uid, bet, choice)
            if res['ok']:
                sector = res['sector']
                if res['win']:
                    await cb.message.edit_text(
                        f"🎯 ДАРТС\n"
                        f"{sector['emoji']} {sector['name']}! [{res['value']}] x{sector['mult']}\n"
                        f"✅ +{fmt(res['amount'])} ⭐\n"
                        f"💰 {fmt(res['balance'])} ⭐"
                    )
                else:
                    await cb.message.edit_text(
                        f"🎯 ДАРТС\n"
                        f"{sector['emoji']} {sector['name']}! [{res['value']}]\n"
                        f"❌ -{fmt(res['amount'])} ⭐\n"
                        f"💰 {fmt(res['balance'])} ⭐"
                    )
            await cb.answer()
            return
        
        await cb.answer()
        
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await cb.answer("❌ Ошибка", show_alert=True)

# ========== ОБРАБОТЧИК ТЕКСТОВЫХ КОМАНД ==========

async def handle_text(msg: Message, state: FSMContext):
    text = msg.text.lower().strip()
    uid = msg.from_user.id
    
    # Проверка бана
    user = db.get_user(uid)
    if user.get('banned', False):
        await msg.reply(f"⛔ Вы забанены!\nПричина: {user.get('ban_reason', 'Не указана')}")
        return
    
    # Проверка подписки
    not_subscribed = await check_subscription(uid)
    if not_subscribed:
        await msg.reply(
            "❌ Для использования бота необходимо подписаться на каналы!",
            reply_markup=get_subscription_keyboard()
        )
        return
    
    # Состояния вывода
    current_state = await state.get_state()
    if current_state == WithdrawStates.entering_amount:
        await process_withdraw_amount(msg, state)
        return
    
    # Команда "б" - баланс
    if text == "б" or text == "баланс":
        await show_balance(msg)
        return
    
    # Ежедневный бонус
    if text in ["ежед", "ежедневный", "ежедневный бонус"]:
        await daily_bonus(msg)
        return
    
    # Пополнение
    if text == "пополнить":
        await deposit_stars(msg)
        return
    
    # Вывод
    if text == "вывод":
        await start_withdraw(msg, state)
        return
    
    # История игр
    if text == "история" or text == "истор":
        await show_game_history(msg, 0)
        return
    
    # Вейджер
    if text == "вейджер" or text == "вж":
        await show_wager_info(msg)
        return
    
    # АДМИН КОМАНДЫ
    if text.startswith("выдать "):
        await admin_give(msg, state)
        return
    
    if text.startswith("забрать "):
        await admin_take(msg)
        return
    
    if text.startswith("бан "):
        await admin_ban(msg)
        return
    
    if text.startswith("разбан "):
        await admin_unban(msg)
        return
    
    if text == "стат":
        await admin_stats(msg)
        return
    
    if text.startswith("истор "):
        await admin_user_history(msg, 0)
        return
    
    if text.startswith("поп "):
        await admin_deposit_history(msg)
        return
    
    if text.startswith("выв "):
        await admin_withdraw_history(msg)
        return
    
    # ИГРЫ
    
    # Слоты
    if text.startswith("слоты "):
        parts = text.split()
        if len(parts) > 1:
            try:
                bet = int(parts[1])
                res = await play_slots(msg, uid, bet)
                if res['ok']:
                    if res['win']:
                        await msg.reply(f"🎰 x{res['mult']}\n✅ +{fmt(res['amount'])} ⭐\n💰 {fmt(res['balance'])} ⭐")
                    else:
                        await msg.reply(f"🎰 {res['value']}\n❌ -{fmt(res['amount'])} ⭐\n💰 {fmt(res['balance'])} ⭐")
                else:
                    await msg.reply(res['msg'])
            except:
                await msg.reply("❌ Неверная ставка")
        return
    
    # Футбол
    if text.startswith("футбол "):
        parts = text.split()
        if len(parts) >= 3:
            try:
                bet = int(parts[1])
                choice = parts[2].lower()
                if choice not in ['гол', 'мимо']:
                    await msg.reply("❌ Выбери: гол или мимо")
                    return
                
                res = await play_football(msg, uid, bet, choice)
                if res['ok']:
                    if res['win']:
                        await msg.reply(f"⚽ {'ГОЛ!' if res['is_goal'] else 'МИМО!'}\n✅ +{fmt(res['amount'])} ⭐\n💰 {fmt(res['balance'])} ⭐")
                    else:
                        await msg.reply(f"⚽ {'ГОЛ!' if res['is_goal'] else 'МИМО!'}\n❌ -{fmt(res['amount'])} ⭐\n💰 {fmt(res['balance'])} ⭐")
                else:
                    await msg.reply(res['msg'])
            except:
                await msg.reply("❌ Неверная ставка")
        else:
            bet = 100
            if len(parts) > 1:
                try:
                    bet = int(parts[1])
                except:
                    pass
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚽ ГОЛ", callback_data=f"fb_{bet}_гол"),
                 InlineKeyboardButton(text="🥅 МИМО", callback_data=f"fb_{bet}_мимо")]
            ])
            await msg.reply(f"⚽ Футбол\n💰 Ставка: {bet} ⭐\nВыбери:", reply_markup=kb)
        return
    
    # Баскетбол
    if text.startswith("баскетбол "):
        parts = text.split()
        if len(parts) >= 3:
            try:
                bet = int(parts[1])
                choice = parts[2].lower()
                if choice not in ['гол', 'мимо']:
                    await msg.reply("❌ Выбери: гол или мимо")
                    return
                
                res = await play_basketball(msg, uid, bet, choice)
                if res['ok']:
                    if res['win']:
                        await msg.reply(f"🏀 {'ГОЛ!' if res['is_goal'] else 'МИМО!'}\n✅ +{fmt(res['amount'])} ⭐\n💰 {fmt(res['balance'])} ⭐")
                    else:
                        await msg.reply(f"🏀 {'ГОЛ!' if res['is_goal'] else 'МИМО!'}\n❌ -{fmt(res['amount'])} ⭐\n💰 {fmt(res['balance'])} ⭐")
                else:
                    await msg.reply(res['msg'])
            except:
                await msg.reply("❌ Неверная ставка")
        else:
            bet = 100
            if len(parts) > 1:
                try:
                    bet = int(parts[1])
                except:
                    pass
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏀 ГОЛ", callback_data=f"bb_{bet}_гол"),
                 InlineKeyboardButton(text="🧺 МИМО", callback_data=f"bb_{bet}_мимо")]
            ])
            await msg.reply(f"🏀 Баскетбол\n💰 Ставка: {bet} ⭐\nВыбери:", reply_markup=kb)
        return
    
    # Дартс
    if text.startswith("дартс ") or text.startswith("дс "):
        prefix = "дартс " if text.startswith("дартс ") else "дс "
        parts = text[len(prefix):].split()
        if len(parts) >= 2:
            try:
                bet = int(parts[0])
                choice = parts[1].lower()
                if choice not in ['ц', 'к', 'б', 'м']:
                    await msg.reply("❌ Выбери: ц (центр), к (красное), б (белое), м (мимо)")
                    return
                
                res = await play_dart(msg, uid, bet, choice)
                if res['ok']:
                    sector = res['sector']
                    if res['win']:
                        await msg.reply(f"🎯 {sector['emoji']} {sector['name']}! x{sector['mult']}\n✅ +{fmt(res['amount'])} ⭐\n💰 {fmt(res['balance'])} ⭐")
                    else:
                        await msg.reply(f"🎯 {sector['emoji']} {sector['name']}!\n❌ -{fmt(res['amount'])} ⭐\n💰 {fmt(res['balance'])} ⭐")
                else:
                    await msg.reply(res['msg'])
            except:
                await msg.reply("❌ Неверная ставка")
        else:
            bet = 100
            if len(parts) > 0:
                try:
                    bet = int(parts[0])
                except:
                    pass
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎯 ЦЕНТР", callback_data=f"dart_{bet}_ц"),
                 InlineKeyboardButton(text="🔴 КРАСНОЕ", callback_data=f"dart_{bet}_к")],
                [InlineKeyboardButton(text="⚪ БЕЛОЕ", callback_data=f"dart_{bet}_б"),
                 InlineKeyboardButton(text="💢 МИМО", callback_data=f"dart_{bet}_м")]
            ])
            await msg.reply(f"🎯 ДАРТС\n💰 Ставка: {bet} ⭐\nВыбери цель:", reply_markup=kb)
        return
    
    # Мины
    if text.startswith("мины "):
        parts = text.split()
        if len(parts) >= 2:
            try:
                bet = int(parts[1])
                mines = int(parts[2]) if len(parts) > 2 else 1
                
                res = mines_game.start(uid, bet, mines)
                if res['ok']:
                    await msg.reply(
                        f"💣 МИНЫ | 💣 {res['data']['count']} мин\n"
                        f"💰 Ставка: {fmt(bet)} ⭐\n"
                        f"📈 Множитель: x1.0\n\n"
                        f"Открывай клетки!",
                        reply_markup=mines_game.get_kb(uid, res['data']['field'])
                    )
                else:
                    await msg.reply(res['msg'])
            except:
                await msg.reply("❌ Неверная ставка или количество мин")
        return
    
    # Башня
    if text.startswith("башня "):
        parts = text.split()
        if len(parts) >= 2:
            try:
                bet = int(parts[1])
                mines = int(parts[2]) if len(parts) > 2 else 1
                
                res = tower_game.start(uid, bet, mines)
                if res['ok']:
                    g = res['data']
                    mults = tower_game.mults(g['mines'])
                    max_mult = mults[-1]
                    await msg.reply(
                        f"🏗️ БАШНЯ | 1/9 | 💣 {g['mines']} мин\n"
                        f"💰 Ставка: {fmt(bet)} ⭐\n"
                        f"📈 Макс: x{max_mult}\n\n"
                        f"Выбери клетку:",
                        reply_markup=tower_game.get_kb(uid, g)
                    )
                else:
                    await msg.reply(res['msg'])
            except:
                await msg.reply("❌ Неверная ставка или количество мин")
        return
    
    # Пирамида
    if text.startswith("пирамида "):
        parts = text.split()
        if len(parts) >= 2:
            try:
                bet = int(parts[1])
                doors = int(parts[2]) if len(parts) > 2 else 3
                
                res = pyramid_game.start(uid, bet, doors)
                if res['ok']:
                    g = res['data']
                    mults = pyramid_game.multipliers[g['doors']]
                    max_mult = mults[-1]
                    await msg.reply(
                        f"🔺 ПИРАМИДА | 1/12 | 🚪 {g['doors']} двери\n"
                        f"💰 Ставка: {fmt(bet)} ⭐\n"
                        f"📈 Макс: x{max_mult}\n\n"
                        f"Выбери дверь:",
                        reply_markup=pyramid_game.get_kb(uid, g)
                    )
                else:
                    await msg.reply(res['msg'])
            except:
                await msg.reply("❌ Неверная ставка или количество дверей")
        return
    
    # Отмена игры
    if text in ["отмена", "отменить"]:
        cancelled = False
        
        if uid in mines_game.games:
            res = mines_game.cancel(uid)
            if res['ok']:
                await msg.reply(res['msg'])
                cancelled = True
        
        if uid in tower_game.games:
            res = tower_game.cancel(uid)
            if res['ok']:
                await msg.reply(res['msg'])
                cancelled = True
        
        if uid in pyramid_game.games:
            res = pyramid_game.cancel(uid)
            if res['ok']:
                await msg.reply(res['msg'])
                cancelled = True
        
        if not cancelled:
            await msg.reply("❌ Нет активной игры")
        return
    
    # Помощь
    if text in ["помощь", "help", "команды"]:
        await msg.reply(
            "🌟 ИГРОВОЙ БОТ 🌟\n\n"
            "💰 Баланс:\n"
            "б - показать баланс\n"
            "пополнить - пополнить баланс\n"
            "вывод - вывести средства\n"
            "вейджер - информация о вейджере\n\n"
            "🎁 Бонусы:\n"
            "ежед - ежедневный бонус (требует @ADAMSTARRBOT в описании)\n\n"
            "📜 История:\n"
            "история - история игр\n\n"
            "🎮 Игры:\n"
            "слоты [ставка]\n"
            "футбол [ставка] [гол/мимо]\n"
            "баскетбол [ставка] [гол/мимо]\n"
            "дартс [ставка] [ц/к/б/м]\n"
            "мины [ставка] [мин]\n"
            "башня [ставка] [мин]\n"
            "пирамида [ставка] [дверей]\n\n"
            "отмена - отменить текущую игру"
        )
        return

# ========== ЗАПУСК БОТА ==========

async def main():
    # Регистрируем обработчики
    dp.message.register(handle_text)
    dp.callback_query.register(callback_handler)
    
    # Запускаем фоновую задачу для часовой раздачи
    asyncio.create_task(hourly_distribution())
    
    print("🌟 Бот запущен!")
    print("✅ Команда 'б' - баланс")
    print("✅ 'ежед' - ежедневный бонус")
    print("✅ 'вывод' - вывод средств")
    print("✅ 'пополнить' - пополнение")
    print("✅ 'история' - история игр")
    print("✅ 'вейджер' - информация о вейджере")
    print("✅ Игры: слоты, футбол, баскетбол, дартс, мины, башня, пирамида")
    print("✅ Часовая раздача звёзд в группе")
    print("✅ Админ команды: выдать, забрать, бан, разбан, стат, истор, поп, выв")
    print(f"✅ Вейджер: x{WAGER_MULTIPLIER}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n❌ Бот остановлен")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
