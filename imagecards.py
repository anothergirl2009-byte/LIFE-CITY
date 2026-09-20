"""
LifeCity Bot - Cartes visuelles style "néon" (fond sombre + halos lumineux)
Inspiré du style de bannière de bienvenue fourni par l'utilisateur.

Fonctions "bas niveau" (tu fournis déjà les objets PIL Image, ou None) :
    generate_marriage_card_neon(name1, name2, date_str, photo1=None, photo2=None)
    generate_friendship_card_neon(name1, name2, date_str, photo1=None, photo2=None)
    generate_family_tree_card_neon(player_name, spouse_name, parents, children, friends,
                                    player_photo=None, spouse_photo=None)

Fonctions "prêtes à l'emploi" (elles vont chercher la vraie photo de profil
Telegram toutes seules à partir du user_id + du bot) :
    await generate_marriage_card_neon_tg(bot, user_id1, name1, user_id2, name2, date_str)
    await generate_friendship_card_neon_tg(bot, user_id1, name1, user_id2, name2, date_str)
    await generate_family_tree_card_neon_tg(bot, player_id, player_name, spouse_id, spouse_name,
                                             parents, children, friends)

Chaque fonction retourne un io.BytesIO (PNG) prêt à être envoyé avec reply_photo/reply_document.

Nécessite : Pillow >= 10.0
"""

import io
import math
import random
from PIL import Image, ImageDraw, ImageFont, ImageFilter


async def _download_profile_photo(user_id: int, bot):
    """
    Télécharge la vraie photo de profil Telegram d'un utilisateur.
    Retourne None si l'utilisateur n'a pas de photo (les fonctions de cartes
    afficheront alors un avatar avec ses initiales à la place).
    """
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos and photos.photos:
            photo = photos.photos[0][-1]  # meilleure qualité disponible
            file = await bot.get_file(photo.file_id)
            photo_bytes = await file.download_as_bytearray()
            return Image.open(io.BytesIO(photo_bytes))
    except Exception:
        pass
    return None

# ============================================================
# POLICES
# ============================================================
_FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
_FONT_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]


def _load_font(candidates, size):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _bold(size):
    return _load_font(_FONT_BOLD, size)


def _regular(size):
    return _load_font(_FONT_REGULAR, size)


def _text_w(draw, text, font):
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0]


# ============================================================
# FOND NÉON
# ============================================================

def _neon_background(W, H, base_top=(8, 6, 30), base_bottom=(3, 3, 15),
                      glow_spots=None, seed=None):
    """
    Fond dégradé sombre + halos lumineux flous (façon ambiance néon de boîte de nuit)
    + petites particules scintillantes.
    glow_spots: liste de (x_ratio, y_ratio, radius, color) pour placer les halos.
    """
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H), base_top)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / max(1, H - 1)
        r = int(base_top[0] + (base_bottom[0] - base_top[0]) * t)
        g = int(base_top[1] + (base_bottom[1] - base_top[1]) * t)
        b = int(base_top[2] + (base_bottom[2] - base_top[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    img = img.convert("RGBA")

    if glow_spots is None:
        glow_spots = [
            (0.12, 0.15, 260, (140, 40, 220)),
            (0.88, 0.12, 220, (60, 90, 240)),
            (0.08, 0.9, 240, (230, 40, 150)),
            (0.92, 0.85, 260, (40, 200, 220)),
        ]

    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow_layer)
    for xr, yr, radius, color in glow_spots:
        cx, cy = int(W * xr), int(H * yr)
        gdraw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(*color, 130),
        )
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=90))
    img = Image.alpha_composite(img, glow_layer)

    # petites particules scintillantes
    sparkle_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(sparkle_layer)
    for _ in range(70):
        x = rng.randint(0, W)
        y = rng.randint(0, H)
        r = rng.choice([1, 1, 1, 2])
        alpha = rng.randint(60, 160)
        sdraw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, alpha))
    img = Image.alpha_composite(img, sparkle_layer)

    return img


def _rounded_frame(img, margin, radius, color, width):
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [margin, margin, img.width - margin, img.height - margin],
        radius=radius, outline=color, width=width,
    )


def _diamond_accent(draw, cx, cy, r, color, alpha=255):
    pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    draw.polygon(pts, outline=(*color, alpha), width=2)


# ============================================================
# TEXTE AVEC EFFET NÉON (GLOW)
# ============================================================

