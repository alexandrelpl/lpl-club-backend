import functions_framework
from PIL import Image, ImageDraw, ImageFont
import io
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import make_response

# --- 1. CHARGEMENT EN MÉMOIRE GLOBALE (Instantané au démarrage) ---
width, height = 800, 120
bg_color = (0, 0, 0)
text_color = (255, 255, 255)

try:
    font_title = ImageFont.truetype("font_bold.ttf", 36) 
    font_subtitle = ImageFont.truetype("font_regular.ttf", 20) 
    font_time = ImageFont.truetype("font_bold.ttf", 38) 
    font_label = ImageFont.truetype("font_regular.ttf", 16) 
except:
    font_title = font_subtitle = font_time = font_label = ImageFont.load_default()

# --- 2. PRÉ-RENDU DU FOND (Hack ultime de performance) ---
# On dessine le texte de gauche une seule fois pour tout le monde
STATIC_BASE_IMG = Image.new('RGB', (width, height), bg_color)
draw_static = ImageDraw.Draw(STATIC_BASE_IMG)
draw_static.text((60, 64), "1 ACHETÉE = 1 OFFERTE", fill=text_color, font=font_title, anchor="ls", stroke_width=0)
draw_static.text((60, 92), "Expédié demain si commandé avant minuit :", fill=text_color, font=font_subtitle, anchor="ls", stroke_width=0)


def get_midnight_deadline():
    tz_paris = ZoneInfo("Europe/Paris")
    now = datetime.now(tz_paris)
    tomorrow = now.date() + timedelta(days=1)
    next_midnight = datetime(
        year=tomorrow.year, month=tomorrow.month, day=tomorrow.day,
        hour=0, minute=0, second=0, tzinfo=tz_paris
    )
    return int(next_midnight.timestamp())


@functions_framework.http
def generate_timer(request):
    deadline = get_midnight_deadline()
    now = int(time.time())
    
    frames = []
    
    # --- 3. SEULEMENT 15 FRAMES (Divise le temps et le poids par 4) ---
    for i in range(15): 
        remaining = max(0, deadline - (now + i))
        
        h = remaining // 3600
        m = (remaining % 3600) // 60
        s = remaining % 60

        # Au lieu de créer une image vide, on copie l'image pré-dessinée (Instantané !)
        img = STATIC_BASE_IMG.copy()
        draw = ImageDraw.Draw(img)
        
        # On ne dessine PLUS QUE les chiffres qui changent
        positions = [
            (560, f"{h:02d}", "HRS"),
            (640, f"{m:02d}", "MINS"),
            (720, f"{s:02d}", "SECS")
        ]

        for x_pos, val, label in positions:
            draw.text((x_pos, 64), val, fill=text_color, font=font_time, anchor="ms", stroke_width=0)
            draw.text((x_pos, 92), label, fill=text_color, font=font_label, anchor="ms", stroke_width=0)

        # Limitation drastique des couleurs pour compresser à fond
        frames.append(img.quantize(colors=8))

    # Export
    buf = io.BytesIO()
    frames[0].save(
        buf, format='GIF', save_all=True, append_images=frames[1:], 
        duration=1000, loop=0, optimize=True
    )
    
    response = make_response(buf.getvalue())
    response.headers.set('Content-Type', 'image/gif')
    response.headers.set('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
    return response