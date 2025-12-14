import asyncio
import random
import time
from collections import Counter
import websockets

# -----------------------------
# Yardımcı Fonksiyonlar
# -----------------------------

def plus_minus_counts(target: str, guess: str):
    plus = sum(
        1 for i in range(len(target))
        if i < len(guess) and guess[i] == target[i]
    )
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

connected_players = []      # websocket listesi
player_names = {}           # websocket -> username
scores = {}                 # websocket -> total score
start_times = {}            # websocket -> last guess time

target_number = None
digit_count = None
game_active = False

# -----------------------------
# Yardımcı Fonksiyonlar
# -----------------------------

async def broadcast(message: str):
    """Tüm oyunculara mesaj gönder."""
    if connected_players:
        await asyncio.gather(*(ws.send(message) for ws in connected_players))

async def send_scores():
    parts = ["SCORES"]
    for ws in connected_players:
        name = player_names.get(ws, "Oyuncu")
        score = scores.get(ws, 0)
        parts.append(f"{name}:{score}")
    await broadcast("|".join(parts))


async def start_game():
    """Yeni oyun başlat."""
    global target_number, digit_count, game_active, scores, start_times

    digit_count = random.randint(3, 5)
    min_val = 10 ** (digit_count - 1)
    max_val = 10 ** digit_count - 1
    target_number = str(random.randint(min_val, max_val))

    print(f"[DEBUG] Yeni sayı: {target_number}")

    for ws in connected_players:
        scores[ws] = 0
        start_times[ws] = time.time()
    game_active = True

    await broadcast(f"START|{digit_count}")
    await send_scores()

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
                username = message.split("|")[1]
                player_names[websocket] = username

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
                    # Oyun devam ediyorsa oyuna dahil et
                    await websocket.send(f"START|{digit_count}")
                    await send_scores()

                continue

            # -------------------------
            # RESTART
            # -------------------------
            if message == "RESTART":
                if len(connected_players) >= 2:
                    await start_game()
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

                    winner_ws = websocket
                    winner_name = player_names.get(websocket, "Oyuncu")

                    await broadcast(f"GAMEOVER|{target_number}|{winner_name}")
                    print("📌 GAMEOVER → Kazanan:", winner_name)
                    print("📌 GAMEOVER MESAJI GÖNDERİLDİ →", message)

    except websockets.exceptions.ConnectionClosed:
        print("Bir oyuncu ayrıldı.")

    finally:
        if websocket in connected_players:
            connected_players.remove(websocket)
        player_names.pop(websocket, None)
        scores.pop(websocket,None)
        start_times.pop(websocket, None)

        if len(connected_players) < 2:
            game_active = False

        await send_scores()
        print("Oyuncu çıktı.")

# -----------------------------
# Sunucuyu Başlat
# -----------------------------

async def main():
    async with websockets.serve(handler, "localhost", 8765):
        print("WebSocket sunucusu çalışıyor: ws://localhost:8765")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
