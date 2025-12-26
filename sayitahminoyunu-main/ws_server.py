import asyncio
import random
import time
from collections import Counter
import websockets
import sqlite3

# -----------------------------
# Veritabanı İşlemleri
# -----------------------------

def init_db():
    conn = sqlite3.connect("game_data.db")
    cursor = conn.cursor()
    # Ad (username) Primary Key, skor ise integer
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            username TEXT PRIMARY KEY,
            high_score INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def db_add_player(username):
    conn = sqlite3.connect("game_data.db")
    cursor = conn.cursor()
    # Eğer kullanıcı yoksa ekle, varsa görmezden gel (IGNORE)
    cursor.execute("INSERT OR IGNORE INTO players (username, high_score) VALUES (?, 0)", (username,))
    conn.commit()
    conn.close()

def db_update_score(username, score):
    conn = sqlite3.connect("game_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE players 
        SET high_score = ? 
        WHERE username = ? AND ? > high_score
    """, (score, username, score))
    print(f"[DB DEBUG] {username} için skor güncellendi: {score}. Etkilenen satır: {cursor.rowcount}")
    conn.commit()
    conn.close()

def db_get_top_scores(limit=5):
    conn = sqlite3.connect("game_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username, high_score FROM players ORDER BY high_score DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows

# -----------------------------
# Yardımcı Fonksiyonlar
# -----------------------------

def plus_minus_counts(target: str, guess: str):
    plus = sum(1 for i in range(len(target)) if i < len(guess) and guess[i] == target[i])
    t_counter = Counter(target)
    g_counter = Counter(guess)
    total_matches = sum(min(t_counter[d], g_counter[d]) for d in t_counter)
    minus = total_matches - plus
    return plus, minus

def compute_score(n_plus, n_minus, elapsed):
    w_plus = 10
    w_minus = 4
    bonus = max(0, 15 - int(elapsed))
    return n_plus * w_plus + n_minus * w_minus + bonus

# -----------------------------
# Global Oyun Durumu
# -----------------------------

connected_players = []  
player_names = {}       
scores = {}             
start_times = {}        
ready_players = set()
target_number = None
digit_count = None
game_active = False

# -----------------------------
# WebSocket Yayın Fonksiyonları
# -----------------------------

async def broadcast(message: str):
    if connected_players:
        await asyncio.gather(*(ws.send(message) for ws in connected_players), return_exceptions=True)

async def send_scores():
    parts = ["SCORES"]
    for ws in connected_players:
        name = player_names.get(ws, "Oyuncu")
        score = scores.get(ws, 0)
        parts.append(f"{name}:{score}")
    await broadcast("|".join(parts))

async def send_leaderboard():
    # Veritabanından gerçek verileri çekiyoruz!
    top_players = db_get_top_scores(5)
    parts = ["LEADERBOARD"]
    for name, score in top_players:
        parts.append(f"{name}:{score}")
    await broadcast("|".join(parts))

async def start_game():
    global target_number, digit_count, game_active
    digit_count = random.randint(3, 5)
    min_val = 10 ** (digit_count - 1)
    max_val = 10 ** digit_count - 1
    target_number = str(random.randint(min_val, max_val))

    print(f"[DEBUG] Yeni sayı: {target_number}")

    for ws in connected_players:
        scores[ws] = 0
        start_times[ws] = time.time()

    game_active = True
    await broadcast("CLEARLOGS")
    await broadcast(f"START|{digit_count}")
    await send_scores()
    await send_leaderboard()

# -----------------------------
# Ana Handler
# -----------------------------
async def handler(websocket):
    global game_active, target_number

    connected_players.append(websocket)
    print("Yeni oyuncu bağlandı.")

    # Oyuncu ismi gelene kadar bekle
    try:
        async for message in websocket:
            print("Gelen mesaj:", message)

            # -------------------------
            # JOIN|username
            # -------------------------
            if message.startswith("JOIN|"):
                username = message.split("|")[1].strip()

                player_names[websocket] = username

                db_add_player(username)
                # Yeni oyuncu skor ve zaman
                scores[websocket] = 0
                start_times[websocket] = time.time()

                print(f"→ Oyuncu adı: {username}")

                if not game_active:
                    if len(connected_players) < 2:
                        await websocket.send("WAIT")
                    else:
                        await start_game()
                else:
                    await websocket.send(f"START|{digit_count}")
                    await send_scores()
                
                # --- YENİ: GİRİŞTE LİDERLİK TABLOSUNU GÖNDER ---
                await send_leaderboard()

                continue

            # -------------------------
            # RESTART
            # -------------------------
            if message == "RESTART":
                # Oyuncuyu hazır listesine ekle
                ready_players.add(websocket)
                await websocket.send("RESTART_CONFIRMED")
                
                # Eğer hazır olanların sayısı, toplam oyuncu sayısına eşitse 
                # VEYA en az 2 kişi hazırsa oyunu başlat
                if len(ready_players) >= 2:
                    ready_players.clear() # Listeyi sıfırla
                    await start_game()
                else:
                    # Diğer oyuncuların da butona basmasını bekliyoruz
                    await broadcast(f"INFO|{player_names[websocket]} hazır! Diğerleri bekleniyor...")
                    await websocket.send("WAIT")
                continue
            # -------------------------
            # Tahmin mesajı
            # -------------------------
            if game_active:
                tahmin = message.strip()
                now = time.time()
                elapsed = now - start_times.get(websocket, now)
                start_times[websocket] = now

                plus, minus = plus_minus_counts(target_number, tahmin)
                skor = compute_score(plus, minus, elapsed)
                scores[websocket] += skor

                total = scores[websocket]

                # Tahmini yapan oyuncuya RESULT gönder
                await websocket.send(
                    f"RESULT|{tahmin}|{plus}|{minus}|{skor}|{total}"
                )

                # Herkese güncel skorları gönder
                await send_scores()
                
                # -------------------------
                # Doğru tahmin → oyun biter
                # -------------------------

                if tahmin == target_number:
                    game_active = False
                    winner_name = player_names.get(websocket, "Oyuncu")

                    # --- YENİ: SKORLARI DB'YE KAYDET ---
                    for ws, name in player_names.items():
                        final_score = scores.get(ws, 0)
                        db_update_score(name, final_score)
                    # ----------------------------------

                    await broadcast(f"GAMEOVER|{target_number}|{winner_name}")
                    
                    # --- YENİ: GÜNCEL LİDERLİK TABLOSUNU GÖNDER ---
                    await send_leaderboard()
                    # -------- ws_server.py-------------------------------------
                    
                    print(f"📌 GAMEOVER → Kazanan: {winner_name}")

    except websockets.exceptions.ConnectionClosed:
        print("Bir oyuncu ayrıldı.")

    finally:
        if websocket in ready_players:
            ready_players.remove(websocket)
        await send_scores()
        if websocket in connected_players:
            connected_players.remove(websocket)
        player_names.pop(websocket, None)
        scores.pop(websocket,None)
        start_times.pop(websocket, None)

        if len(connected_players) < 2:
            game_active = False

        print("Oyuncu çıktı.")

# -----------------------------
# Başlat
# -----------------------------

async def main():
    init_db() # Veritabanını oluştur
    async with websockets.serve(handler, "172.18.97.42", 5888):
        print("WebSocket + SQLite sunucusu çalışıyor: ws://172.18.97.42:5888")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())