def _glow_text(base_img, cx, y, text, font, glow_color, fill=(255, 255, 255),
               blur=10, glow_alpha=230, passes=2):
    """Dessine un texte centré horizontalement avec halo lumineux derrière."""
    W, H = base_img.size
    tmp_draw = ImageDraw.Draw(base_img)
    w = _text_w(tmp_draw, text, font)
    x = cx - w / 2

    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow_layer)
    for _ in range(passes):
        gdraw.text((x, y), text, font=font, fill=(*glow_color, glow_alpha))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=blur))

    base_img.alpha_composite(glow_layer)
    draw = ImageDraw.Draw(base_img)
    draw.text((x, y), text, font=font, fill=fill)
    return w


# ============================================================
# PHOTO CIRCULAIRE AVEC ANNEAU NÉON
# ============================================================

def _circle_crop(image, size):
    image = image.convert("RGBA")
    ratio = image.width / image.height
    if ratio > 1:
        nw, nh = size, int(size / ratio)
    else:
        nh, nw = size, int(size * ratio)
    image = image.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (image.width - size) // 2
    top = (image.height - size) // 2
    # Recadrage sécurisé si l'image est plus petite que 'size' dans un sens
    image = ImageOpsFit(image, size)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(image, (0, 0))
    out.putalpha(mask)
    return out


def ImageOpsFit(image, size):
    # petite sécurité anti-crop négatif
    if image.width < size or image.height < size:
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        return image
    left = (image.width - size) // 2
    top = (image.height - size) // 2
    return image.crop((left, top, left + size, top + size))


def _default_avatar(size, name, color):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size, size), fill=color)
    initials = (name[:2] if name else "??").upper()
    font = _bold(int(size * 0.36))
    bbox = draw.textbbox((0, 0), initials, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - bbox[1]), initials,
               font=font, fill=(255, 255, 255, 255))
    return img


def _glow_photo(base_img, cx, cy, size, photo, glow_color, ring_color=(255, 255, 255), name=""):
    """Colle une photo (ou avatar par défaut) circulaire avec anneau lumineux néon."""
    W, H = base_img.size
    if photo is not None:
        circle = _circle_crop(photo, size)
    else:
        circle = _default_avatar(size, name, glow_color)

    # halo derrière l'anneau
    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow_layer)
    ring_r = size // 2 + 10
    gdraw.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
                  outline=(*glow_color, 255), width=14)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=12))
    base_img.alpha_composite(glow_layer)

    # photo
    base_img.alpha_composite(circle, (cx - size // 2, cy - size // 2))

    # anneau net par-dessus
    draw = ImageDraw.Draw(base_img)
    draw.ellipse([cx - size // 2 - 4, cy - size // 2 - 4,
                  cx + size // 2 + 4, cy + size // 2 + 4],
                 outline=ring_color, width=4)
    draw.ellipse([cx - size // 2 - 8, cy - size // 2 - 8,
                  cx + size // 2 + 8, cy + size // 2 + 8],
                 outline=(*glow_color, 200), width=2)


# ============================================================
# ICÔNES VECTORIELLES (pas d'emoji -> pas de bug de rendu)
# ============================================================

def _draw_heart(draw, cx, cy, size, color):
    r = size // 4
    draw.ellipse([cx - 2 * r, cy - r, cx, cy + r], fill=color)
    draw.ellipse([cx, cy - r, cx + 2 * r, cy + r], fill=color)
    draw.polygon([(cx - 2 * r, cy), (cx + 2 * r, cy), (cx, cy + 2 * r + r // 2)], fill=color)


def _glow_heart(base_img, cx, cy, size, color):
    W, H = base_img.size
    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _draw_heart(ImageDraw.Draw(glow_layer), cx, cy, int(size * 1.5), (*color, 255))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=10))
    base_img.alpha_composite(glow_layer)
    _draw_heart(ImageDraw.Draw(base_img), cx, cy, size, color)


def _draw_handshake(draw, cx, cy, size, color):
    half = size // 2
    draw.rounded_rectangle([cx - size, cy - half // 2, cx - half // 4, cy + half // 2],
                            radius=half // 2, fill=color)
    draw.rounded_rectangle([cx + half // 4, cy - half // 2, cx + size, cy + half // 2],
                            radius=half // 2, fill=color)
    draw.ellipse([cx - half // 2, cy - half // 2, cx + half // 2, cy + half // 2], fill=color)


def _glow_handshake(base_img, cx, cy, size, color):
    W, H = base_img.size
    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _draw_handshake(ImageDraw.Draw(glow_layer), cx, cy, int(size * 1.4), (*color, 255))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=10))
    base_img.alpha_composite(glow_layer)
    _draw_handshake(ImageDraw.Draw(base_img), cx, cy, size, color)


def _draw_tree_icon(draw, cx, cy, size, color):
    """Petit arbre stylisé (triangle empilé + tronc), vectoriel."""
    trunk_w = size // 6
    draw.rectangle([cx - trunk_w // 2, cy + size // 4, cx + trunk_w // 2, cy + size // 2],
                   fill=(120, 80, 40))
    draw.polygon([(cx, cy - size // 2), (cx - size // 2, cy + size // 8), (cx + size // 2, cy + size // 8)],
                 fill=color)
    draw.polygon([(cx, cy - size // 3), (cx - size * 2 // 5, cy + size // 4), (cx + size * 2 // 5, cy + size // 4)],
                 fill=color)


def _glow_tree_icon(base_img, cx, cy, size, color):
    W, H = base_img.size
    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _draw_tree_icon(ImageDraw.Draw(glow_layer), cx, cy, int(size * 1.4), color)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=10))
    base_img.alpha_composite(glow_layer)
    _draw_tree_icon(ImageDraw.Draw(base_img), cx, cy, size, color)


# ============================================================
# CARTE DE MARIAGE — NÉON
# ============================================================

def generate_marriage_card_neon(name1: str, name2: str, date_str: str,
                                 photo1: "Image.Image|None" = None,
                                 photo2: "Image.Image|None" = None) -> io.BytesIO:
    W, H = 1200, 700
    img = _neon_background(
        W, H,
        glow_spots=[
            (0.15, 0.15, 260, (220, 30, 130)),
            (0.85, 0.15, 220, (150, 30, 220)),
            (0.5, 0.95, 300, (255, 90, 150)),
        ],
        seed=1,
    )

    _rounded_frame(img, 22, 34, (255, 255, 255, 90), 3)
    _rounded_frame(img, 34, 26, (255, 105, 160, 200), 2)

    photo_size = 190
    cy_photo = 220
    _glow_photo(img, W // 2 - 190, cy_photo, photo_size, photo1, (255, 90, 150), name=name1)
    _glow_photo(img, W // 2 + 190, cy_photo, photo_size, photo2, (150, 90, 255), name=name2)
    _glow_heart(img, W // 2, cy_photo, 46, (255, 60, 110))

    _glow_text(img, W / 2, 40, "CERTIFICAT DE MARIAGE", _bold(34),
               glow_color=(255, 90, 150), fill=(255, 255, 255), blur=8)

    n1 = name1 if len(name1) <= 16 else name1[:15] + "…"
    n2 = name2 if len(name2) <= 16 else name2[:15] + "…"
    names_font = _bold(52)
    _glow_text(img, W / 2, 360, f"{n1}  &  {n2}", names_font,
               glow_color=(255, 130, 190), fill=(255, 255, 255), blur=14, passes=2)

    draw = ImageDraw.Draw(img)
    draw.line([(W / 2 - 260, 460), (W / 2 + 260, 460)], fill=(255, 130, 190, 180), width=2)
    sub_font = _regular(26)
    _glow_text(img, W / 2, 480, f"Unis le {date_str}", sub_font,
               glow_color=(255, 130, 190), fill=(230, 220, 255), blur=6, glow_alpha=160)

    _glow_text(img, W / 2, 610, "LifeCity", _bold(30),
               glow_color=(150, 90, 255), fill=(255, 255, 255), blur=10)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    buf.name = "mariage_neon.png"
    return buf


# ============================================================
# CARTE D'AMITIÉ — NÉON
# ============================================================

def generate_friendship_card_neon(name1: str, name2: str, date_str: str,
                                   photo1: "Image.Image|None" = None,
                                   photo2: "Image.Image|None" = None) -> io.BytesIO:
    W, H = 1200, 700
    img = _neon_background(
        W, H,
        glow_spots=[
            (0.15, 0.15, 260, (255, 170, 30)),
            (0.85, 0.15, 220, (40, 220, 200)),
            (0.5, 0.95, 300, (255, 200, 60)),
        ],
        seed=2,
    )

    _rounded_frame(img, 22, 34, (255, 255, 255, 90), 3)
    _rounded_frame(img, 34, 26, (255, 190, 60, 200), 2)

    photo_size = 190
    cy_photo = 220
    _glow_photo(img, W // 2 - 190, cy_photo, photo_size, photo1, (255, 190, 60), name=name1)
    _glow_photo(img, W // 2 + 190, cy_photo, photo_size, photo2, (40, 220, 200), name=name2)
    _glow_handshake(img, W // 2, cy_photo, 60, (255, 210, 90))

    _glow_text(img, W / 2, 40, "CERTIFICAT D'AMITIÉ", _bold(34),
               glow_color=(255, 190, 60), fill=(255, 255, 255), blur=8)

    n1 = name1 if len(name1) <= 16 else name1[:15] + "…"
    n2 = name2 if len(name2) <= 16 else name2[:15] + "…"
    names_font = _bold(52)
    _glow_text(img, W / 2, 360, f"{n1}  &  {n2}", names_font,
               glow_color=(255, 210, 110), fill=(255, 255, 255), blur=14, passes=2)

    draw = ImageDraw.Draw(img)
    draw.line([(W / 2 - 260, 460), (W / 2 + 260, 460)], fill=(255, 210, 110, 180), width=2)
    sub_font = _regular(26)
    _glow_text(img, W / 2, 480, f"Amis depuis le {date_str}", sub_font,
               glow_color=(255, 210, 110), fill=(255, 235, 210), blur=6, glow_alpha=160)

    _glow_text(img, W / 2, 610, "LifeCity", _bold(30),
               glow_color=(40, 220, 200), fill=(255, 255, 255), blur=10)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    buf.name = "amitie_neon.png"
    return buf


# ============================================================
# ARBRE GÉNÉALOGIQUE — NÉON (version simple, 1 joueur + relations)
# ============================================================

def generate_family_tree_card_neon(player_name: str, spouse_name: "str|None",
                                    parents: list, children: list, friends: list,
                                    player_photo: "Image.Image|None" = None,
                                    spouse_photo: "Image.Image|None" = None) -> io.BytesIO:
    W, H = 1200, 800
    img = _neon_background(
        W, H,
        glow_spots=[
            (0.12, 0.1, 260, (60, 100, 255)),
            (0.88, 0.1, 220, (150, 60, 255)),
            (0.5, 0.95, 300, (60, 200, 255)),
        ],
        seed=3,
    )

    _rounded_frame(img, 22, 34, (255, 255, 255, 90), 3)
    _rounded_frame(img, 34, 26, (100, 150, 255, 200), 2)

    _glow_text(img, W / 2, 36, "ARBRE GÉNÉALOGIQUE", _bold(32),
               glow_color=(100, 160, 255), fill=(255, 255, 255), blur=8)

    photo_size = 170
    cy_photo = 200
    if spouse_name:
        _glow_photo(img, W // 2 - 110, cy_photo, photo_size, player_photo, (100, 160, 255), name=player_name)
        _glow_photo(img, W // 2 + 110, cy_photo, photo_size, spouse_photo, (255, 110, 170), name=spouse_name)
        _glow_heart(img, W // 2, cy_photo, 30, (255, 90, 150))
        _glow_text(img, W / 2, 300, f"{player_name}  &  {spouse_name}", _bold(34),
                   glow_color=(180, 200, 255), fill=(255, 255, 255), blur=10)
    else:
        _glow_photo(img, W // 2, cy_photo, photo_size, player_photo, (100, 160, 255), name=player_name)
        _glow_text(img, W / 2, 300, player_name, _bold(34),
                   glow_color=(180, 200, 255), fill=(255, 255, 255), blur=10)

    draw = ImageDraw.Draw(img)
    y = 380

    def section(title, items, color):
        nonlocal y
        _glow_text(img, W / 2, y, title, _bold(26), glow_color=color, fill=(255, 255, 255), blur=6)
        y += 44
        text = ", ".join(items) if items else "Aucun"
        _glow_text(img, W / 2, y, text, _regular(24), glow_color=color, fill=(225, 225, 245),
                   blur=4, glow_alpha=120)
        y += 60

    section("PARENTS", parents, (150, 190, 255))
    section("ENFANTS", children, (255, 190, 150))
    section("AMIS", friends, (150, 255, 210))

    _glow_text(img, W / 2, H - 60, "LifeCity", _bold(28),
               glow_color=(120, 150, 255), fill=(255, 255, 255), blur=10)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    buf.name = "arbre_neon.png"
    return buf


# ============================================================
# WRAPPERS "PRÊTS À L'EMPLOI" — téléchargent la vraie photo Telegram
# ============================================================

async def generate_marriage_card_neon_tg(bot, user_id1: int, name1: str,
                                          user_id2: int, name2: str, date_str: str) -> io.BytesIO:
    """Comme generate_marriage_card_neon, mais va chercher les vraies photos de profil."""
    photo1 = await _download_profile_photo(user_id1, bot)
    photo2 = await _download_profile_photo(user_id2, bot)
    return generate_marriage_card_neon(name1, name2, date_str, photo1, photo2)


async def generate_friendship_card_neon_tg(bot, user_id1: int, name1: str,
                                            user_id2: int, name2: str, date_str: str) -> io.BytesIO:
    """Comme generate_friendship_card_neon, mais va chercher les vraies photos de profil."""
    photo1 = await _download_profile_photo(user_id1, bot)
    photo2 = await _download_profile_photo(user_id2, bot)
    return generate_friendship_card_neon(name1, name2, date_str, photo1, photo2)


async def generate_family_tree_card_neon_tg(bot, player_id: int, player_name: str,
                                             spouse_id: "int|None", spouse_name: "str|None",
                                             parents: list, children: list, friends: list) -> io.BytesIO:
    """Comme generate_family_tree_card_neon, mais va chercher les vraies photos de profil."""
    player_photo = await _download_profile_photo(player_id, bot)
    spouse_photo = await _download_profile_photo(spouse_id, bot) if spouse_id else None
    return generate_family_tree_card_neon(
        player_name, spouse_name, parents, children, friends,
        player_photo, spouse_photo,
    )


# ============================================================
# WRAPPERS DE COMPATIBILITÉ — mêmes noms/signatures que l'ancien
# imagecards.py, pour que family.py n'ait RIEN à changer.
# ============================================================

def _row_get(row, key, default=None):
    """Accès sûr à un champ, que ce soit un sqlite3.Row, un dict ou un objet."""
    if row is None:
        return default
    try:
        value = row[key]
        return value if value is not None else default
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, default)


def _row_label(row) -> str:
    if row is None:
        return "Joueur inconnu"
    return _row_get(row, "first_name") or _row_get(row, "username") or "Joueur inconnu"


def generate_marriage_card(name1: str, name2: str, date_str: str,
                            photo1: "Image.Image|None" = None,
                            photo2: "Image.Image|None" = None) -> io.BytesIO:
    """Compatible avec l'ancien appel : generate_marriage_card(name1, name2, date_str)."""
    return generate_marriage_card_neon(name1, name2, date_str, photo1, photo2)


def generate_friendship_card(name1: str, name2: str, date_str: str,
                              photo1: "Image.Image|None" = None,
                              photo2: "Image.Image|None" = None) -> io.BytesIO:
    """Compatible avec l'ancien appel : generate_friendship_card(name1, name2, date_str)."""
    return generate_friendship_card_neon(name1, name2, date_str, photo1, photo2)


async def generate_family_tree_card(player_data, spouse_data=None,
                                     parents_data=None, children_data=None,
                                     friends_data=None, bot=None) -> io.BytesIO:
    """
    Compatible avec l'ancien appel :
        await generate_family_tree_card(
            player_data=player, spouse_data=spouse_data,
            parents_data=parents_data, children_data=children_data,
            friends_data=friends_data, bot=context.bot,
        )
    Récupère les vraies photos de profil via le bot si disponible, sinon
    affiche des avatars avec initiales.
    """
    parents_data = parents_data or []
    children_data = children_data or []
    friends_data = friends_data or []

    player_name = _row_label(player_data)
    spouse_name = _row_label(spouse_data) if spouse_data else None

    parents_names = [_row_label(p) for p in parents_data]
    children_names = [_row_label(c) for c in children_data]
    friends_names = [_row_label(f) for f in friends_data]

    player_photo = None
    spouse_photo = None
    if bot is not None:
        player_id = _row_get(player_data, "user_id")
        if player_id:
            player_photo = await _download_profile_photo(player_id, bot)
        if spouse_data:
            spouse_id = _row_get(spouse_data, "user_id")
            if spouse_id:
                spouse_photo = await _download_profile_photo(spouse_id, bot)

    return generate_family_tree_card_neon(
        player_name, spouse_name, parents_names, children_names, friends_names,
        player_photo, spouse_photo,
    )

