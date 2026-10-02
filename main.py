#!/usr/bin/env python3
"""
Punto de Venta - SportElite - OPTIMIZADO PARA 5000+ PRODUCTOS
- Paginación en listado (60 por página) para evitar 25000 widgets
- Cache LRU + thumbnails en disco para carga rápida
- ProductoForm 100% CustomTkinter (sin tk.Frame mezclado) -> botones no desaparecen
- SQLite WAL + índices para rendimiento
- Limpieza de jobs after() para evitar bloqueo de mainloop
"""

import sqlite3
import random
import zipfile
import re
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageTk
from collections import OrderedDict

import customtkinter as ctk

# ============================================================
NOMBRE_EMPRESA = "SportElite"
TITULO_APP = f"Punto de Venta - {NOMBRE_EMPRESA}"

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "pos.db"
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
THUMBS_DIR = UPLOADS_DIR / "thumbs"
THUMBS_DIR.mkdir(exist_ok=True)

CARPETA_FOTOS_PRENDAS = Path(r"C:\Users\Dell Precision 7520\Pictures\Sports Elite Prendas")

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("green")

CATEGORIAS = [
    "Camiseta",
    "Playera",
    "Gorra",
    "Deportivo General",
    "Shorts",
    "Pantalones",
    "Sudaderas",
    "Suéteres",
    "Calzado",
    "Uniforme Completo",
]

TALLAS = ["S", "M", "L", "XL", "XXL", "3XL", "4XL"]
TALLAS_CALZADO = [
    "6.0", "6.5", "7.0", "7.5", "8.0", "8.5", "9.0", "9.5",
    "10.0", "10.5", "11", "11.5", "12", "12.5",
]

def es_calzado(categoria):
    return (categoria or "").strip().lower() == "calzado"

def tallas_para_categoria(categoria=None):
    if es_calzado(categoria):
        return list(TALLAS_CALZADO)
    return list(TALLAS)

PRECIO_EXTRA_JUGADOR = 50

FILTRO_PRECIOS = {
    "Ed Especial": 450,
    "Europeos": 400,
    "F1": 700,
    "Liga MX": 400,
    "Manga Larga": 450,
    "MLS": 400,
    "Originals": 450,
    "Retro": 500,
    "Rugby": 400,
    "Seleccion": 400,
    "Selección": 400,
    "NFL": 500,
    "NBA": 400,
    "Sin Mangas": 400,
    "Running Calzado": 1500,
    "Futbol Calzado": 1500,
    "Basquetbol Calzado": 1500,
}
PRECIO_SIN_FILTRO = 400

def precio_por_filtro(nombre_filtro):
    if not nombre_filtro or not str(nombre_filtro).strip():
        return PRECIO_SIN_FILTRO
    key = str(nombre_filtro).strip()
    if key in FILTRO_PRECIOS:
        return FILTRO_PRECIOS[key]
    low = key.lower()
    for k, v in FILTRO_PRECIOS.items():
        if k.lower() == low:
            return v
    return PRECIO_SIN_FILTRO

CATEGORIA_PRECIOS = {
    "Shorts": 550,
    "Pantalones": 550,
    "Calzado": 700,
    "Playera": 500,
    "Sudaderas": 1000,
    "Suéteres": 1000,
    "Uniforme Completo": 450,
}

def precio_para_producto(filtro=None, categoria=None):
    cat = (categoria or "").strip()
    if cat in CATEGORIA_PRECIOS:
        return CATEGORIA_PRECIOS[cat]
    low = cat.lower()
    for k, v in CATEGORIA_PRECIOS.items():
        if k.lower() == low:
            return v
    return precio_por_filtro(filtro)

def actualizar_precios_todos_productos():
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.id, p.filtro, c.nombre AS categoria
        FROM productos p
        LEFT JOIN categorias c ON p.categoria_id = c.id
    """).fetchall()
    for r in rows:
        precio = precio_para_producto(r["filtro"], r["categoria"])
        conn.execute("UPDATE productos SET precio=? WHERE id=?", (precio, r["id"]))
    conn.commit()
    conn.close()
    return len(rows)

def lista_filtros_disponibles():
    s = set(FILTRO_PRECIOS.keys())
    try:
        conn = get_conn()
        rows = conn.execute(
            """SELECT DISTINCT filtro FROM productos
               WHERE filtro IS NOT NULL AND TRIM(filtro) != ''"""
        ).fetchall()
        conn.close()
        for r in rows:
            if r["filtro"]:
                s.add(r["filtro"].strip())
    except Exception:
        pass
    return sorted(s, key=lambda x: x.lower())

PROMO_DEFS = {
    "3x2_filtro": {"nombre": "3x2 mismo filtro","desc": "Mismo filtro: lleva 3, paga 2."},
    "40_todo": {"nombre": "40% en cualquier prenda","desc": "40% en todo el carrito."},
    "2x1_pants": {"nombre": "2x1 pantalones y shorts","desc": "En Pantalones y Shorts: lleva 2, paga 1."},
    "20_cinco": {"nombre": "20% en 5 prendas","desc": "20% si hay 5+ prendas (fuera del bloque Uniforme Completo). No se combina con lealtad 30%."},
    "lealtad_30": {"nombre": "30% cliente frecuente","desc": "Al 5to pedido (4 ventas previas con el mismo telefono). Reemplaza el 20%. Sobre prendas fuera del uniforme equipo si aplica."},
    "equipo_15": {"nombre": "15% uniforme equipo","desc": "15% solo en Uniforme Completo con 10+ del mismo modelo. El resto se cobra aparte (20%/30% solo si cumplen su condicion)."},
}


def _parse_fecha(s):
    from datetime import datetime
    if not s:
        return None
    s = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None

def promo_es_permanente(promo_id):
    return promo_id in ("lealtad_30", "equipo_15", "20_cinco")

def promo_estado(row, hoy=None):
    from datetime import date
    if hoy is None:
        hoy = date.today()
    if not row["activa"]:
        return "Desactivada", "gray"
    if promo_es_permanente(row["id"]):
        return "Permanente - Siempre activa", VERDE
    ini = _parse_fecha(row["inicio"])
    fin = _parse_fecha(row["fin"])
    if ini and hoy < ini:
        return f"Programada desde {ini.strftime('%d/%m/%Y')}", "#d97706"
    if fin and hoy > fin:
        return f"Vencida el {fin.strftime('%d/%m/%Y')}", ROJO
    if row["ultimo_uso"]:
        try:
            ult = _parse_fecha(row["ultimo_uso"])
            if ult:
                dias = (hoy - ult).days
                if dias < 14:
                    faltan = 14 - dias
                    return f"En cooldown - {faltan} dias restantes", "#d97706"
        except Exception:
            pass
    return "Activa", VERDE

VERDE = "#16a34a"

VERDE_HOVER = "#15803d"
ROJO = "#dc2626"
ROJO_HOVER = "#b91c1c"
AZUL = "#2563eb"

IMG_LISTA = (56, 56)
IMG_DETALLE = (280, 280)
IMG_MINI = (56, 56)
IMG_DASH = (140, 140)

# ============================================================
#  BASE DE DATOS OPTIMIZADA
# ============================================================

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -64000")  # 64MB cache
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn

def _table_cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

def _ensure_column(conn, table, column, coltype):
    cols = _table_cols(conn, table)
    if column not in cols:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        except sqlite3.OperationalError:
            pass

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            categoria_id INTEGER,
            modelo TEXT,
            descripcion TEXT,
            precio REAL NOT NULL DEFAULT 0,
            filtro TEXT,
            version_jugador INTEGER NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1,
            creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (categoria_id) REFERENCES categorias(id)
        );
        CREATE TABLE IF NOT EXISTS producto_imagenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            ruta TEXT NOT NULL,
            orden INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (producto_id) REFERENCES productos(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS producto_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            talla TEXT NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            UNIQUE(producto_id, talla),
            FOREIGN KEY (producto_id) REFERENCES productos(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_pedido TEXT UNIQUE,
            cliente_nombre TEXT,
            cliente_telefono TEXT,
            total REAL NOT NULL DEFAULT 0,
            estado TEXT NOT NULL DEFAULT 'completado',
            metodo_pago TEXT,
            notas TEXT,
            creado_en TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS pedido_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            producto_id INTEGER,
            producto_nombre TEXT,
            talla TEXT,
            cantidad INTEGER NOT NULL DEFAULT 1,
            precio_unitario REAL NOT NULL DEFAULT 0,
            sublimar_texto INTEGER NOT NULL DEFAULT 0,
            texto_sublimado TEXT,
            numero_sublimado TEXT,
            FOREIGN KEY (pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS promos (
            id TEXT PRIMARY KEY,
            nombre TEXT,
            descripcion TEXT,
            activa INTEGER NOT NULL DEFAULT 1,
            inicio TEXT,
            fin TEXT,
            ultimo_uso TEXT
        );
        CREATE TABLE IF NOT EXISTS cotizaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_cotizacion TEXT UNIQUE,
            cliente_nombre TEXT,
            cliente_telefono TEXT,
            total REAL NOT NULL DEFAULT 0,
            notas TEXT,
            creado_en TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS cotizacion_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cotizacion_id INTEGER NOT NULL,
            producto_id INTEGER,
            producto_nombre TEXT,
            talla TEXT,
            cantidad INTEGER NOT NULL DEFAULT 1,
            precio_unitario REAL NOT NULL DEFAULT 0,
            sublimar_texto INTEGER NOT NULL DEFAULT 0,
            texto_sublimado TEXT,
            numero_sublimado TEXT,
            FOREIGN KEY (cotizacion_id) REFERENCES cotizaciones(id) ON DELETE CASCADE
        );
    """)
    # Migraciones
    _ensure_column(conn, "productos", "filtro", "TEXT")
    _ensure_column(conn, "productos", "version_jugador", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "pedidos", "metodo_pago", "TEXT")
    _ensure_column(conn, "pedido_items", "sublimar_texto", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "pedido_items", "texto_sublimado", "TEXT")
    _ensure_column(conn, "pedido_items", "numero_sublimado", "TEXT")
    _ensure_column(conn, "cotizaciones", "numero_cotizacion", "TEXT")
    _ensure_column(conn, "cotizaciones", "cliente_nombre", "TEXT")
    _ensure_column(conn, "cotizaciones", "cliente_telefono", "TEXT")
    _ensure_column(conn, "cotizaciones", "total", "REAL NOT NULL DEFAULT 0")
    _ensure_column(conn, "cotizaciones", "notas", "TEXT")
    _ensure_column(conn, "cotizaciones", "creado_en", "TEXT")
    
    # ÍNDICES CRÍTICOS PARA 5000+ PRODUCTOS
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_productos_activo ON productos(activo);
        CREATE INDEX IF NOT EXISTS idx_productos_categoria ON productos(categoria_id);
        CREATE INDEX IF NOT EXISTS idx_productos_filtro ON productos(filtro);
        CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos(nombre COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_imagenes_producto ON producto_imagenes(producto_id, orden);
        CREATE INDEX IF NOT EXISTS idx_stock_producto ON producto_stock(producto_id);
        CREATE INDEX IF NOT EXISTS idx_pedidos_fecha ON pedidos(creado_en);
    """)
    
    # Categorías base
    for cat in CATEGORIAS:
        conn.execute("INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", (cat,))
    # Promos base
    for pid, d in PROMO_DEFS.items():
        conn.execute("INSERT OR IGNORE INTO promos (id, nombre, descripcion, activa) VALUES (?,?,?,1)",
                     (pid, d["nombre"], d["desc"]))
    conn.commit()
    conn.close()

# ============================================================
#  CACHE DE IMÁGENES OPTIMIZADO - LRU + THUMBNAILS EN DISCO
# ============================================================

class LRUCache(OrderedDict):
    def __init__(self, maxsize=400):
        super().__init__()
        self.maxsize = maxsize
    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value
    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            self.popitem(last=False)

_IMAGE_CACHE = LRUCache(maxsize=400)

def get_thumb_path(original_path, size):
    """Ruta del thumbnail en disco para evitar re-escalar cada vez."""
    name = Path(original_path).stem
    # hash simple para evitar colisiones
    suffix = f"{size[0]}x{size[1]}"
    return THUMBS_DIR / f"{name}_{suffix}.jpg"

def load_ctk_image(path, size, fast=False):
    """Carga con cache LRU + thumbnails en disco. Mucho más rápido para 5000 productos."""
    try:
        p = Path(path)
        if not p.is_file():
            return None
        key = (str(p.resolve()), int(size[0]), int(size[1]), bool(fast))
        if key in _IMAGE_CACHE:
            return _IMAGE_CACHE[key]

        # Intentar usar thumbnail pre-generado
        thumb_path = get_thumb_path(p, size)
        use_path = p
        if thumb_path.exists():
            # Si thumbnail es más reciente que original, usarlo
            try:
                if thumb_path.stat().st_mtime >= p.stat().st_mtime:
                    use_path = thumb_path
            except Exception:
                pass
        
        with Image.open(use_path) as im:
            img = im.convert("RGB")
            resample = Image.Resampling.BILINEAR if fast else Image.Resampling.LANCZOS
            # Si no es thumbnail, crear y guardar thumbnail para futuro
            if use_path == p:
                img.thumbnail((int(size[0]*2), int(size[1]*2)), resample)
                try:
                    # Guardar thumbnail en disco (jpeg rápido)
                    thumb_path.parent.mkdir(parents=True, exist_ok=True)
                    # Redimension exacta para thumb
                    thumb = img.copy()
                    thumb.thumbnail((int(size[0]), int(size[1])), resample)
                    thumb.save(thumb_path, "JPEG", quality=85, optimize=True)
                    img = thumb
                except Exception:
                    img.thumbnail((int(size[0]), int(size[1])), resample)
            else:
                # Ya es thumbnail, solo asegurar tamaño
                img.thumbnail((int(size[0]), int(size[1])), resample)
            w, h = img.size
            if w == 0 or h == 0:
                return None
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(w, h))
        
        _IMAGE_CACHE[key] = ctk_img
        return ctk_img
    except Exception as e:
        # print(f"load_ctk_image error {path}: {e}")
        return None

def clear_image_cache():
    _IMAGE_CACHE.clear()
    # Opcional: limpiar thumbs viejos
    # for f in THUMBS_DIR.glob("*.jpg"):
    #     try: f.unlink()
    #     except: pass

# Resto de helpers originales (precio, etc.)
def precio_aficion(prod):
    try:
        return float(prod["precio"] or 0)
    except Exception:
        return 0.0

def precio_jugador(prod):
    return precio_aficion(prod) + PRECIO_EXTRA_JUGADOR

def tiene_version_jugador(prod):
    try:
        return bool(prod["version_jugador"])
    except Exception:
        return False

def primera_imagen(producto_id):
    conn = get_conn()
    r = conn.execute("SELECT ruta FROM producto_imagenes WHERE producto_id=? ORDER BY orden, id LIMIT 1", (producto_id,)).fetchone()
    conn.close()
    return r["ruta"] if r else None

def mapa_primeras_imagenes(producto_ids):
    if not producto_ids:
        return {}
    conn = get_conn()
    placeholders = ",".join("?" * len(producto_ids))
    rows = conn.execute(
        f"""SELECT producto_id, ruta FROM producto_imagenes
            WHERE producto_id IN ({placeholders})
            ORDER BY orden, id""",
        list(producto_ids),
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        pid = r["producto_id"]
        if pid not in out:
            out[pid] = r["ruta"]
    return out

def nombre_archivo_seguro(nombre, usados):
    base = re.sub(r'[^A-Za-z0-9_-]+', '_', (nombre or "producto").strip())[:40] or "producto"
    cand = base
    i = 1
    while cand.lower() in usados:
        cand = f"{base}_{i}"
        i += 1
    usados.add(cand.lower())
    return cand




def exportar_prenda_imagen(prod, ruta_png):
    W, H = 600, 800
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, 180], fill="#16a34a")
    try:
        font_b = ImageFont.truetype("arial.ttf", 26)
        font_m = ImageFont.truetype("arial.ttf", 20)
        font_s = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        try:
            font_b = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 26)
            font_m = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20)
            font_s = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
        except Exception:
            font_b = ImageFont.load_default()
            font_m = ImageFont.load_default()
            font_s = ImageFont.load_default()
    nombre = (prod["nombre"] or "Producto")[:40]
    draw.text((20, 15), nombre, fill="white", font=font_b)
    draw.text((20, 55), "Filtro: " + str(prod["filtro"] or "Sin filtro"), fill="white", font=font_m)
    categoria = prod["categoria"] if "categoria" in prod.keys() else ""
    draw.text((20, 85), "Cat: " + str(categoria or "-"), fill="white", font=font_s)
    precio = float(prod["precio"] or 0)
    draw.text((20, 115), "$" + f"{precio:,.0f}" + " MXN", fill="white", font=font_b)
    y_img = 200
    ruta_rel = primera_imagen(prod["id"])
    if ruta_rel:
        p = UPLOADS_DIR / ruta_rel
        if p.exists():
            try:
                with Image.open(p) as im:
                    im = im.convert("RGB")
                    im.thumbnail((540, 500), Image.Resampling.LANCZOS)
                    x_offset = (W - im.width)//2
                    img.paste(im, (x_offset, y_img))
            except Exception:
                pass
    draw.text((20, H-25), "SportElite - " + datetime.now().strftime('%d/%m/%Y'), fill="gray", font=font_s)
    try:
        img.save(ruta_png, "PNG", optimize=True)
    except Exception:
        img.save(ruta_png, "PNG")

def exportar_catalogo_pdf(ruta_pdf, categoria=None, filtro=None):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        HAS_REPORTLAB = True
    except ImportError:
        HAS_REPORTLAB = False
    conn = get_conn()
    sql = """
        SELECT p.id, p.nombre, p.filtro, p.precio, p.version_jugador, c.nombre as categoria
        FROM productos p
        LEFT JOIN categorias c ON p.categoria_id = c.id
        WHERE p.activo=1
    """
    params = []
    if categoria:
        sql += " AND c.nombre=?"
        params.append(categoria)
    if filtro:
        if str(filtro).strip().lower() in ("sin filtro", "sin_filtro", "-", "no filtro"):
            sql += " AND (p.filtro IS NULL OR TRIM(p.filtro)='' OR LOWER(TRIM(p.filtro)) IN ('sin filtro','sin_filtro','-','no filtro'))"
        else:
            sql += " AND p.filtro=?"
            params.append(filtro)
    prods = conn.execute(sql, params).fetchall()
    conn.close()
    if not prods:
        if categoria and filtro:
            raise ValueError(f"No hay productos activos en {categoria} / {filtro}")
        if categoria:
            raise ValueError(f"No hay productos activos en la categoria {categoria}")
        raise ValueError("No hay productos activos para exportar")
    cat_order = {name: idx for idx, name in enumerate(CATEGORIAS)}
    def sort_key(p):
        cat = (p["categoria"] or "").strip()
        cat_rank = cat_order.get(cat, len(CATEGORIAS) + 100)
        cat_name_lower = cat.lower()
        filtro = (p["filtro"] or "").strip()
        filtro_lower = filtro.lower()
        es_sin_filtro = 1 if (not filtro or filtro_lower in ("sin filtro", "sin_filtro", "-", "no filtro")) else 0
        nombre_lower = (p["nombre"] or "").lower()
        return (cat_rank, cat_name_lower, es_sin_filtro, filtro_lower, nombre_lower)
    prods_sorted = sorted(prods, key=sort_key)
    ids = [p["id"] for p in prods_sorted]
    img_map = mapa_primeras_imagenes(ids)
    if HAS_REPORTLAB:
        c = canvas.Canvas(str(ruta_pdf), pagesize=A4)
        w, h = A4
        cols = 3
        rows = 10
        per_page = cols * rows
        margin = 10 * mm
        header_h = 12 * mm
        footer_h = 8 * mm
        gap_x = 4 * mm
        gap_y = 3 * mm
        usable_w = w - 2 * margin
        usable_h = h - margin - header_h - footer_h - margin
        col_w = (usable_w - (cols - 1) * gap_x) / cols
        row_h = (usable_h - (rows - 1) * gap_y) / rows
        verde_rgb = (0.08, 0.64, 0.29)
        gris_borde = (0.85, 0.85, 0.85)
        gris_texto = (0.4, 0.4, 0.4)
        total_pages = (len(prods_sorted) + per_page - 1) // per_page
        for page_idx in range(total_pages):
            if page_idx > 0:
                c.showPage()
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica-Bold", 13)
            titulo = f"Catalogo {NOMBRE_EMPRESA}"
            if categoria and filtro:
                titulo += f" - {categoria} / {filtro}"
            elif categoria:
                titulo += f" - {categoria}"
            else:
                titulo += " - Completo"
            c.drawString(margin, h - margin - 5 * mm, titulo)
            c.setFont("Helvetica", 8)
            c.setFillColorRGB(*gris_texto)
            rango_ini = page_idx * per_page + 1
            rango_fin = min((page_idx + 1) * per_page, len(prods_sorted))
            cats_in_page = []
            for p in prods_sorted[page_idx * per_page : (page_idx + 1) * per_page]:
                if p["categoria"] and p["categoria"] not in cats_in_page:
                    cats_in_page.append(p["categoria"])
            cats_txt = ", ".join(cats_in_page[:3])
            if len(cats_in_page) > 3:
                cats_txt += f" +{len(cats_in_page)-3} mas"
            c.drawString(margin, h - margin - 9 * mm, f"Pagina {page_idx+1}/{total_pages} | {rango_ini}-{rango_fin} de {len(prods_sorted)} | {cats_txt} | Orden: Categoria > Filtro > Sin filtro final")
            c.setStrokeColorRGB(*gris_borde)
            c.setLineWidth(0.5)
            c.line(margin, h - margin - header_h + 2 * mm, w - margin, h - margin - header_h + 2 * mm)
            for idx_in_page in range(per_page):
                global_idx = page_idx * per_page + idx_in_page
                if global_idx >= len(prods_sorted):
                    break
                p = prods_sorted[global_idx]
                col = idx_in_page % cols
                row = idx_in_page // cols
                x = margin + col * (col_w + gap_x)
                y_top = h - margin - header_h - row * (row_h + gap_y)
                y_bottom = y_top - row_h
                c.setFillColorRGB(1, 1, 1)
                c.setStrokeColorRGB(*gris_borde)
                c.setLineWidth(0.6)
                c.rect(x, y_bottom, col_w, row_h, stroke=1, fill=1)
                img_h = row_h * 0.58
                text_h = row_h * 0.42
                img_y_bottom = y_bottom + text_h + 1 * mm
                ruta_rel = img_map.get(p["id"])
                if ruta_rel:
                    img_path = UPLOADS_DIR / ruta_rel
                    if img_path.exists():
                        try:
                            c.drawImage(ImageReader(str(img_path)), x + 1.5 * mm, img_y_bottom, width=col_w - 3 * mm, height=img_h - 2 * mm, preserveAspectRatio=True, anchor='c')
                        except Exception:
                            pass
                c.setFillColorRGB(0, 0, 0)
                c.setFont("Helvetica-Bold", 7)
                nombre = (p["nombre"] or "").strip()
                if len(nombre) > 32:
                    line1 = nombre[:32].strip()
                    line2 = nombre[32:58].strip()
                    c.drawString(x + 2 * mm, y_bottom + text_h - 3 * mm, line1)
                    c.setFont("Helvetica-Bold", 6.5)
                    c.drawString(x + 2 * mm, y_bottom + text_h - 6.5 * mm, line2)
                else:
                    c.drawString(x + 2 * mm, y_bottom + text_h - 3 * mm, nombre)
                c.setFont("Helvetica", 6)
                c.setFillColorRGB(*gris_texto)
                cat_txt = (p["categoria"] or "Sin cat")[:18]
                filtro_txt = (p["filtro"] or "Sin filtro")[:18]
                if not p["filtro"] or filtro_txt.lower() in ("sin filtro", "sin_filtro"):
                    filtro_txt = "Sin filtro"
                    c.setFillColorRGB(0.6, 0.4, 0.2)
                info_line = f"{cat_txt} | {filtro_txt}"
                c.drawString(x + 2 * mm, y_bottom + 6 * mm, info_line)
                c.setFont("Helvetica-Bold", 8)
                c.setFillColorRGB(*verde_rgb)
                precio = float(p["precio"] or 0)
                precio_txt = f"${precio:,.0f}"
                if p["version_jugador"]:
                    precio_txt += f" / J${precio+PRECIO_EXTRA_JUGADOR:,.0f}"
                c.drawString(x + 2 * mm, y_bottom + 2 * mm, precio_txt)
            c.setFont("Helvetica", 7)
            c.setFillColorRGB(*gris_texto)
            c.drawCentredString(w / 2, margin - 1 * mm, f"{NOMBRE_EMPRESA} - Catalogo ordenado por categoria y filtro - {datetime.now().strftime('%d/%m/%Y')} - Pag {page_idx+1}/{total_pages}")
            c.drawRightString(w - margin, margin - 1 * mm, f"{len(prods_sorted)} productos")
        c.save()
        return
    pages = []
    per_page_img = 30
    cols = 3
    for i in range(0, len(prods_sorted), per_page_img):
        batch = prods_sorted[i:i+per_page_img]
        page_w, page_h = 2480, 3508
        page = Image.new("RGB", (page_w, page_h), "white")
        draw = ImageDraw.Draw(page)
        try:
            font_b = ImageFont.truetype("arial.ttf", 42)
            font = ImageFont.truetype("arial.ttf", 32)
            font_s = ImageFont.truetype("arial.ttf", 26)
        except:
            font_b = ImageFont.load_default()
            font = ImageFont.load_default()
            font_s = ImageFont.load_default()
        titulo_img = f"Catalogo {NOMBRE_EMPRESA}"
        if categoria and filtro:
            titulo_img += f" - {categoria} / {filtro}"
        elif categoria:
            titulo_img += f" - {categoria}"
        else:
            titulo_img += " - Completo"
        draw.text((30,30), f"{titulo_img} - Pagina {i//per_page_img+1} - {len(prods_sorted)} prod", fill="black", font=font_b)
        margin = 40
        gap_x = 20
        gap_y = 20
        usable_w = page_w - 2*margin
        col_w = (usable_w - (cols-1)*gap_x)//cols
        row_h = 300
        y0 = 120
        for idx_in_page, p in enumerate(batch):
            col = idx_in_page % cols
            row = idx_in_page // cols
            x = margin + col * (col_w + gap_x)
            y = y0 + row * (row_h + gap_y)
            draw.rectangle([x, y, x+col_w, y+row_h], outline="#cccccc")
            draw.text((x+10, y+10), (p["nombre"] or "")[:30], fill="black", font=font)
            filtro_txt = p["filtro"] or "Sin filtro"
            draw.text((x+10, y+50), f"{p['categoria'] or ''} | {filtro_txt}", fill="gray", font=font_s)
            draw.text((x+10, y+90), f"${float(p['precio'] or 0):,.0f}", fill="#16a34a", font=font)
            ruta_rel = img_map.get(p["id"])
            if ruta_rel and (UPLOADS_DIR / ruta_rel).exists():
                try:
                    from PIL import Image as PILImage
                    with PILImage.open(UPLOADS_DIR / ruta_rel) as im:
                        im = im.convert("RGB")
                        im.thumbnail((180,180), PILImage.Resampling.LANCZOS)
                        page.paste(im, (x+col_w-200, y+10))
                except:
                    pass
        pages.append(page)
    if pages:
        first = pages[0]
        rest = pages[1:]
        if rest:
            first.save(ruta_pdf, "PDF", resolution=100.0, save_all=True, append_images=rest)
        else:
            first.save(ruta_pdf, "PDF", resolution=100.0)


# ============================================================
#  EXPORTAR / IMPORTAR DATOS (JSON) E IMÁGENES (ZIP)
# ============================================================

def serializar_producto_completo(prod_row, stock_rows, imagen_rows):
    """Convierte un producto de SQLite a dict JSON-serializable."""
    return {
        "id": prod_row["id"],
        "nombre": prod_row["nombre"],
        "categoria": prod_row["categoria"] if "categoria" in prod_row.keys() else None,
        "categoria_id": prod_row["categoria_id"],
        "modelo": prod_row["modelo"] or "",
        "descripcion": prod_row["descripcion"] or "",
        "precio": float(prod_row["precio"] or 0),
        "filtro": prod_row["filtro"] or "",
        "version_jugador": int(prod_row["version_jugador"] or 0),
        "activo": int(prod_row["activo"] if "activo" in prod_row.keys() else 1),
        "stock": [{"talla": s["talla"], "stock": int(s["stock"] or 0)} for s in stock_rows],
        "imagenes": [{"orden": int(im["orden"] or 0), "archivo": im["ruta"]} for im in imagen_rows],
    }


def consultar_productos_export(categoria=None, filtro=None, solo_ids=None, solo_activos=True):
    """Lista productos con categoria para exportar."""
    conn = get_conn()
    sql = """
        SELECT p.*, c.nombre as categoria
        FROM productos p
        LEFT JOIN categorias c ON p.categoria_id = c.id
        WHERE 1=1
    """
    params = []
    if solo_activos:
        sql += " AND p.activo=1"
    if solo_ids:
        placeholders = ",".join("?" * len(solo_ids))
        sql += f" AND p.id IN ({placeholders})"
        params.extend(list(solo_ids))
    if categoria:
        sql += " AND c.nombre=?"
        params.append(categoria)
    if filtro:
        if str(filtro).strip().lower() in ("sin filtro", "sin_filtro", "-", "no filtro"):
            sql += " AND (p.filtro IS NULL OR TRIM(p.filtro)='' OR LOWER(TRIM(p.filtro)) IN ('sin filtro','sin_filtro','-','no filtro'))"
        else:
            sql += " AND p.filtro=?"
            params.append(filtro)
    sql += " ORDER BY c.nombre COLLATE NOCASE, p.filtro COLLATE NOCASE, p.nombre COLLATE NOCASE"
    prods = conn.execute(sql, params).fetchall()
    conn.close()
    return prods


def exportar_productos_json(ruta_json, categoria=None, filtro=None, solo_ids=None):
    """Exporta productos (+ stock + rutas de imagen) a un archivo JSON."""
    import json
    prods = consultar_productos_export(categoria=categoria, filtro=filtro, solo_ids=solo_ids)
    if not prods:
        raise ValueError("No hay productos para exportar con esos criterios.")
    conn = get_conn()
    out = []
    for p in prods:
        stock = conn.execute(
            "SELECT talla, stock FROM producto_stock WHERE producto_id=? ORDER BY talla",
            (p["id"],),
        ).fetchall()
        imgs = conn.execute(
            "SELECT ruta, orden FROM producto_imagenes WHERE producto_id=? ORDER BY orden, id",
            (p["id"],),
        ).fetchall()
        out.append(serializar_producto_completo(p, stock, imgs))
    conn.close()
    payload = {
        "version": 1,
        "app": NOMBRE_EMPRESA,
        "exportado_en": datetime.now().isoformat(timespec="seconds"),
        "total": len(out),
        "filtros": {"categoria": categoria, "filtro": filtro, "ids": list(solo_ids) if solo_ids else None},
        "productos": out,
    }
    Path(ruta_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(out)


def exportar_imagenes_zip(ruta_zip, categoria=None, filtro=None, solo_ids=None, incluir_json=True):
    """
    Empaqueta las fotos originales de uploads/ en un ZIP.
    Estructura:
      imagenes/{id_producto}/{archivo_original}
      productos.json  (opcional, metadatos)
    Nota: se usa ZIP (estándar, sin software extra). Es el equivalente práctico a RAR.
    """
    prods = consultar_productos_export(categoria=categoria, filtro=filtro, solo_ids=solo_ids)
    if not prods:
        raise ValueError("No hay productos para exportar con esos criterios.")
    conn = get_conn()
    exportados = 0
    faltantes = 0
    meta = []
    with zipfile.ZipFile(ruta_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in prods:
            imgs = conn.execute(
                "SELECT ruta, orden FROM producto_imagenes WHERE producto_id=? ORDER BY orden, id",
                (p["id"],),
            ).fetchall()
            stock = conn.execute(
                "SELECT talla, stock FROM producto_stock WHERE producto_id=? ORDER BY talla",
                (p["id"],),
            ).fetchall()
            meta.append(serializar_producto_completo(p, stock, imgs))
            for im in imgs:
                nombre = im["ruta"]
                if not nombre:
                    continue
                src = UPLOADS_DIR / nombre
                if not src.is_file():
                    # a veces la ruta incluye subcarpeta
                    src2 = UPLOADS_DIR / Path(nombre).name
                    src = src2 if src2.is_file() else src
                if src.is_file():
                    arc = f"imagenes/{p['id']}/{src.name}"
                    zf.write(src, arcname=arc)
                    exportados += 1
                else:
                    faltantes += 1
        if incluir_json:
            import json
            payload = {
                "version": 1,
                "app": NOMBRE_EMPRESA,
                "exportado_en": datetime.now().isoformat(timespec="seconds"),
                "total_productos": len(meta),
                "total_imagenes": exportados,
                "productos": meta,
            }
            zf.writestr("productos.json", json.dumps(payload, ensure_ascii=False, indent=2))
    conn.close()
    return {"productos": len(prods), "imagenes": exportados, "faltantes": faltantes}


def importar_productos_json(ruta_json, zip_imagenes=None, actualizar_existentes=True):
    """
    Importa productos desde JSON.
    Si se pasa un ZIP de imágenes (estructura imagenes/{id}/archivo o archivo plano),
    copia las fotos a uploads/.
    - Si el producto tiene el mismo id y actualizar_existentes: actualiza.
    - Si no existe id o no se quiere actualizar: crea nuevo (nuevo id) y re-mapea imágenes.
    """
    import json
    data = json.loads(Path(ruta_json).read_text(encoding="utf-8"))
    productos = data.get("productos") if isinstance(data, dict) else data
    if not isinstance(productos, list) or not productos:
        raise ValueError("El JSON no contiene una lista de productos.")

    # Extraer imágenes del ZIP a carpeta temporal si aplica
    tmp_extract = None
    zip_root = None
    if zip_imagenes:
        tmp_extract = tempfile.mkdtemp(prefix="pos_img_import_")
        with zipfile.ZipFile(zip_imagenes, "r") as zf:
            zf.extractall(tmp_extract)
        zip_root = Path(tmp_extract)

    conn = get_conn()
    creados = 0
    actualizados = 0
    imgs_copiadas = 0

    def asegurar_categoria(nombre):
        nombre = (nombre or "Deportivo General").strip() or "Deportivo General"
        row = conn.execute("SELECT id FROM categorias WHERE nombre=?", (nombre,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO categorias (nombre) VALUES (?)", (nombre,))
        return cur.lastrowid

    def copiar_imagen_desde_zip(producto_id_origen, archivo, nuevo_producto_id):
        nonlocal imgs_copiadas
        if not zip_root or not archivo:
            return None
        candidatos = [
            zip_root / "imagenes" / str(producto_id_origen) / Path(archivo).name,
            zip_root / "imagenes" / str(producto_id_origen) / archivo,
            zip_root / Path(archivo).name,
            zip_root / archivo,
        ]
        # búsqueda flexible por nombre de archivo
        found = None
        for c in candidatos:
            if c.is_file():
                found = c
                break
        if not found:
            for p in zip_root.rglob(Path(archivo).name):
                if p.is_file():
                    found = p
                    break
        if not found:
            return None
        # nombre destino único
        dest_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{nuevo_producto_id}_{Path(found).name}"
        dest_name = re.sub(r"[^A-Za-z0-9._-]+", "_", dest_name)
        dest = UPLOADS_DIR / dest_name
        shutil.copy2(found, dest)
        imgs_copiadas += 1
        return dest_name

    try:
        for item in productos:
            nombre = (item.get("nombre") or "").strip()
            if not nombre:
                continue
            cat_nombre = item.get("categoria")
            cat_id = asegurar_categoria(cat_nombre)
            precio = float(item.get("precio") or 0)
            filtro = item.get("filtro") or ""
            modelo = item.get("modelo") or ""
            desc = item.get("descripcion") or ""
            vjug = int(item.get("version_jugador") or 0)
            activo = int(item.get("activo") if item.get("activo") is not None else 1)
            old_id = item.get("id")

            existente = None
            if old_id is not None and actualizar_existentes:
                existente = conn.execute("SELECT id FROM productos WHERE id=?", (old_id,)).fetchone()

            if existente:
                pid = existente["id"]
                conn.execute(
                    """UPDATE productos SET nombre=?, categoria_id=?, modelo=?, descripcion=?,
                       precio=?, filtro=?, version_jugador=?, activo=? WHERE id=?""",
                    (nombre, cat_id, modelo, desc, precio, filtro, vjug, activo, pid),
                )
                actualizados += 1
            else:
                cur = conn.execute(
                    """INSERT INTO productos (nombre, categoria_id, modelo, descripcion, precio, filtro, version_jugador, activo)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (nombre, cat_id, modelo, desc, precio, filtro, vjug, activo),
                )
                pid = cur.lastrowid
                creados += 1

            # Stock: reemplazar
            conn.execute("DELETE FROM producto_stock WHERE producto_id=?", (pid,))
            for s in item.get("stock") or []:
                talla = str(s.get("talla") or "").strip()
                if not talla:
                    continue
                st = int(s.get("stock") or 0)
                conn.execute(
                    "INSERT OR REPLACE INTO producto_stock (producto_id, talla, stock) VALUES (?,?,?)",
                    (pid, talla, st),
                )

            # Imágenes
            imgs = item.get("imagenes") or []
            if imgs and zip_root:
                conn.execute("DELETE FROM producto_imagenes WHERE producto_id=?", (pid,))
                for im in imgs:
                    archivo = im.get("archivo") or im.get("ruta") or ""
                    orden = int(im.get("orden") or 0)
                    nuevo_archivo = copiar_imagen_desde_zip(old_id or pid, archivo, pid)
                    if nuevo_archivo:
                        conn.execute(
                            "INSERT INTO producto_imagenes (producto_id, ruta, orden) VALUES (?,?,?)",
                            (pid, nuevo_archivo, orden),
                        )
            elif imgs and not zip_root:
                # Solo metadatos: si el archivo ya existe en uploads con ese nombre, vincular
                for im in imgs:
                    archivo = im.get("archivo") or im.get("ruta") or ""
                    orden = int(im.get("orden") or 0)
                    if archivo and (UPLOADS_DIR / Path(archivo).name).is_file():
                        # evitar duplicar mismo vínculo
                        existe_v = conn.execute(
                            "SELECT id FROM producto_imagenes WHERE producto_id=? AND ruta=?",
                            (pid, Path(archivo).name),
                        ).fetchone()
                        if not existe_v:
                            conn.execute(
                                "INSERT INTO producto_imagenes (producto_id, ruta, orden) VALUES (?,?,?)",
                                (pid, Path(archivo).name, orden),
                            )

        conn.commit()
    finally:
        conn.close()
        if tmp_extract:
            try:
                shutil.rmtree(tmp_extract, ignore_errors=True)
            except Exception:
                pass

    return {"creados": creados, "actualizados": actualizados, "imagenes_copiadas": imgs_copiadas}


def exportar_ticket_imagen(pedido, items, ruta_png):

    W = 800
    H = 120 + len(items)*110
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    try:
        font_b = ImageFont.truetype("arial.ttf", 22)
        font = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font_b = ImageFont.load_default()
        font = ImageFont.load_default()
    draw.text((20, 10), f"Pedido #{pedido['numero_pedido']}", fill="black", font=font_b)
    draw.text((20, 45), f"Cliente: {pedido['cliente_nombre'] or '-'} Tel: {pedido['cliente_telefono'] or '-'}", fill="black", font=font)
    draw.text((20, 70), f"Total: ${float(pedido['total']):,.2f}  Fecha: {str(pedido['creado_en'])[:19]}", fill="black", font=font)
    y = 110
    for it in items:
        draw.text((20, y), f"{it['producto_nombre'] or '-'} - Talla {it['talla'] or '-'} x{it['cantidad']}  ${float(it['precio_unitario']):,.2f}", fill="black", font=font)
        y += 25
        if it["texto_sublimado"] or it["numero_sublimado"]:
            draw.text((20, y), f"  Sublimado: {it['texto_sublimado'] or ''} #{it['numero_sublimado'] or ''}", fill="#2563eb", font=font)
            y += 22
        y += 15
    img.save(ruta_png, "PNG")

def generar_numero_pedido(conn):
    try:
        existentes = {r[0] for r in conn.execute("SELECT numero_pedido FROM pedidos WHERE numero_pedido IS NOT NULL").fetchall()}
    except Exception:
        existentes = set()
    for _ in range(200):
        num = f"{random.randint(0, 99999):05d}"
        if num not in existentes:
            return num
    return f"{random.randint(0, 99999):05d}"

def generar_numero_cotizacion(conn):
    try:
        existentes = {r[0] for r in conn.execute("SELECT numero_cotizacion FROM cotizaciones WHERE numero_cotizacion IS NOT NULL").fetchall()}
    except Exception:
        existentes = set()
    for _ in range(200):
        num = f"C{random.randint(0, 99999):05d}"
        if num not in existentes:
            return num
    return f"C{random.randint(0, 99999):05d}"

def _row_val(row, *names):
    if row is None:
        return None
    keys = []
    try:
        keys = list(row.keys())
    except Exception:
        pass
    for n in names:
        if n in keys:
            try:
                return row[n]
            except Exception:
                pass
        try:
            return row[n]
        except Exception:
            continue
    return None

def _promo_vigente(row, hoy=None):
    from datetime import date
    if hoy is None:
        hoy = date.today()
    try:
        if not _row_val(row, "activa"):
            return False
    except Exception:
        return False
    pid = _row_val(row, "id")
    if promo_es_permanente(pid):
        return True
    ini = _parse_fecha(_row_val(row, "inicio", "fecha_inicio"))
    fin = _parse_fecha(_row_val(row, "fin", "fecha_fin"))
    if ini and hoy < ini:
        return False
    if fin and hoy > fin:
        return False
    return True

def calcular_total_con_promos(carrito, nombre="", tel=""):
    items = []
    for it in carrito or []:
        try:
            cant = int(it.get("cantidad") or 1)
        except Exception:
            cant = 1
        try:
            precio = float(it.get("precio") or 0)
        except Exception:
            precio = 0.0
        if cant <= 0:
            continue
        items.append({
            "producto_id": it.get("producto_id"),
            "nombre": it.get("nombre") or "",
            "talla": it.get("talla"),
            "cantidad": cant,
            "precio": precio,
            "filtro": (it.get("filtro") or "") or "",
            "categoria": (it.get("categoria") or "") or "",
        })
    subtotal = sum(i["precio"] * i["cantidad"] for i in items)
    vacio = {"subtotal": subtotal, "descuento": 0.0, "total": subtotal, "detalle": ""}
    if subtotal <= 0:
        return vacio

    from datetime import date
    from collections import defaultdict
    hoy = date.today()
    conn = get_conn()
    # Completar categoria desde BD si falta (necesario para Uniforme Completo)
    pids_sin_cat = [i["producto_id"] for i in items if i["producto_id"] and not i["categoria"]]
    if pids_sin_cat:
        try:
            ph = ",".join("?" * len(pids_sin_cat))
            cat_rows = conn.execute(
                f"""SELECT p.id, c.nombre AS categoria
                    FROM productos p
                    LEFT JOIN categorias c ON p.categoria_id = c.id
                    WHERE p.id IN ({ph})""",
                pids_sin_cat,
            ).fetchall()
            cat_map = {r["id"]: (r["categoria"] or "") for r in cat_rows}
            for i in items:
                if not i["categoria"] and i["producto_id"] in cat_map:
                    i["categoria"] = cat_map[i["producto_id"]]
        except Exception:
            pass
    rows = []
    for tabla in ("promos", "promociones"):
        try:
            rows = conn.execute(f"SELECT * FROM {tabla}").fetchall()
            if rows:
                break
        except Exception:
            rows = []
    activas = set()
    for r in rows:
        try:
            if _promo_vigente(r, hoy):
                activas.add(_row_val(r, "id"))
        except Exception:
            pass

    units = []
    for i in items:
        for _ in range(i["cantidad"]):
            units.append(i)

    descuento = 0.0
    detalles = []

    if "2x1_pants" in activas:
        pants = [u for u in units if (u.get("categoria") or "").strip() in ("Pantalones", "Shorts")]
        pants.sort(key=lambda x: x["precio"], reverse=True)
        n_free = len(pants) // 2
        d = sum(p["precio"] for p in pants[-n_free:]) if n_free else 0.0
        if d > 0:
            descuento += d
            detalles.append(f"2x1 pantalones/shorts -${d:,.0f}")

    if "3x2_filtro" in activas:
        groups = defaultdict(list)
        for u in units:
            f = (u.get("filtro") or "").strip()
            if f and f.lower() not in ("sin filtro", "sin_filtro"):
                groups[f].append(u)
        d3 = 0.0
        for lst in groups.values():
            if len(lst) < 3:
                continue
            lst = sorted(lst, key=lambda x: x["precio"])
            n_free = len(lst) // 3
            d3 += sum(x["precio"] for x in lst[:n_free])
        if d3 > 0:
            descuento += d3
            detalles.append(f"3x2 mismo filtro -${d3:,.0f}")

    # --- Uniforme equipo (15%): SOLO categoria Uniforme Completo con 10+ del mismo modelo ---
    # No se aplica a otras categorias aunque haya 10+ piezas del mismo producto.
    by_n_uc = defaultdict(int)   # solo Uniforme Completo
    by_v_uc = defaultdict(float)
    for u in units:
        cat = (u.get("categoria") or "").strip().lower()
        if cat != "uniforme completo":
            continue
        pid = u.get("producto_id")
        by_n_uc[pid] += 1
        by_v_uc[pid] += u["precio"]

    team_pids = set()
    d15 = 0.0
    if "equipo_15" in activas:
        for pid, n in by_n_uc.items():
            if n >= 10:
                team_pids.add(pid)
                d15 += by_v_uc[pid] * 0.15
        if d15 > 0:
            descuento += d15
            detalles.append(f"15% uniforme equipo (Uniforme Completo 10+) -${d15:,.0f}")

    # Prendas FUERA del bloque de uniforme equipo (otras categorias o modelos que no alcanzan 10)
    extras = [u for u in units if u.get("producto_id") not in team_pids]
    valor_extras = sum(u["precio"] for u in extras)
    n_extras = len(extras)
    total_prendas = sum(i["cantidad"] for i in items)

    # --- Lealtad 30%: solo si hay 4+ pedidos previos (esta es la 5ta venta) ---
    # NO se combina con el 20%.
    es_lealtad = False
    if "lealtad_30" in activas and (tel or "").strip():
        nped = 0
        try:
            nped = conn.execute(
                "SELECT COUNT(*) FROM pedidos WHERE IFNULL(cliente_telefono,'')=? AND IFNULL(estado,'')!='cancelado'",
                ((tel or "").strip(),),
            ).fetchone()[0]
        except Exception:
            nped = 0
        if nped >= 4:
            es_lealtad = True

    # Descuentos %: cada uno solo si cumple su condicion.
    # Con Uniforme Completo (team_pids): 20%/30% aplican SOLO sobre las prendas extras.
    # Sin equipo: 20% si total>=5; 30% lealtad sobre el restante tras 2x1/3x2.
    # Prioridad: 40% todo > lealtad 30% > 20% en 5+
    if "40_todo" in activas:
        base = max(0.0, subtotal - descuento)
        d_pct = base * 0.40
        if d_pct > 0:
            descuento += d_pct
            detalles.append(f"40% en todo -${d_pct:,.0f}")
    elif es_lealtad:
        # 30% solo si es cliente frecuente (5ta venta+)
        if team_pids:
            # Solo sobre prendas distintas al bloque de 10+ Uniforme Completo
            d_pct = valor_extras * 0.30
            if d_pct > 0:
                descuento += d_pct
                detalles.append(f"30% cliente frecuente sobre prendas extra -${d_pct:,.0f}")
        else:
            base = max(0.0, subtotal - descuento)
            d_pct = base * 0.30
            if d_pct > 0:
                descuento += d_pct
                detalles.append(f"30% cliente frecuente (5ta venta) -${d_pct:,.0f}")
    elif "20_cinco" in activas:
        # 20% solo si hay 5+ prendas en el conjunto donde aplica
        if team_pids:
            # Solo cuenta prendas DIFERENTES al modelo de 10 Uniformes Completos
            if n_extras >= 5 and valor_extras > 0:
                d_pct = valor_extras * 0.20
                descuento += d_pct
                detalles.append(f"20% en {n_extras} prendas extra (fuera de uniforme) -${d_pct:,.0f}")
        else:
            if total_prendas >= 5:
                base = max(0.0, subtotal - descuento)
                d_pct = base * 0.20
                if d_pct > 0:
                    descuento += d_pct
                    detalles.append(f"20% en 5+ prendas -${d_pct:,.0f}")

    try:
        conn.close()
    except Exception:
        pass

    if descuento > subtotal:
        descuento = subtotal
    total = max(0.0, subtotal - descuento)
    return {
        "subtotal": subtotal,
        "descuento": descuento,
        "total": total,
        "detalle": " | ".join(detalles),
    }

def totales_venta(carrito, nombre="", tel="", desc_manual=0.0):
    try:
        r = calcular_total_con_promos(carrito, nombre, tel)
    except Exception as e:
        print("promos:", e)
        r = {"subtotal": 0.0, "descuento": 0.0, "total": 0.0, "detalle": ""}
    try:
        sub = float(r.get("subtotal") or 0)
    except Exception:
        sub = 0.0
    if sub <= 0:
        try:
            sub = sum(
                float(i.get("precio") or 0) * int(i.get("cantidad") or 1)
                for i in (carrito or [])
            )
        except Exception:
            sub = 0.0
    try:
        promo = float(r.get("descuento") or 0)
    except Exception:
        promo = 0.0
    if promo > sub:
        promo = sub
    try:
        dm = float(desc_manual or 0)
    except Exception:
        dm = 0.0
    if dm < 0:
        dm = 0.0
    after = max(0.0, sub - promo)
    if dm > after:
        dm = after
    total = max(0.0, after - dm)
    return {
        "subtotal": sub,
        "descuento_promo": promo,
        "descuento_manual": dm,
        "total": total,
        "detalle": r.get("detalle") or "",
    }


# ============================================================
#  FRAMES OPTIMIZADOS
# ============================================================

class ImageViewer(ctk.CTkToplevel):
    def __init__(self, parent, image_path):
        super().__init__(parent)
        self.title("Vista imagen")
        self.geometry("600x600")
        self.grab_set()
        try:
            img = load_ctk_image(image_path, (540,540), fast=False)
            if img:
                lbl = ctk.CTkLabel(self, image=img, text="")
                lbl.image = img
                lbl.pack(padx=10, pady=10, expand=True)
        except Exception:
            ctk.CTkLabel(self, text="No se pudo cargar imagen").pack(pady=20)

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(TITULO_APP)
        self.geometry("1320x820")
        self.minsize(1100, 700)
        self.carrito = []
        self._frames = {}
        self._current = None

        nav = ctk.CTkFrame(self, width=170, corner_radius=0)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        ctk.CTkLabel(nav, text=NOMBRE_EMPRESA, font=ctk.CTkFont(size=18, weight="bold")).pack(pady=18)
        for name, key in [("Dashboard","dashboard"),("Productos","productos"),("Cotizaciones","cotizaciones"),("Carrito / Venta","venta"),("Pedidos","pedidos")]:
            ctk.CTkButton(nav, text=name, fg_color="transparent", text_color=("gray10","gray90"),
                          anchor="w", command=lambda k=key: self._mostrar_frame(k)).pack(fill="x", padx=8, pady=3)
        self.badge_carrito = ctk.CTkLabel(nav, text="Carrito: 0", text_color=VERDE, font=ctk.CTkFont(weight="bold"))
        self.badge_carrito.pack(side="bottom", pady=6, padx=8, anchor="w")
        tema = ctk.CTkLabel(nav, text="Tema", font=ctk.CTkFont(size=11))
        tema.pack(side="bottom", anchor="w", padx=8)
        self.tema_menu = ctk.CTkOptionMenu(nav, values=["Light","Dark","System"], command=self._cambiar_tema, width=120)
        self.tema_menu.pack(side="bottom", padx=8, pady=8)
        self.tema_menu.set("Light")

        self.cont = ctk.CTkFrame(self, fg_color="transparent")
        self.cont.pack(side="left", fill="both", expand=True)

        # Frames definidos más abajo, se resuelven en runtime
        self._frames["dashboard"] = DashboardFrame(self.cont, self)
        self._frames["productos"] = ProductosFrame(self.cont, self)
        self._frames["cotizaciones"] = CotizacionesFrame(self.cont, self)
        self._frames["venta"] = VentaFrame(self.cont, self)
        self._frames["pedidos"] = PedidosFrame(self.cont, self)

        for f in self._frames.values():
            f.grid(row=0, column=0, sticky="nsew")
        self.cont.grid_rowconfigure(0, weight=1)
        self.cont.grid_columnconfigure(0, weight=1)

        self._mostrar_frame("dashboard")

    def _cambiar_tema(self, v):
        ctk.set_appearance_mode(v)

    def _mostrar_frame(self, key):
        # Cancelar jobs pendientes del frame anterior si es productos
        if self._current == "productos" and "productos" in self._frames:
            try:
                self._frames["productos"]._cancel_all_jobs()
            except Exception:
                pass
        self._current = key
        self._frames[key].tkraise()
        # refrescar si es necesario
        if hasattr(self._frames[key], "_on_show"):
            try:
                self._frames[key]._on_show()
            except Exception:
                pass

    def actualizar_badge_carrito(self):
        total = sum(i.get("cantidad",0) for i in self.carrito)
        self.badge_carrito.configure(text=f"Carrito: {total}")

    def abrir_detalle(self, producto_id):
        detalle = ProductoDetalleFrame(self.cont, self, producto_id)
        detalle.grid(row=0, column=0, sticky="nsew")
        detalle.tkraise()
        self._frames["_detalle_temp"] = detalle

    def ir_carrito(self):
        if "_detalle_temp" in self._frames:
            try:
                self._frames["_detalle_temp"].destroy()
            except Exception:
                pass
            self._frames.pop("_detalle_temp", None)
        self._mostrar_frame("venta")

    def abrir_cotizacion_detalle(self, cotizacion_id):
        old = self._frames.pop("_cot_temp", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        fr = CotizacionDetalleFrame(self.cont, self, cotizacion_id)
        fr.grid(row=0, column=0, sticky="nsew")
        fr.tkraise()
        self._frames["_cot_temp"] = fr

    def abrir_pedido_detalle(self, pedido_id):
        old = self._frames.pop("_ped_temp", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        fr = PedidoDetalleFrame(self.cont, self, pedido_id)
        fr.grid(row=0, column=0, sticky="nsew")
        fr.tkraise()
        self._frames["_ped_temp"] = fr

    def volver_productos(self):
        if "_detalle_temp" in self._frames:
            try:
                self._frames["_detalle_temp"].destroy()
            except Exception:
                pass
            del self._frames["_detalle_temp"]
        self._mostrar_frame("productos")

class DatePickerDialog(ctk.CTkToplevel):
    def __init__(self, parent, on_pick=None):
        super().__init__(parent)
        self.title("Elegir fecha")
        self.geometry("300x300")
        self.grab_set()
        self.on_pick = on_pick
        self.cal = ctk.CTkFrame(self)
        self.cal.pack(fill="both", expand=True, padx=10, pady=10)
        # simple entry para fecha
        self.entry = ctk.CTkEntry(self.cal, placeholder_text="YYYY-MM-DD")
        self.entry.pack(pady=20)
        ctk.CTkButton(self.cal, text="OK", command=self._ok).pack()

    def _ok(self):
        if self.on_pick:
            self.on_pick(self.entry.get().strip())
        self.destroy()

class DashboardFrame(ctk.CTkFrame):
    def __init__(self, master, app=None, *args, **kwargs):
        super().__init__(master, corner_radius=12)
        self.app = app if app is not None else getattr(master, 'app', master)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text="Dashboard", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, sticky="w", padx=12, pady=8)
        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.grid_rowconfigure(1, weight=1)
        self._cargar()

    def _cargar(self):
        for w in self.scroll.winfo_children():
            w.destroy()
        conn = get_conn()
        total_prod = conn.execute("SELECT COUNT(*) FROM productos WHERE activo=1").fetchone()[0]
        total_ped = conn.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0]
        total_stock = conn.execute("SELECT COALESCE(SUM(stock),0) FROM producto_stock").fetchone()[0]
        low_stock = conn.execute("SELECT p.nombre, ps.talla, ps.stock FROM producto_stock ps JOIN productos p ON ps.producto_id=p.id WHERE ps.stock>0 AND ps.stock<=3 ORDER BY ps.stock LIMIT 20").fetchall()
        promos = conn.execute("SELECT * FROM promos ORDER BY id").fetchall()
        conn.close()

        # Header stats
        header = ctk.CTkFrame(self.scroll, fg_color=("gray90","gray20"), corner_radius=10)
        header.pack(fill="x", pady=6, padx=4)
        ctk.CTkLabel(header, text=f"Productos activos: {total_prod} | Pedidos: {total_ped} | Stock total: {total_stock}", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=12, pady=8)
        sub = ctk.CTkFrame(header, fg_color="transparent")
        sub.pack(fill="x", padx=12, pady=(0,8))
        ctk.CTkLabel(sub, text=f"Cache imagenes: {len(_IMAGE_CACHE)}/400", font=ctk.CTkFont(size=11), text_color="gray").pack(side="left")
        ctk.CTkButton(sub, text="Limpiar cache", width=120, height=24, fg_color="gray", command=lambda: [clear_image_cache(), self._cargar()]).pack(side="left", padx=8)
        ctk.CTkButton(sub, text="Recargar", width=80, height=24, fg_color=VERDE, command=self._cargar).pack(side="left", padx=4)

        # Stock bajo
        sec_stock = ctk.CTkFrame(self.scroll, corner_radius=10)
        sec_stock.pack(fill="x", pady=6, padx=4)
        ctk.CTkLabel(sec_stock, text="Stock bajo (≤3 unidades):", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=12, pady=(8,4))
        if not low_stock:
            ctk.CTkLabel(sec_stock, text="Sin stock bajo.", text_color="gray").pack(anchor="w", padx=12, pady=4)
        else:
            for r in low_stock:
                row = ctk.CTkFrame(sec_stock, fg_color="transparent")
                row.pack(fill="x", padx=12, pady=2)
                ctk.CTkLabel(row, text=f"• {r['nombre']} - {r['talla']}: {r['stock']}", font=ctk.CTkFont(size=12)).pack(side="left")

        # OFERTAS / PROMOS
        sec_promos = ctk.CTkFrame(self.scroll, corner_radius=10)
        sec_promos.pack(fill="x", pady=10, padx=4)
        ctk.CTkLabel(sec_promos, text="Ofertas / Promociones marcadas", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=12, pady=(10,2))
        ctk.CTkLabel(sec_promos, text="Activa/desactiva tus ofertas. Las de calendario tienen cooldown de 2 semanas.", font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w", padx=12, pady=(0,8))

        for promo in promos:
            estado_txt, estado_color = promo_estado(promo)
            card = ctk.CTkFrame(sec_promos, corner_radius=8, fg_color=("white","gray22"))
            card.pack(fill="x", padx=10, pady=6)

            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(8,2))
            var_activa = ctk.BooleanVar(value=bool(promo["activa"]))
            def toggle_activa(p_id=promo["id"], v=var_activa):
                conn = get_conn()
                conn.execute("UPDATE promos SET activa=? WHERE id=?", (1 if v.get() else 0, p_id))
                conn.commit()
                conn.close()
                self._cargar()
            chk = ctk.CTkCheckBox(top, text="", variable=var_activa, width=24, command=toggle_activa)
            chk.pack(side="left")

            ctk.CTkLabel(top, text=promo["nombre"] or promo["id"], font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=6)
            ctk.CTkLabel(top, text=estado_txt, font=ctk.CTkFont(size=11, weight="bold"), text_color=estado_color).pack(side="left", padx=8)

            btn_frame = ctk.CTkFrame(top, fg_color="transparent")
            btn_frame.pack(side="right")
            ctk.CTkButton(btn_frame, text="Editar fechas", width=90, height=24, fg_color=AZUL, command=lambda pid=promo["id"]: self._editar_fechas(pid)).pack(side="left", padx=2)
            ctk.CTkButton(btn_frame, text="Limpiar cooldown", width=110, height=24, fg_color="gray", command=lambda pid=promo["id"]: self._limpiar_cooldown(pid)).pack(side="left", padx=2)

            ctk.CTkLabel(card, text=promo["descripcion"] or "", font=ctk.CTkFont(size=11), text_color="gray", wraplength=600, justify="left").pack(anchor="w", padx=38, pady=(0,2))

            fechas_txt = f"Inicio: {promo['inicio'] or '—'} | Fin: {promo['fin'] or '—'} | Ultimo uso: {promo['ultimo_uso'] or 'Nunca'}"
            if promo_es_permanente(promo["id"]):
                fechas_txt = "Permanente: 15% solo Uniforme Completo 10+ mismo modelo | 20% si 5+ prendas fuera de ese bloque | 30% lealtad desde 5ta venta (reemplaza 20%)"
            ctk.CTkLabel(card, text=fechas_txt, font=ctk.CTkFont(size=10), text_color="gray").pack(anchor="w", padx=38, pady=(0,8))

        ctk.CTkButton(sec_promos, text="Restaurar ofertas por defecto", width=200, height=28, fg_color="gray", command=self._restaurar_promos).pack(pady=8)

    def _editar_fechas(self, promo_id):
        win = ctk.CTkToplevel(self)
        win.title(f"Editar {promo_id}")
        win.geometry("380x260")
        win.grab_set()
        conn = get_conn()
        row = conn.execute("SELECT * FROM promos WHERE id=?", (promo_id,)).fetchone()
        conn.close()
        if not row:
            return
        ctk.CTkLabel(win, text=row["nombre"], font=ctk.CTkFont(weight="bold")).pack(pady=10)
        ctk.CTkLabel(win, text="Fecha inicio (YYYY-MM-DD o vacia):").pack()
        e_ini = ctk.CTkEntry(win, width=200)
        e_ini.pack(pady=2)
        if row["inicio"]:
            e_ini.insert(0, row["inicio"])
        ctk.CTkLabel(win, text="Fecha fin (YYYY-MM-DD o vacia):").pack()
        e_fin = ctk.CTkEntry(win, width=200)
        e_fin.pack(pady=2)
        if row["fin"]:
            e_fin.insert(0, row["fin"])

        def guardar():
            ini = e_ini.get().strip() or None
            fin = e_fin.get().strip() or None
            if ini and not _parse_fecha(ini):
                messagebox.showerror("Fecha", "Formato inicio invalido. Usa YYYY-MM-DD")
                return
            if fin and not _parse_fecha(fin):
                messagebox.showerror("Fecha", "Formato fin invalido. Usa YYYY-MM-DD")
                return
            conn = get_conn()
            conn.execute("UPDATE promos SET inicio=?, fin=? WHERE id=?", (ini, fin, promo_id))
            conn.commit()
            conn.close()
            win.destroy()
            self._cargar()
            messagebox.showinfo("Guardado", "Fechas actualizadas")

        ctk.CTkButton(win, text="Guardar", fg_color=VERDE, command=guardar).pack(pady=12)
        ctk.CTkButton(win, text="Cancelar", fg_color="gray", command=win.destroy).pack()

    def _limpiar_cooldown(self, promo_id):
        if not messagebox.askyesno("Limpiar cooldown", f"Limpiar el cooldown de {promo_id}? Podra usarse inmediatamente."):
            return
        conn = get_conn()
        conn.execute("UPDATE promos SET ultimo_uso=NULL WHERE id=?", (promo_id,))
        conn.commit()
        conn.close()
        self._cargar()
        messagebox.showinfo("Listo", "Cooldown limpiado")

    def _restaurar_promos(self):
        if not messagebox.askyesno("Restaurar", "Restaurar todas las ofertas por defecto? Se mantiene activa/inactiva pero se restauran nombres y descripciones."):
            return
        conn = get_conn()
        for pid, d in PROMO_DEFS.items():
            conn.execute("INSERT OR IGNORE INTO promos (id, nombre, descripcion, activa) VALUES (?,?,?,1)", (pid, d["nombre"], d["desc"]))
            conn.execute("UPDATE promos SET nombre=?, descripcion=? WHERE id=?", (d["nombre"], d["desc"], pid))
        conn.commit()
        conn.close()
        self._cargar()
        messagebox.showinfo("Restaurado", "Ofertas restauradas")


class ProductosFrame(ctk.CTkFrame):
    def __init__(self, master, app=None):
        super().__init__(master, corner_radius=12)
        # app es la instancia de App pasada desde App.__init__
        if app is not None:
            self.app = app
        else:
            # fallback: si master tiene app, usarla, si master es App, usar master
            self.app = getattr(master, 'app', master)
            if isinstance(master, App):
                self.app = app if app is not None else getattr(master, 'app', master)

        self.filtro_activo = None
        self.seleccion = {}
        self._modo_todos_productos = False
        self._ids_todos_productos = []
        self._img_jobs = []
        self._search_after = None
        self._current_page = 1
        self._per_page = 60  # 5 columnas x 12 filas
        self._total_productos = 0
        self._total_pages = 1

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 2))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Productos", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, sticky="w")
        btns_h = ctk.CTkFrame(header, fg_color="transparent")
        btns_h.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(btns_h, text="Exportar Catalogo Completo", width=190, height=28, fg_color=AZUL, hover_color="#1d4ed8",
                      command=self._exportar_catalogo).pack(side="left", padx=2)
        ctk.CTkButton(btns_h, text="Exportar por Categoria", width=160, height=28, fg_color="#0d9488", hover_color="#0f766e",
                      command=self._exportar_catalogo_categoria).pack(side="left", padx=2)
        ctk.CTkButton(btns_h, text="Datos JSON", width=100, height=28, fg_color="#7c3aed", hover_color="#6d28d9",
                      command=self._menu_datos_json).pack(side="left", padx=2)
        ctk.CTkButton(btns_h, text="Fotos ZIP", width=95, height=28, fg_color="#c2410c", hover_color="#9a3412",
                      command=self._menu_fotos_zip).pack(side="left", padx=2)
        ctk.CTkButton(btns_h, text="+ Nuevo", width=90, height=28, fg_color=VERDE, hover_color=VERDE_HOVER,
                      command=self._nuevo).pack(side="left", padx=2)

        # Barra acciones seleccion
        acciones = ctk.CTkFrame(self, fg_color=("gray90", "gray20"), corner_radius=8)
        acciones.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        ctk.CTkLabel(acciones, text="Seleccion:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=8)
        ctk.CTkButton(acciones, text="Exportar fichas (PNG)", width=150, height=28, fg_color=AZUL, hover_color="#1d4ed8",
                      command=self._exportar_seleccion).pack(side="left", padx=3, pady=6)
        ctk.CTkButton(acciones, text="Exportar fotos originales", width=170, height=28, fg_color="#c2410c", hover_color="#9a3412",
                      command=self._exportar_fotos_seleccion).pack(side="left", padx=3, pady=6)
        ctk.CTkButton(acciones, text="Exportar JSON seleccion", width=155, height=28, fg_color="#7c3aed", hover_color="#6d28d9",
                      command=self._exportar_json_seleccion).pack(side="left", padx=3, pady=6)
        ctk.CTkButton(acciones, text="Editar categoria", width=120, height=28,
                      command=self._editar_categoria_sel).pack(side="left", padx=3, pady=6)
        ctk.CTkButton(acciones, text="Editar filtro", width=110, height=28,
                      command=self._editar_filtro_sel).pack(side="left", padx=3, pady=6)
        self.btn_eliminar = ctk.CTkButton(
            acciones, text="Eliminar seleccionados", width=150, height=28,
            fg_color=ROJO, hover_color=ROJO_HOVER, command=self._eliminar_seleccion
        )
        self.btn_eliminar.pack(side="left", padx=3, pady=6)
        ctk.CTkButton(acciones, text="Todos (Categoria)", width=130, height=28, fg_color="gray",
                      command=lambda: self._toggle_todos(True)).pack(side="left", padx=3)
        ctk.CTkButton(
            acciones, text="Seleccionar todos los productos", width=210, height=28, fg_color="#0d9488",
            hover_color="#0f766e", command=self._seleccionar_todos_productos
        ).pack(side="left", padx=3)
        ctk.CTkButton(acciones, text="Ninguno", width=70, height=28, fg_color="gray",
                      command=lambda: self._toggle_todos(False)).pack(side="left", padx=3)

        filtros = ctk.CTkFrame(self, fg_color="transparent")
        filtros.grid(row=2, column=0, sticky="ew", padx=12, pady=2)
        self.busqueda = ctk.CTkEntry(filtros, placeholder_text="Buscar... (Enter)", width=220)
        self.busqueda.pack(side="left", padx=(0, 6))
        self.busqueda.bind("<Return>", lambda e: self._ir_primera_pagina())
        def _on_key(_e=None):
            if self._search_after:
                try:
                    self.after_cancel(self._search_after)
                except Exception:
                    pass
            self._search_after = self.after(400, self._ir_primera_pagina)
        self.busqueda.bind("<KeyRelease>", _on_key)
        self.cat_var = ctk.StringVar(value="Todas")
        ctk.CTkOptionMenu(filtros, values=["Todas"] + CATEGORIAS, variable=self.cat_var,
                          command=lambda _: self._ir_primera_pagina(), width=150).pack(side="left", padx=3)
        ctk.CTkButton(filtros, text="Buscar", width=70, height=28, fg_color=VERDE, hover_color=VERDE_HOVER,
                      command=self._ir_primera_pagina).pack(side="left", padx=3)
        ctk.CTkLabel(filtros, text="Por pág:", font=ctk.CTkFont(size=11)).pack(side="left", padx=(12,2))
        self.per_page_var = ctk.StringVar(value="60")
        ctk.CTkOptionMenu(filtros, values=["30","60","100","200"], variable=self.per_page_var,
                          command=self._cambiar_per_page, width=70).pack(side="left", padx=2)

        self.filtros_row = ctk.CTkFrame(self, height=36)
        self.filtros_row.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 2))

        # Paginación controles
        self.pagin_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.pagin_frame.grid(row=4, column=0, sticky="ew", padx=12, pady=2)
        self.pagin_frame.grid_columnconfigure(1, weight=1)
        self.btn_prev = ctk.CTkButton(self.pagin_frame, text="◀ Anterior", width=90, height=26, fg_color="gray", command=self._prev_page)
        self.btn_prev.grid(row=0, column=0, padx=2)
        self.lbl_pagin = ctk.CTkLabel(self.pagin_frame, text="Página 1/1 - 0 productos", font=ctk.CTkFont(size=12))
        self.lbl_pagin.grid(row=0, column=1)
        self.btn_next = ctk.CTkButton(self.pagin_frame, text="Siguiente ▶", width=90, height=26, fg_color="gray", command=self._next_page)
        self.btn_next.grid(row=0, column=2, padx=2)

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.grid(row=5, column=0, sticky="nsew", padx=8, pady=(0, 8))
        for i in range(5):
            self.scroll.grid_columnconfigure(i, weight=1)

        self._cargar()

    def _on_show(self):
        # Al volver a mostrar, no recargar todo si ya hay datos
        pass

    def _cancel_all_jobs(self):
        for aid in getattr(self, "_img_jobs", []):
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        self._img_jobs = []
        if getattr(self, "_search_after", None):
            try:
                self.after_cancel(self._search_after)
            except Exception:
                pass
            self._search_after = None

    def _ir_primera_pagina(self):
        self._current_page = 1
        self._cargar()

    def _cambiar_per_page(self, val):
        try:
            self._per_page = int(val)
        except Exception:
            self._per_page = 60
        self._current_page = 1
        self._cargar()

    def _prev_page(self):
        if self._current_page > 1:
            self._current_page -= 1
            self._cargar()

    def _next_page(self):
        if self._current_page < self._total_pages:
            self._current_page += 1
            self._cargar()

    def _ids_seleccionados(self):
        if getattr(self, "_modo_todos_productos", False) and self._ids_todos_productos:
            return list(self._ids_todos_productos)
        return [pid for pid, var in self.seleccion.items() if var.get()]

    def _set_bloqueo_eliminar(self, bloquear):
        self._modo_todos_productos = bool(bloquear)
        btn = getattr(self, "btn_eliminar", None)
        if btn is None:
            return
        if bloquear:
            btn.configure(state="disabled", fg_color="gray")
        else:
            btn.configure(state="normal", fg_color=ROJO)

    def _seleccionar_todos_productos(self):
        """Marca TODOS los productos activos del catalogo. Bloquea eliminar."""
        conn = get_conn()
        rows = conn.execute("SELECT id FROM productos WHERE activo=1").fetchall()
        conn.close()
        ids = [r["id"] for r in rows]
        if not ids:
            messagebox.showwarning("Seleccion", "No hay productos cargados.")
            return
        self._ids_todos_productos = ids
        for pid in ids:
            if pid in self.seleccion:
                self.seleccion[pid].set(True)
            else:
                self.seleccion[pid] = ctk.BooleanVar(value=True)
        self._set_bloqueo_eliminar(True)
        messagebox.showinfo(
            "Seleccion",
            f"Se seleccionaron {len(ids)} producto(s).\n"
            "Puedes Exportar imagenes.\n"
            "Eliminar esta bloqueado en esta seleccion."
        )

    def _toggle_todos(self, valor):
        """Selecciona todos los productos de la categoria/filtro actual (no solo la pagina)."""
        self._ids_todos_productos = []
        self._set_bloqueo_eliminar(False)
        if not valor:
            for var in self.seleccion.values():
                var.set(False)
            return
        sql, params = self._build_sql(count=False, paginar=False)
        conn = get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception as e:
            print("toggle todos:", e)
            rows = []
        conn.close()
        for r in rows:
            pid = r["id"]
            if pid in self.seleccion:
                self.seleccion[pid].set(True)
            else:
                self.seleccion[pid] = ctk.BooleanVar(value=True)

    def _exportar_seleccion(self):
        ids = self._ids_seleccionados()
        if not ids:
            messagebox.showwarning("Seleccion", "Marca al menos un producto.")
            return
        default = f"seleccion_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
        ruta = filedialog.asksaveasfilename(
            title="Exportar seleccion como imagenes (ZIP)",
            defaultextension=".zip",
            initialfile=default,
            filetypes=[("ZIP", "*.zip")]
        )
        if not ruta:
            return
        try:
            conn = get_conn()
            prods = []
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                placeholders = ",".join("?" * len(chunk))
                prods.extend(conn.execute(
                    f"""SELECT p.*, c.nombre as categoria FROM productos p
                        LEFT JOIN categorias c ON p.categoria_id=c.id
                        WHERE p.id IN ({placeholders})""",
                    chunk,
                ).fetchall())
            conn.close()
            usados = set()
            with zipfile.ZipFile(ruta, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    for p in prods:
                        safe = nombre_archivo_seguro(p["nombre"], usados)
                        local = tmp_path / f"{safe}.png"
                        exportar_prenda_imagen(p, str(local))
                        zf.write(local, arcname=f"{safe}.png")
            messagebox.showinfo("Listo", f"Exportados {len(prods)} producto(s) en:\n{ruta}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _eliminar_seleccion(self):
        if getattr(self, "_modo_todos_productos", False):
            messagebox.showwarning(
                "Bloqueado",
                "Eliminar esta bloqueado cuando seleccionas todos los productos.\n"
                "Usa Ninguno o Todos (Categoria) para una seleccion mas pequena."
            )
            return
        ids = self._ids_seleccionados()
        if not ids:
            messagebox.showwarning("Seleccion", "Marca al menos un producto.")
            return
        if not messagebox.askyesno("Eliminar", f"Eliminar {len(ids)} producto(s) del catalogo?"):
            return
        conn = get_conn()
        for pid in ids:
            conn.execute("UPDATE productos SET activo=0 WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        if hasattr(self.app, 'carrito'):
            self.app.carrito = [i for i in self.app.carrito if i.get("producto_id") not in set(ids)]
            self.app.actualizar_badge_carrito()
        messagebox.showinfo("Eliminados", f"{len(ids)} producto(s) eliminados.")
        self._cargar()

    def _editar_categoria_sel(self):
        ids = self._ids_seleccionados()
        if not ids:
            messagebox.showwarning("Seleccion", "Marca al menos un producto.")
            return
        win = ctk.CTkToplevel(self)
        win.title("Editar categoria")
        win.geometry("360x180")
        win.grab_set()
        ctk.CTkLabel(win, text=f"Mover {len(ids)} producto(s) a:", font=ctk.CTkFont(weight="bold")).pack(pady=12)
        cat_var = ctk.StringVar(value=CATEGORIAS[0])
        ctk.CTkOptionMenu(win, values=CATEGORIAS, variable=cat_var, width=240).pack(pady=6)

        def ok():
            conn = get_conn()
            row = conn.execute("SELECT id FROM categorias WHERE nombre=?", (cat_var.get(),)).fetchone()
            if not row:
                conn.close()
                messagebox.showerror("Error", "Categoria no encontrada")
                return
            cid = row["id"]
            for pid in ids:
                conn.execute("UPDATE productos SET categoria_id=? WHERE id=?", (cid, pid,))
            for pid in ids:
                r = conn.execute(
                    """SELECT p.filtro, c.nombre as categoria FROM productos p
                       LEFT JOIN categorias c ON p.categoria_id=c.id WHERE p.id=?""",
                    (pid,),
                ).fetchone()
                if r:
                    conn.execute(
                        "UPDATE productos SET precio=? WHERE id=?",
                        (precio_para_producto(r["filtro"], r["categoria"]), pid),
                    )
            conn.commit()
            conn.close()
            win.destroy()
            messagebox.showinfo("Listo", "Categoria actualizada.")
            self._cargar()

        ctk.CTkButton(win, text="Aplicar", fg_color=VERDE, hover_color=VERDE_HOVER, command=ok).pack(pady=12)

    def _editar_filtro_sel(self):
        ids = self._ids_seleccionados()
        if not ids:
            messagebox.showwarning("Seleccion", "Marca al menos un producto.")
            return
        win = ctk.CTkToplevel(self)
        win.title("Editar filtro")
        win.geometry("380x220")
        win.grab_set()
        ctk.CTkLabel(win, text=f"Asignar filtro a {len(ids)} producto(s):", font=ctk.CTkFont(weight="bold")).pack(pady=12)
        vals = ["Sin filtro"] + lista_filtros_disponibles()
        fvar = ctk.StringVar(value=vals[0])
        ctk.CTkComboBox(win, values=vals, variable=fvar, width=260).pack(pady=4)
        ctk.CTkLabel(win, text="O escribe uno nuevo:", font=ctk.CTkFont(size=11), text_color="gray").pack()
        nuevo = ctk.CTkEntry(win, width=260, placeholder_text="Filtro nuevo")
        nuevo.pack(pady=4)

        def ok():
            f = (nuevo.get() or "").strip() or (fvar.get() or "").strip()
            if not f or f == "Sin filtro":
                f = None
            conn = get_conn()
            for pid in ids:
                conn.execute("UPDATE productos SET filtro=? WHERE id=?", (f, pid))
                r = conn.execute(
                    """SELECT p.filtro, c.nombre as categoria FROM productos p
                       LEFT JOIN categorias c ON p.categoria_id=c.id WHERE p.id=?""",
                    (pid,),
                ).fetchone()
                if r:
                    conn.execute(
                        "UPDATE productos SET precio=? WHERE id=?",
                        (precio_para_producto(r["filtro"], r["categoria"]), pid),
                    )
            conn.commit()
            conn.close()
            win.destroy()
            messagebox.showinfo("Listo", "Filtro actualizado.")
            self._cargar()

        ctk.CTkButton(win, text="Aplicar", fg_color=VERDE, hover_color=VERDE_HOVER, command=ok).pack(pady=12)

    def _pintar_filtros(self, force=False):
        conn = get_conn()
        rows = conn.execute(
            """SELECT DISTINCT filtro FROM productos
               WHERE activo=1 AND filtro IS NOT NULL AND TRIM(filtro) != ''
               ORDER BY filtro COLLATE NOCASE"""
        ).fetchall()
        conn.close()
        nombres = [r["filtro"] for r in rows]
        sig = (tuple(nombres), self.filtro_activo)
        if not force and getattr(self, "_filtros_sig", None) == sig and self.filtros_row.winfo_children():
            return
        self._filtros_sig = sig
        for w in self.filtros_row.winfo_children():
            w.destroy()
        es_todos = self.filtro_activo is None
        ctk.CTkButton(
            self.filtros_row, text="Todos", width=60, height=26,
            fg_color=VERDE if es_todos else ("gray75", "gray30"),
            text_color=("white", "white") if es_todos else ("gray10", "gray90"),
            hover_color=VERDE_HOVER, command=lambda: self._set_filtro(None)
        ).pack(side="left", padx=2, pady=2)
        for nombre in nombres[:40]:  # limitar botones para no saturar UI con 5000 productos
            activo = self.filtro_activo == nombre
            ctk.CTkButton(
                self.filtros_row, text=nombre, width=max(70, min(120, len(nombre) * 8)), height=26,
                fg_color=VERDE if activo else ("gray75", "gray30"),
                text_color=("white", "white") if activo else ("gray10", "gray90"),
                hover_color=VERDE_HOVER, command=lambda n=nombre: self._set_filtro(n)
            ).pack(side="left", padx=2, pady=2)

    def _set_filtro(self, nombre):
        self.filtro_activo = nombre
        self._current_page = 1
        self._pintar_filtros(force=True)
        self._cargar()

    def _build_sql(self, count=False, paginar=True):
        q = self.busqueda.get().strip()
        cat = self.cat_var.get()
        if count:
            sql = "SELECT COUNT(*) as cnt FROM productos p LEFT JOIN categorias c ON p.categoria_id=c.id WHERE p.activo=1"
        else:
            sql = "SELECT p.id, p.nombre, p.filtro, p.precio, p.version_jugador FROM productos p LEFT JOIN categorias c ON p.categoria_id=c.id WHERE p.activo=1"
        params = []
        if q:
            sql += " AND (p.nombre LIKE ? OR p.modelo LIKE ? OR IFNULL(p.filtro,'') LIKE ?)"
            params += [f"%{q}%", f"%{q}%", f"%{q}%"]
        if cat != "Todas":
            sql += " AND c.nombre=?"
            params.append(cat)
        if self.filtro_activo:
            sql += " AND p.filtro = ?"
            params.append(self.filtro_activo)
        if not count:
            sql += " ORDER BY p.id DESC"
            if paginar:
                sql += " LIMIT ? OFFSET ?"
        return sql, params

    def _cargar(self):
        # Cancelar jobs previos
        self._cancel_all_jobs()
        self.seleccion = {}
        self._pintar_filtros()
        for w in self.scroll.winfo_children():
            w.destroy()

        # 1) Contar total
        sql_cnt, params_cnt = self._build_sql(count=True)
        conn = get_conn()
        try:
            cnt_row = conn.execute(sql_cnt, params_cnt).fetchone()
            self._total_productos = cnt_row["cnt"] if cnt_row else 0
        except Exception:
            self._total_productos = 0
        
        self._total_pages = max(1, (self._total_productos + self._per_page - 1) // self._per_page)
        if self._current_page > self._total_pages:
            self._current_page = self._total_pages
        if self._current_page < 1:
            self._current_page = 1

        offset = (self._current_page - 1) * self._per_page
        sql, params = self._build_sql(count=False)
        params = params + [self._per_page, offset]

        try:
            prods = conn.execute(sql, params).fetchall()
        except Exception as e:
            print("SQL error:", e)
            prods = []
        conn.close()

        # Actualizar paginación label
        start = offset + 1 if self._total_productos else 0
        end = min(offset + self._per_page, self._total_productos)
        self.lbl_pagin.configure(text=f"Página {self._current_page}/{self._total_pages} - Mostrando {start}-{end} de {self._total_productos} productos")
        self.btn_prev.configure(state="normal" if self._current_page > 1 else "disabled")
        self.btn_next.configure(state="normal" if self._current_page < self._total_pages else "disabled")

        if not prods:
            msg = "No hay productos en este filtro." if self.filtro_activo else "No hay productos."
            ctk.CTkLabel(self.scroll, text=msg).pack(pady=30)
            return

        ids = [p["id"] for p in prods]
        self._img_map = mapa_primeras_imagenes(ids)
        self._prod_rows = list(prods)

        for idx, p in enumerate(prods):
            self._card(p, idx)

        if getattr(self, "_modo_todos_productos", False):
            for pid in list(self.seleccion.keys()):
                try:
                    self.seleccion[pid].set(True)
                except Exception:
                    pass

        self._schedule_images(list(range(len(prods))))

    def _schedule_images(self, indices):
        if not indices:
            return
        batch = indices[:8]  # 8 a la vez, más rápido pero sin bloquear
        rest = indices[8:]
        for idx in batch:
            try:
                # Buscar card por fila/col
                row = idx // 5
                col = idx % 5
                # grid_slaves devuelve lista
                cards = self.scroll.grid_slaves(row=row, column=col)
                if not cards:
                    continue
                card = cards[0]
                ph = getattr(card, "_ph", None)
                pid = getattr(card, "_pid", None)
                if ph is None or pid is None:
                    continue
                # Verificar que widget aún existe
                if not ph.winfo_exists():
                    continue
                ruta = self._img_map.get(pid)
                if not ruta or not (UPLOADS_DIR / ruta).exists():
                    continue
                img = load_ctk_image(UPLOADS_DIR / ruta, IMG_LISTA, fast=True)
                if img:
                    ph.configure(image=img, text="")
                    ph.image = img
            except Exception:
                pass
        if rest:
            jid = self.after(20, lambda: self._schedule_images(rest))
            self._img_jobs.append(jid)

    def _card(self, p, idx):
        col, row = idx % 5, idx // 5
        card = ctk.CTkFrame(self.scroll, corner_radius=6, height=64)
        card.grid(row=row, column=col, padx=3, pady=3, sticky="nsew")
        card.grid_propagate(False)
        card._pid = p["id"]

        var = ctk.BooleanVar(value=False)
        self.seleccion[p["id"]] = var

        def open_p(e=None, pid=p["id"]):
            # Cancelar jobs al abrir detalle para liberar CPU
            self._cancel_all_jobs()
            if hasattr(self.app, 'abrir_detalle'):
                self.app.abrir_detalle(pid)
            else:
                # fallback
                self.master.master.app.abrir_detalle(pid)

        left = ctk.CTkFrame(card, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        top = ctk.CTkFrame(left, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkCheckBox(top, text="", variable=var, width=22, checkbox_width=18, checkbox_height=18).pack(side="left")
        name = ctk.CTkLabel(
            top, text=(p["nombre"] or "")[:22],
            font=ctk.CTkFont(size=11, weight="bold"), anchor="w"
        )
        name.pack(side="left", fill="x", expand=True)
        name.bind("<Button-1>", open_p)

        pa = precio_aficion(p)
        if tiene_version_jugador(p):
            txt_precio = f"${pa:,.0f} / J${precio_jugador(p):,.0f}"
        else:
            txt_precio = f"${pa:,.0f}"
        price_lbl = ctk.CTkLabel(left, text=txt_precio, font=ctk.CTkFont(size=11, weight="bold"),
                                 text_color=VERDE, anchor="w")
        price_lbl.pack(fill="x")
        price_lbl.bind("<Button-1>", open_p)

        filtro_txt = (p["filtro"] or "Sin filtro")[:18]
        tag = ctk.CTkLabel(left, text=filtro_txt, font=ctk.CTkFont(size=9), text_color="gray", anchor="w")
        tag.pack(fill="x")
        tag.bind("<Button-1>", open_p)

        ph = ctk.CTkLabel(card, text="...", width=56, height=56, font=ctk.CTkFont(size=9), text_color="gray")
        ph.pack(side="right", padx=4, pady=4)
        ph.bind("<Button-1>", open_p)
        card._ph = ph
        card.bind("<Button-1>", open_p)

    def _nuevo(self):
        self._cancel_all_jobs()
        ProductoForm(self, on_save=self._cargar)

    def _exportar_catalogo(self):
        default = "Catalogo Sports Elite Completo.pdf"
        ruta = filedialog.asksaveasfilename(
            parent=self,
            title="Guardar Catalogo Completo",
            defaultextension=".pdf",
            initialfile=default,
            filetypes=[("PDF", "*.pdf")]
        )
        if not ruta:
            return
        try:
            exportar_catalogo_pdf(ruta)
            messagebox.showinfo("Catalogo exportado", "Catalogo completo guardado en:\n" + ruta)
        except Exception as e:
            messagebox.showerror("Error", "No se pudo exportar: " + str(e))

    def _exportar_catalogo_categoria(self):
        win = ctk.CTkToplevel(self)
        win.title("Exportar catalogo por categoria")
        win.geometry("440x280")
        try:
            win.transient(self.winfo_toplevel())
            win.lift()
            win.focus_force()
        except Exception:
            pass
        ctk.CTkLabel(
            win, text="Elige que exportar:",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(pady=(16, 6))
        actual = self.cat_var.get() if getattr(self, "cat_var", None) and self.cat_var.get() != "Todas" else CATEGORIAS[0]
        cat_var = ctk.StringVar(value=actual if actual in CATEGORIAS else CATEGORIAS[0])

        ctk.CTkLabel(win, text="Categoria", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=90)
        ctk.CTkOptionMenu(
            win, values=CATEGORIAS, variable=cat_var, width=260,
            command=lambda _=None: refresh_filtros()
        ).pack(pady=(0, 8))

        ctk.CTkLabel(win, text="Filtro (opcional)", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=90)
        filtro_box = ctk.CTkComboBox(win, values=["Toda la categoria"], width=260)
        filtro_box.pack(pady=(0, 4))
        filtro_box.set("Toda la categoria")
        ctk.CTkLabel(
            win, text="Si no eliges filtro, se exporta toda la categoria.",
            font=ctk.CTkFont(size=11), text_color="gray"
        ).pack()

        def filtros_de_categoria(categoria):
            conn = get_conn()
            rows = conn.execute(
                """SELECT DISTINCT p.filtro FROM productos p
                   LEFT JOIN categorias c ON p.categoria_id=c.id
                   WHERE p.activo=1 AND c.nombre=?
                   ORDER BY p.filtro COLLATE NOCASE""",
                (categoria,),
            ).fetchall()
            conn.close()
            out = []
            hay_sin = False
            for r in rows:
                f = (r["filtro"] or "").strip()
                if not f or f.lower() in ("sin filtro", "sin_filtro", "-", "no filtro"):
                    hay_sin = True
                    continue
                if f not in out:
                    out.append(f)
            if hay_sin:
                out.append("Sin filtro")
            return out

        def refresh_filtros(_=None):
            vals = ["Toda la categoria"] + filtros_de_categoria(cat_var.get())
            try:
                filtro_box.configure(values=vals)
            except Exception:
                pass
            filtro_box.set("Toda la categoria")

        refresh_filtros()

        def ok():
            cat = cat_var.get()
            fraw = (filtro_box.get() or "").strip()
            filtro = None if (not fraw or fraw == "Toda la categoria") else fraw
            win.destroy()
            safe_cat = re.sub(r"[^A-Za-z0-9_-]+", "_", cat)
            if filtro:
                safe_f = re.sub(r"[^A-Za-z0-9_-]+", "_", filtro)
                default = f"Catalogo Sports Elite - {safe_cat} - {safe_f}.pdf"
                titulo = f"Guardar catalogo de {cat} / {filtro}"
            else:
                default = f"Catalogo Sports Elite - {safe_cat}.pdf"
                titulo = f"Guardar catalogo de {cat}"
            ruta = filedialog.asksaveasfilename(
                parent=self,
                title=titulo,
                defaultextension=".pdf",
                initialfile=default,
                filetypes=[("PDF", "*.pdf")]
            )
            if not ruta:
                return
            try:
                exportar_catalogo_pdf(ruta, categoria=cat, filtro=filtro)
                extra = f" / {filtro}" if filtro else ""
                messagebox.showinfo("Catalogo exportado", f"Catalogo de {cat}{extra} guardado en:\n{ruta}")
            except Exception as e:
                messagebox.showerror("Error", "No se pudo exportar: " + str(e))

        ctk.CTkButton(
            win, text="Exportar PDF", width=160, height=34,
            fg_color=VERDE, hover_color=VERDE_HOVER, command=ok
        ).pack(pady=14)

    # ----- Datos JSON / Fotos ZIP -----

    def _menu_datos_json(self):
        win = ctk.CTkToplevel(self)
        win.title("Datos de productos (JSON)")
        win.geometry("420x320")
        try:
            win.transient(self.winfo_toplevel())
            win.lift()
            win.focus_force()
        except Exception:
            pass
        ctk.CTkLabel(win, text="Exportar / Importar catalogo en JSON",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(16, 8))
        ctk.CTkLabel(
            win,
            text="El JSON incluye nombre, categoria, filtro, precio,\nstock por talla y nombres de archivos de imagen.",
            font=ctk.CTkFont(size=12), text_color="gray"
        ).pack(pady=4)

        ctk.CTkButton(
            win, text="Exportar TODO a JSON", width=260, height=36,
            fg_color="#7c3aed", hover_color="#6d28d9",
            command=lambda: (win.destroy(), self._exportar_json_dialog())
        ).pack(pady=6)
        ctk.CTkButton(
            win, text="Exportar por categoria / filtro (JSON)", width=260, height=36,
            fg_color="#7c3aed", hover_color="#6d28d9",
            command=lambda: (win.destroy(), self._exportar_json_categoria_dialog())
        ).pack(pady=6)
        ctk.CTkButton(
            win, text="Importar desde JSON (+ ZIP de fotos opcional)", width=300, height=36,
            fg_color=VERDE, hover_color=VERDE_HOVER,
            command=lambda: (win.destroy(), self._importar_json_dialog())
        ).pack(pady=6)
        ctk.CTkButton(win, text="Cerrar", width=100, fg_color="gray", command=win.destroy).pack(pady=10)

    def _menu_fotos_zip(self):
        win = ctk.CTkToplevel(self)
        win.title("Exportar fotos (ZIP)")
        win.geometry("440x340")
        try:
            win.transient(self.winfo_toplevel())
            win.lift()
            win.focus_force()
        except Exception:
            pass
        ctk.CTkLabel(win, text="Exportar fotografias originales",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(16, 6))
        ctk.CTkLabel(
            win,
            text="Se genera un .ZIP (compatible y sin programas extra).\n"
                 "Carpeta: imagenes/{id_producto}/archivo.jpg + productos.json\n"
                 "Las fotos originales de uploads/ NO se modifican.",
            font=ctk.CTkFont(size=12), text_color="gray"
        ).pack(pady=4)
        ctk.CTkButton(
            win, text="ZIP de TODAS las fotos", width=280, height=36,
            fg_color="#c2410c", hover_color="#9a3412",
            command=lambda: (win.destroy(), self._exportar_fotos_dialog())
        ).pack(pady=6)
        ctk.CTkButton(
            win, text="ZIP por categoria / filtro", width=280, height=36,
            fg_color="#c2410c", hover_color="#9a3412",
            command=lambda: (win.destroy(), self._exportar_fotos_categoria_dialog())
        ).pack(pady=6)
        ctk.CTkButton(
            win, text="ZIP de la seleccion actual", width=280, height=36,
            fg_color="#c2410c", hover_color="#9a3412",
            command=lambda: (win.destroy(), self._exportar_fotos_seleccion())
        ).pack(pady=6)
        ctk.CTkButton(win, text="Cerrar", width=100, fg_color="gray", command=win.destroy).pack(pady=10)

    def _exportar_json_dialog(self, categoria=None, filtro=None, solo_ids=None):
        default = "productos_sportelite.json"
        if categoria:
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", categoria)
            default = f"productos_{safe}.json"
            if filtro:
                safe_f = re.sub(r"[^A-Za-z0-9_-]+", "_", filtro)
                default = f"productos_{safe}_{safe_f}.json"
        if solo_ids:
            default = f"productos_seleccion_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        ruta = filedialog.asksaveasfilename(
            parent=self,
            title="Guardar datos JSON",
            defaultextension=".json",
            initialfile=default,
            filetypes=[("JSON", "*.json")],
        )
        if not ruta:
            return
        try:
            n = exportar_productos_json(ruta, categoria=categoria, filtro=filtro, solo_ids=solo_ids)
            messagebox.showinfo("JSON exportado", f"{n} producto(s) guardados en:\n{ruta}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _exportar_json_seleccion(self):
        ids = self._ids_seleccionados()
        if not ids:
            messagebox.showwarning("Seleccion", "Marca al menos un producto.")
            return
        self._exportar_json_dialog(solo_ids=ids)

    def _exportar_json_categoria_dialog(self):
        self._dialogo_categoria_filtro(
            titulo="Exportar JSON por categoria",
            boton="Exportar JSON",
            on_ok=lambda cat, filtro: self._exportar_json_dialog(categoria=cat, filtro=filtro),
        )

    def _exportar_fotos_dialog(self, categoria=None, filtro=None, solo_ids=None):
        default = f"fotos_sportelite_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
        if categoria:
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", categoria)
            default = f"fotos_{safe}.zip"
            if filtro:
                safe_f = re.sub(r"[^A-Za-z0-9_-]+", "_", filtro)
                default = f"fotos_{safe}_{safe_f}.zip"
        if solo_ids:
            default = f"fotos_seleccion_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
        ruta = filedialog.asksaveasfilename(
            parent=self,
            title="Guardar fotos (ZIP)",
            defaultextension=".zip",
            initialfile=default,
            filetypes=[("ZIP", "*.zip")],
        )
        if not ruta:
            return
        try:
            r = exportar_imagenes_zip(ruta, categoria=categoria, filtro=filtro, solo_ids=solo_ids, incluir_json=True)
            msg = (
                f"Productos: {r['productos']}\n"
                f"Imagenes incluidas: {r['imagenes']}\n"
                f"Faltantes en disco: {r['faltantes']}\n\n"
                f"Archivo:\n{ruta}"
            )
            messagebox.showinfo("ZIP de fotos listo", msg)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _exportar_fotos_seleccion(self):
        ids = self._ids_seleccionados()
        if not ids:
            messagebox.showwarning("Seleccion", "Marca al menos un producto.")
            return
        self._exportar_fotos_dialog(solo_ids=ids)

    def _exportar_fotos_categoria_dialog(self):
        self._dialogo_categoria_filtro(
            titulo="Exportar fotos por categoria",
            boton="Exportar ZIP de fotos",
            on_ok=lambda cat, filtro: self._exportar_fotos_dialog(categoria=cat, filtro=filtro),
        )

    def _dialogo_categoria_filtro(self, titulo, boton, on_ok):
        """Dialogo reutilizable: elige categoria y filtro opcional. Si no hay filtro -> toda la categoria."""
        win = ctk.CTkToplevel(self)
        win.title(titulo)
        win.geometry("440x280")
        try:
            win.transient(self.winfo_toplevel())
            win.lift()
            win.focus_force()
        except Exception:
            pass
        ctk.CTkLabel(win, text="Elige categoria y filtro (opcional):",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(16, 6))
        actual = self.cat_var.get() if getattr(self, "cat_var", None) and self.cat_var.get() != "Todas" else CATEGORIAS[0]
        cat_var = ctk.StringVar(value=actual if actual in CATEGORIAS else CATEGORIAS[0])
        ctk.CTkLabel(win, text="Categoria", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=90)
        ctk.CTkOptionMenu(
            win, values=CATEGORIAS, variable=cat_var, width=260,
            command=lambda _=None: refresh_filtros()
        ).pack(pady=(0, 8))
        ctk.CTkLabel(win, text="Filtro (opcional)", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=90)
        filtro_box = ctk.CTkComboBox(win, values=["Toda la categoria"], width=260)
        filtro_box.pack(pady=(0, 4))
        filtro_box.set("Toda la categoria")
        ctk.CTkLabel(
            win, text="Si no eliges filtro, se usa toda la categoria.",
            font=ctk.CTkFont(size=11), text_color="gray"
        ).pack()

        def filtros_de_categoria(categoria):
            conn = get_conn()
            rows = conn.execute(
                """SELECT DISTINCT p.filtro FROM productos p
                   LEFT JOIN categorias c ON p.categoria_id=c.id
                   WHERE p.activo=1 AND c.nombre=?
                   ORDER BY p.filtro COLLATE NOCASE""",
                (categoria,),
            ).fetchall()
            conn.close()
            out = []
            hay_sin = False
            for r in rows:
                f = (r["filtro"] or "").strip()
                if not f or f.lower() in ("sin filtro", "sin_filtro", "-", "no filtro"):
                    hay_sin = True
                    continue
                if f not in out:
                    out.append(f)
            if hay_sin:
                out.append("Sin filtro")
            return out

        def refresh_filtros(_=None):
            vals = ["Toda la categoria"] + filtros_de_categoria(cat_var.get())
            try:
                filtro_box.configure(values=vals)
            except Exception:
                pass
            filtro_box.set("Toda la categoria")

        refresh_filtros()

        def ok():
            cat = cat_var.get()
            fraw = (filtro_box.get() or "").strip()
            filtro = None if (not fraw or fraw == "Toda la categoria") else fraw
            win.destroy()
            on_ok(cat, filtro)

        ctk.CTkButton(
            win, text=boton, width=200, height=34,
            fg_color=VERDE, hover_color=VERDE_HOVER, command=ok
        ).pack(pady=14)

    def _importar_json_dialog(self):
        ruta_json = filedialog.askopenfilename(
            parent=self,
            title="Selecciona el archivo JSON de productos",
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
        )
        if not ruta_json:
            return
        usar_zip = messagebox.askyesno(
            "Fotos",
            "¿Tambien tienes un ZIP de fotos exportado?\n\n"
            "Si: elige el .zip a continuacion (se copiaran a uploads/).\n"
            "No: solo se importan datos; las imagenes deben existir ya en uploads/."
        )
        zip_path = None
        if usar_zip:
            zip_path = filedialog.askopenfilename(
                parent=self,
                title="ZIP de imagenes (opcional)",
                filetypes=[("ZIP", "*.zip"), ("Todos", "*.*")],
            )
            if not zip_path:
                zip_path = None
        actualizar = messagebox.askyesno(
            "Actualizar existentes",
            "Si un producto del JSON tiene el mismo ID que uno actual,\n"
            "¿actualizarlo?\n\n"
            "Si: actualiza.\nNo: crea siempre productos nuevos."
        )
        try:
            r = importar_productos_json(ruta_json, zip_imagenes=zip_path, actualizar_existentes=actualizar)
            clear_image_cache()
            messagebox.showinfo(
                "Importacion lista",
                f"Creados: {r['creados']}\n"
                f"Actualizados: {r['actualizados']}\n"
                f"Imagenes copiadas: {r['imagenes_copiadas']}"
            )
            self._cargar()
        except Exception as e:
            messagebox.showerror("Error al importar", str(e))


# Continuar con el resto de clases originales (ProductoDetalleFrame, ProductoForm, etc.)
# Para mantener archivo manejable, copiamos del original pero con ProductoForm corregido


class ProductoDetalleFrame(ctk.CTkFrame):
    def __init__(self, master, app, producto_id):
        super().__init__(master, corner_radius=12)
        self.app = app
        self.producto_id = producto_id
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        ctk.CTkButton(header, text="← Volver", width=90, command=self.app.volver_productos).pack(side="left")
        self.title_lbl = ctk.CTkLabel(header, text="Cargando...", font=ctk.CTkFont(size=18, weight="bold"))
        self.title_lbl.pack(side="left", padx=12)

        body = ctk.CTkFrame(self)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=8)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self.left = ctk.CTkScrollableFrame(body)
        self.left.grid(row=0, column=0, sticky="nsew", padx=(0,6), pady=6)
        self.right = ctk.CTkFrame(body)
        self.right.grid(row=0, column=1, sticky="nsew", padx=(6,0), pady=6)

        self._cargar()

    def _cargar(self):
        for w in list(self.left.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        for w in list(self.right.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        conn = get_conn()
        prod = conn.execute("SELECT p.*, c.nombre as categoria FROM productos p LEFT JOIN categorias c ON p.categoria_id=c.id WHERE p.id=?", (self.producto_id,)).fetchone()
        if not prod:
            conn.close()
            ctk.CTkLabel(self.left, text="Producto no encontrado").pack()
            return
        imgs = conn.execute("SELECT ruta FROM producto_imagenes WHERE producto_id=? ORDER BY orden", (self.producto_id,)).fetchall()
        stocks = conn.execute("SELECT talla, stock FROM producto_stock WHERE producto_id=? ORDER BY talla", (self.producto_id,)).fetchall()
        conn.close()

        self.title_lbl.configure(text=prod["nombre"] or "")

        # Galería
        ctk.CTkLabel(self.left, text="Fotografías", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=4)
        gal = ctk.CTkFrame(self.left, fg_color="transparent")
        gal.pack(fill="x", padx=8, pady=4)
        for r in imgs:
            p = UPLOADS_DIR / r["ruta"]
            if not p.exists():
                continue
            ctk_img = load_ctk_image(p, IMG_DETALLE, fast=False)
            if ctk_img:
                lbl = ctk.CTkLabel(gal, image=ctk_img, text="")
                lbl.image = ctk_img
                lbl.pack(side="left", padx=4)
        if not imgs:
            ctk.CTkLabel(gal, text="Sin fotos").pack()

        ctk.CTkLabel(self.left, text=f"Categoria: {prod['categoria'] or '-'} | Filtro: {prod['filtro'] or 'Sin filtro'} | Precio: ${float(prod['precio'] or 0):,.0f}", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=8, pady=8)
        if prod["descripcion"]:
            ctk.CTkLabel(self.left, text=prod["descripcion"], wraplength=500).pack(anchor="w", padx=8)

        # Stock y carrito
        ctk.CTkLabel(self.right, text="Tallas y Stock", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=12, pady=8)
        for s in stocks:
            row = ctk.CTkFrame(self.right, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=2)
            ctk.CTkLabel(row, text=f"{s['talla']}: {s['stock']} disponibles", width=160, anchor="w").pack(side="left")
            qty = ctk.CTkEntry(row, width=60)
            qty.insert(0, "1")
            qty.pack(side="left", padx=4)

            def add_to_cart(t=s["talla"], e=qty, stock=int(s["stock"] or 0)):
                try:
                    cant = int((e.get() or "1").strip() or 1)
                except Exception:
                    cant = 0
                if cant <= 0:
                    messagebox.showwarning("Cantidad", "Ingresa cantidad valida")
                    return
                if cant > stock:
                    messagebox.showwarning("Stock", f"Solo hay {stock} disponibles")
                    return
                item = {
                    "producto_id": self.producto_id,
                    "nombre": prod["nombre"],
                    "talla": t,
                    "cantidad": cant,
                    "precio": float(prod["precio"] or 0),
                    "filtro": prod["filtro"],
                    "categoria": prod["categoria"],
                    "version_jugador": bool(prod["version_jugador"]) if "version_jugador" in prod.keys() else False,
                }
                merged = False
                for it in self.app.carrito:
                    if (
                        it.get("producto_id") == self.producto_id
                        and it.get("talla") == t
                        and not it.get("sublimar")
                    ):
                        it["cantidad"] = int(it.get("cantidad") or 0) + cant
                        merged = True
                        break
                if not merged:
                    self.app.carrito.append(item)
                self.app.actualizar_badge_carrito()
                messagebox.showinfo("Carrito", f"Agregado {cant} x {prod['nombre']} talla {t}")

            ctk.CTkButton(row, text="Agregar", width=80, fg_color=VERDE, command=add_to_cart).pack(side="left", padx=4)

        ctk.CTkButton(
            self.right, text="Editar producto", fg_color=AZUL,
            command=lambda: ProductoForm(self, producto_id=self.producto_id, on_save=self._cargar)
        ).pack(pady=(16, 6), padx=12, fill="x")
        ctk.CTkButton(
            self.right, text="Ir al carrito", fg_color=VERDE, hover_color=VERDE_HOVER,
            command=self.app.ir_carrito
        ).pack(pady=(0, 12), padx=12, fill="x")

# ============================================================
#  PRODUCTO FORM - TOTALMENTE CORREGIDO (botones no desaparecen)
# ============================================================

class ProductoForm(ctk.CTkToplevel):
    def __init__(self, parent, producto_id=None, on_save=None):
        super().__init__(parent)
        self.producto_id = producto_id
        self.on_save = on_save
        self.imagenes_nuevas = []
        self.imagenes_existentes = []
        self._preview_jobs = []
        self._preview_ctk_imgs = []  # mantener referencias CTkImage
        self._preview_labels = []

        self.title("Editar producto" if producto_id else "Nuevo producto")
        self.geometry("1100x780")
        self.minsize(1000, 720)
        self.resizable(True, True)
        try:
            self.transient(parent.winfo_toplevel())
        except Exception:
            pass

        self.datos = None
        self.stocks_db = {}
        if producto_id:
            conn = get_conn()
            self.datos = conn.execute("SELECT * FROM productos WHERE id=?", (producto_id,)).fetchone()
            imgs = conn.execute(
                "SELECT ruta FROM producto_imagenes WHERE producto_id=? ORDER BY orden", (producto_id,)
            ).fetchall()
            self.imagenes_existentes = [r["ruta"] for r in imgs]
            for r in conn.execute("SELECT talla, stock FROM producto_stock WHERE producto_id=?", (producto_id,)):
                self.stocks_db[r["talla"]] = r["stock"]
            conn.close()

        self._construir()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            self.update_idletasks()
            w, h = 1100, 780
            x = max(40, (self.winfo_screenwidth() - w) // 2)
            y = max(20, (self.winfo_screenheight() - h) // 2)
            self.geometry(f"{w}x{h}+{x}+{y}")
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _on_close(self):
        # Limpiar jobs
        for j in self._preview_jobs:
            try:
                self.after_cancel(j)
            except:
                pass
        self._preview_jobs = []
        self.destroy()

    def _chg_stock(self, talla, delta):
        e = self.stock_entries[talla]
        try:
            v = int(float(e.get() or 0))
        except ValueError:
            v = 0
        v = max(0, v + delta)
        e.delete(0, "end")
        e.insert(0, str(v))
        self.talla_vars[talla].set(v > 0)

    def _on_stock_type(self, talla, _event=None):
        e = self.stock_entries[talla]
        try:
            v = int(float(e.get() or 0))
        except ValueError:
            v = 0
        self.talla_vars[talla].set(v > 0)

    def _chg_precio(self, delta):
        try:
            v = float(self.precio.get() or 0)
        except ValueError:
            v = 0
        v = max(0, v + delta)
        self.precio.delete(0, "end")
        self.precio.insert(0, str(int(v)))

    def _construir(self):
        # HEADER FIJO - siempre visible
        head = ctk.CTkFrame(self, fg_color=("gray90", "gray18"), corner_radius=0, height=48)
        head.pack(fill="x", side="top")
        head.pack_propagate(False)
        ctk.CTkLabel(
            head,
            text="Editar producto" if self.producto_id else "Nuevo producto",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left", padx=14, pady=10)
        ctk.CTkButton(head, text="Cancelar", width=90, height=30, fg_color="gray",
                      command=self._on_close).pack(side="right", padx=10, pady=8)
        ctk.CTkButton(head, text="Guardar", width=110, height=30, fg_color=VERDE, hover_color=VERDE_HOVER,
                      command=self._guardar).pack(side="right", padx=4, pady=8)

        # BODY con grid 2 columnas
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=8)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, corner_radius=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        pad = {"padx": 12, "pady": 3}

        ctk.CTkLabel(left, text="Nombre *", font=ctk.CTkFont(weight="bold")).pack(anchor="w", **pad)
        self.nombre = ctk.CTkEntry(left, height=32, placeholder_text="Ej: Playera Local 2024")
        self.nombre.pack(fill="x", **pad)
        if self.datos:
            self.nombre.insert(0, self.datos["nombre"] or "")

        ctk.CTkLabel(left, text="Categoria *", font=ctk.CTkFont(weight="bold")).pack(anchor="w", **pad)
        self.cat_var = ctk.StringVar(value=CATEGORIAS[0])
        if self.datos and self.datos["categoria_id"]:
            conn = get_conn()
            r = conn.execute("SELECT nombre FROM categorias WHERE id=?", (self.datos["categoria_id"],)).fetchone()
            conn.close()
            if r and r["nombre"]:
                self.cat_var.set(r["nombre"])
        self.cat_menu = ctk.CTkComboBox(
            left, values=list(CATEGORIAS), variable=self.cat_var, height=32,
            command=self._on_categoria_change
        )
        self.cat_menu.pack(fill="x", **pad)

        ctk.CTkLabel(left, text="Modelo (opcional)").pack(anchor="w", **pad)
        self.modelo = ctk.CTkEntry(left, height=30, placeholder_text="Opcional")
        self.modelo.pack(fill="x", **pad)
        if self.datos and self.datos["modelo"]:
            self.modelo.insert(0, self.datos["modelo"])

        ctk.CTkLabel(left, text="Filtro", font=ctk.CTkFont(weight="bold")).pack(anchor="w", **pad)
        filtros_vals = ["Sin filtro"] + lista_filtros_disponibles()
        self.filtro_var = ctk.StringVar(value="Sin filtro")
        if self.datos and self.datos["filtro"]:
            f0 = str(self.datos["filtro"]).strip()
            if f0 not in filtros_vals:
                filtros_vals.append(f0)
            self.filtro_var.set(f0)
        self.filtro = ctk.CTkComboBox(
            left, values=filtros_vals, variable=self.filtro_var, height=32,
            command=self._on_filtro_change
        )
        self.filtro.pack(fill="x", **pad)
        self.filtro_nuevo = ctk.CTkEntry(left, height=30, placeholder_text="O escribe filtro nuevo...")
        self.filtro_nuevo.pack(fill="x", **pad)
        self.filtro_nuevo.bind("<KeyRelease>", lambda e: self._aplicar_precio_tabla())

        ctk.CTkLabel(left, text="Precio aficion", font=ctk.CTkFont(weight="bold")).pack(anchor="w", **pad)
        pref = ctk.CTkFrame(left, fg_color="transparent")
        pref.pack(fill="x", **pad)
        ctk.CTkButton(pref, text="-10", width=44, height=30, command=lambda: self._chg_precio(-10)).pack(side="left", padx=2)
        self.precio = ctk.CTkEntry(pref, width=90, height=30)
        self.precio.pack(side="left", padx=2)
        if self.datos and self.datos["precio"] is not None:
            self.precio.insert(0, str(int(float(self.datos["precio"]))))
        else:
            self.precio.insert(0, str(int(precio_para_producto(
                None if self.filtro_var.get() == "Sin filtro" else self.filtro_var.get(),
                self.cat_var.get(),
            ))))
        ctk.CTkButton(pref, text="+10", width=44, height=30, command=lambda: self._chg_precio(10)).pack(side="left", padx=2)

        self.version_jugador_var = ctk.BooleanVar(value=False)
        if self.datos:
            try:
                self.version_jugador_var.set(bool(self.datos["version_jugador"]))
            except Exception:
                pass
        ctk.CTkCheckBox(
            left,
            text=f"Version jugador (+${PRECIO_EXTRA_JUGADOR})",
            variable=self.version_jugador_var,
        ).pack(anchor="w", padx=12, pady=6)

        ctk.CTkLabel(left, text="Descripcion (opcional)").pack(anchor="w", **pad)
        self.desc = ctk.CTkTextbox(left, height=70)
        self.desc.pack(fill="x", padx=12, pady=(0, 12))
        if self.datos and self.datos["descripcion"]:
            self.desc.insert("1.0", self.datos["descripcion"])

        # RIGHT SIDE - Fotografias y tallas - TODO CTk, sin tk mezclado
        right = ctk.CTkFrame(body, corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text="Fotografias", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))
        
        # BOTONES FIJOS - siempre visibles, en frame separado con altura fija
        img_row = ctk.CTkFrame(right, fg_color="transparent", height=40)
        img_row.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        img_row.pack_propagate(False)
        img_row.grid_columnconfigure(2, weight=1)
        
        btn_add = ctk.CTkButton(
            img_row, text="Agregar fotos", width=130, height=32,
            fg_color=VERDE, hover_color=VERDE_HOVER, command=self._add_fotos
        )
        btn_add.grid(row=0, column=0, padx=(0,6))
        
        btn_paste = ctk.CTkButton(
            img_row, text="Pegar rutas", width=110, height=32, fg_color="gray",
            command=self._pegar_rutas_manual
        )
        btn_paste.grid(row=0, column=1, padx=6)
        
        self.img_info = ctk.CTkLabel(img_row, text="", font=ctk.CTkFont(size=12), text_color="gray")
        self.img_info.grid(row=0, column=2, sticky="w", padx=8)
        self._upd_img_info()

        # Previews en CTkScrollableFrame horizontal - no tk.Frame
        self.imgs_prev = ctk.CTkFrame(right, fg_color=("#e9e9e9", "gray20"), height=130, corner_radius=8)
        self.imgs_prev.grid(row=2, column=0, sticky="ew", padx=12, pady=8)
        self.imgs_prev.grid_propagate(False)
        self.imgs_prev.grid_columnconfigure(0, weight=1)
        self.imgs_scroll = ctk.CTkScrollableFrame(self.imgs_prev, fg_color="transparent", orientation="horizontal", height=120)
        self.imgs_scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # Tallas
        self.tallas_title = ctk.CTkLabel(
            right, text="Tallas y stock  (S  M  L  XL  XXL  3XL  4XL)",
            font=ctk.CTkFont(weight="bold")
        )
        self.tallas_title.grid(row=3, column=0, sticky="w", padx=12, pady=(4, 2))

        # Tabla tallas con CTkScrollableFrame - no tk.Canvas
        self.tallas_wrap = ctk.CTkScrollableFrame(right, height=280)
        self.tallas_wrap.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 10))
        right.grid_rowconfigure(4, weight=1)

        self.talla_vars = {}
        self.stock_entries = {}
        cat0 = self.cat_var.get() if hasattr(self, "cat_var") else CATEGORIAS[0]
        if es_calzado(cat0):
            self.tallas_title.configure(text="Tallas de calzado  (6.0 a 12.5)")
        self._llenar_tabla_tallas(tallas_para_categoria(cat0))

        self._show_previews()

    def _llenar_tabla_tallas(self, lista):
        for w in self.tallas_wrap.winfo_children():
            w.destroy()
        self.talla_vars = {}
        self.stock_entries = {}
        
        # Headers con CTk
        header = ctk.CTkFrame(self.tallas_wrap, fg_color=("gray80","gray30"))
        header.pack(fill="x", pady=1)
        for h in ["", "Talla", "Stock", "+1", "+5", "+10", "-1", "-5", "-10"]:
            ctk.CTkLabel(header, text=h, width=50, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=1)

        for i, t in enumerate(lista):
            row = ctk.CTkFrame(self.tallas_wrap, fg_color=("white","gray20") if i%2==0 else ("gray95","gray25"))
            row.pack(fill="x", pady=1)
            var = ctk.BooleanVar(value=t in self.stocks_db)
            ctk.CTkCheckBox(row, text="", variable=var, width=30).pack(side="left", padx=2)
            ctk.CTkLabel(row, text=t, width=50, font=ctk.CTkFont(weight="bold")).pack(side="left", padx=2)
            ent = ctk.CTkEntry(row, width=60, justify="center")
            ent.insert(0, str(self.stocks_db[t]) if t in self.stocks_db else "0")
            ent.pack(side="left", padx=2)
            ent.bind("<KeyRelease>", lambda e, tt=t: self._on_stock_type(tt, e))
            self.talla_vars[t] = var
            self.stock_entries[t] = ent
            
            for step, color in [(1, VERDE),(5, VERDE),(10, VERDE),(-1, "gray"),(-5, "gray"),(-10, "gray")]:
                lab = f"+{step}" if step>0 else str(step)
                fg = VERDE if step>0 else "gray"
                b = ctk.CTkButton(row, text=lab, width=38, height=26, fg_color=fg, command=lambda tt=t, s=step: self._chg_stock(tt, s))
                b.pack(side="left", padx=1)

    def _on_filtro_change(self, choice=None):
        self._aplicar_precio_tabla()

    def _on_categoria_change(self, choice=None):
        self._aplicar_precio_tabla()
        cat = self.cat_var.get() if hasattr(self, "cat_var") else (choice or "")
        lista = tallas_para_categoria(cat)
        if es_calzado(cat):
            self.tallas_title.configure(text="Tallas de calzado  (6.0 a 12.5)")
        else:
            self.tallas_title.configure(text="Tallas y stock  (S  M  L  XL  XXL  3XL  4XL)")
        self._llenar_tabla_tallas(lista)

    def _aplicar_precio_tabla(self):
        nuevo = ""
        if hasattr(self, "filtro_nuevo"):
            nuevo = (self.filtro_nuevo.get() or "").strip()
        if nuevo:
            filtro = nuevo
        else:
            val = (self.filtro.get() or getattr(self, "filtro_var", ctk.StringVar(value="")).get() or "").strip()
            filtro = None if (not val or val == "Sin filtro") else val
        cat = None
        if hasattr(self, "cat_var"):
            cat = self.cat_var.get()
        precio = precio_para_producto(filtro, cat)
        self.precio.delete(0, "end")
        self.precio.insert(0, str(int(precio)))

    def _carpeta_inicio_fotos(self):
        try:
            CARPETA_FOTOS_PRENDAS.mkdir(parents=True, exist_ok=True)
            return str(CARPETA_FOTOS_PRENDAS)
        except Exception:
            p = Path.home() / "Pictures"
            return str(p) if p.is_dir() else str(Path.home())

    def _elegir_fotos_powershell(self, inicio):
        inicio_safe = str(inicio).replace("'", "''")
        return self._run_ps_dialog(inicio_safe)

    def _run_ps_dialog(self, inicio_safe):
        import subprocess
        import os
        lines = [
            "Add-Type -AssemblyName System.Windows.Forms",
            "$dlg = New-Object System.Windows.Forms.OpenFileDialog",
            "$dlg.Title = 'Seleccionar imagenes del producto'",
            "$dlg.Filter = 'Imagenes|*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp|Todos|*.*'",
            "$dlg.Multiselect = $true",
            f"$dlg.InitialDirectory = '{inicio_safe}'",
            "if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {",
            "  $dlg.FileNames | ForEach-Object { $_ }",
            "}",
        ]
        ps = chr(10).join(lines)
        try:
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=180,
                creationflags=flags,
            )
            out = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
            return out
        except Exception as e:
            print("powershell picker:", e)
            return None

    def _elegir_fotos_tk(self, inicio):
        try:
            paths = filedialog.askopenfilenames(
                parent=self,
                title="Seleccionar imagenes",
                initialdir=inicio,
                filetypes=[
                    ("Imagenes", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"),
                    ("Todos", "*.*"),
                ],
            )
            return list(paths) if paths else []
        except Exception as e:
            print("tk picker:", e)
            return None

    def _agregar_rutas(self, paths):
        exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
        n = 0
        for p in paths or []:
            try:
                path = Path(str(p).strip().strip('"'))
                if not path.is_file():
                    continue
                if path.suffix.lower() not in exts:
                    continue
                sp = str(path)
                if sp not in self.imagenes_nuevas:
                    self.imagenes_nuevas.append(sp)
                    n += 1
            except Exception:
                continue
        if n:
            self._upd_img_info()
            self._show_previews()
        return n

    def _pegar_rutas_manual(self):
        win = ctk.CTkToplevel(self)
        win.title("Pegar rutas de imagenes")
        win.geometry("520x280")
        try:
            win.transient(self)
        except Exception:
            pass
        ctk.CTkLabel(win, text="Pega las rutas completas (una por linea):").pack(padx=10, pady=8)
        box = ctk.CTkTextbox(win, height=140)
        box.pack(fill="both", expand=True, padx=10, pady=4)

        def ok():
            raw = box.get("1.0", "end").strip()
            parts = []
            for line in raw.replace(";", chr(10)).splitlines():
                line = line.strip().strip('"')
                if line:
                    parts.append(line)
            win.destroy()
            if parts:
                self._agregar_rutas(parts)

        fr = ctk.CTkFrame(win, fg_color="transparent")
        fr.pack(pady=8)
        ctk.CTkButton(fr, text="Agregar", width=110, fg_color=VERDE, hover_color=VERDE_HOVER,
                      command=ok).pack(side="left", padx=4)
        ctk.CTkButton(fr, text="Cancelar", width=90, fg_color="gray",
                      command=win.destroy).pack(side="left", padx=4)

    def _add_fotos(self):
        def _open():
            inicio = self._carpeta_inicio_fotos()
            paths = None
            try:
                paths = self._elegir_fotos_powershell(inicio)
            except Exception as e:
                print("ps:", e)
                paths = None
            if paths is None:
                paths = self._elegir_fotos_tk(inicio)
            if paths is None:
                try:
                    messagebox.showwarning(
                        "Explorador",
                        "No se pudo abrir el explorador.\nUsa el boton Pegar rutas.",
                        parent=self,
                    )
                except Exception:
                    pass
                self._pegar_rutas_manual()
                return
            if paths:
                self._agregar_rutas(list(paths))
            try:
                self.lift()
                self.focus_force()
            except Exception:
                pass

        self.after(40, _open)

    def _upd_img_info(self):
        n = len(self.imagenes_existentes) + len(self.imagenes_nuevas)
        try:
            self.img_info.configure(text=f"{n} foto(s)")
        except Exception:
            pass

    def _show_previews(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        # Limpiar
        for w in self.imgs_scroll.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self._preview_ctk_imgs = []
        self._preview_labels = []

        paths = []
        for ruta in self.imagenes_existentes:
            p = UPLOADS_DIR / ruta
            if p.is_file():
                paths.append(p)
        for p in self.imagenes_nuevas:
            pp = Path(p)
            if pp.is_file():
                paths.append(pp)

        if not paths:
            ctk.CTkLabel(
                self.imgs_scroll, text="Las fotos apareceran aqui",
                font=ctk.CTkFont(size=11), text_color="gray"
            ).pack(pady=20, padx=20)
            return

        shown = 0
        for p in paths:
            if shown >= 8:
                break
            try:
                ctk_img = load_ctk_image(p, (90,90), fast=True)
                if not ctk_img:
                    continue
            except Exception:
                continue
            self._preview_ctk_imgs.append(ctk_img)
            frame = ctk.CTkFrame(self.imgs_scroll, fg_color="transparent")
            frame.pack(side="left", padx=4, pady=4)
            lbl = ctk.CTkLabel(frame, image=ctk_img, text="")
            lbl.pack()
            # Botón borrar
            def make_del(idx=shown, path=p):
                # Si es nueva, quitar de lista
                if str(path) in self.imagenes_nuevas:
                    self.imagenes_nuevas.remove(str(path))
                # Si es existente, marcar para borrar? Por ahora solo ocultamos
                # Para simplificar, solo quitamos de nuevas; existentes se mantienen hasta guardar
                # Implementación: si es existente, quitar de existentes y borrar archivo al guardar no, solo no mostrar
                if isinstance(path, Path) and (UPLOADS_DIR / path.name).exists() or str(path) in self.imagenes_existentes:
                    # es existente con ruta relativa
                    rel = str(path) if isinstance(path, str) else path.name
                    # buscar en existentes
                    for er in list(self.imagenes_existentes):
                        if er == rel or (UPLOADS_DIR / er) == path:
                            self.imagenes_existentes.remove(er)
                            break
                        if Path(er).name == Path(path).name:
                            self.imagenes_existentes.remove(er)
                            break
                self._upd_img_info()
                self._show_previews()
            # Solo mostrar borrar si hay más de 1
            ctk.CTkButton(frame, text="x", width=20, height=20, fg_color=ROJO, command=make_del).pack(pady=2)
            shown += 1

        extra = len(paths) - shown
        if extra > 0:
            ctk.CTkLabel(
                self.imgs_scroll, text=f"+{extra} más",
                font=ctk.CTkFont(size=11, weight="bold")
            ).pack(side="left", padx=8)

    def _guardar(self):
        nombre = self.nombre.get().strip()
        if not nombre:
            messagebox.showerror("Error", "El nombre es obligatorio")
            return
        try:
            precio = float(self.precio.get() or 0)
        except ValueError:
            messagebox.showerror("Error", "Precio inválido")
            return

        stocks = {}
        for t, var in self.talla_vars.items():
            if not var.get():
                continue
            try:
                st = int(self.stock_entries[t].get() or 0)
            except ValueError:
                messagebox.showerror("Error", f"Stock inválido en talla {t}")
                return
            stocks[t] = st

        if not stocks:
            messagebox.showerror("Error", "Selecciona al menos una talla")
            return

        conn = get_conn()
        cat_row = conn.execute("SELECT id FROM categorias WHERE nombre=?", (self.cat_var.get(),)).fetchone()
        cat_id = cat_row["id"] if cat_row else None
        modelo = self.modelo.get().strip() or None
        desc = self.desc.get("1.0", "end-1c").strip() or None
        nuevo = ""
        if hasattr(self, "filtro_nuevo"):
            nuevo = (self.filtro_nuevo.get() or "").strip()
        if nuevo:
            filtro = nuevo
        else:
            filtro_raw = (self.filtro.get() or self.filtro_var.get() or "").strip()
            if not filtro_raw or filtro_raw == "Sin filtro":
                filtro = None
            else:
                filtro = filtro_raw
        vj = 1 if self.version_jugador_var.get() else 0

        if self.producto_id:
            pid = self.producto_id
            conn.execute(
                "UPDATE productos SET nombre=?, categoria_id=?, modelo=?, descripcion=?, precio=?, filtro=?, version_jugador=? WHERE id=?",
                (nombre, cat_id, modelo, desc, precio, filtro, vj, pid)
            )
            conn.execute("DELETE FROM producto_stock WHERE producto_id=?", (pid,))
            # Borrar imágenes que se quitaron del preview: comparar existentes actuales con los que quedan visibles
            # Para simplificar, borramos todas y reinsertamos las que quedan
            # Pero necesitamos saber cuáles quedan: self.imagenes_existentes ya fue actualizado al borrar previews
            conn.execute("DELETE FROM producto_imagenes WHERE producto_id=?", (pid,))
        else:
            cur = conn.execute(
                "INSERT INTO productos (nombre, categoria_id, modelo, descripcion, precio, filtro, version_jugador) VALUES (?,?,?,?,?,?,?)",
                (nombre, cat_id, modelo, desc, precio, filtro, vj)
            )
            pid = cur.lastrowid

        for t, st in stocks.items():
            conn.execute(
                "INSERT INTO producto_stock (producto_id, talla, stock) VALUES (?,?,?)",
                (pid, t, st)
            )

        # Reinsertar imágenes existentes que aún quedan
        orden = 0
        for ruta in self.imagenes_existentes:
            # ruta ya es nombre archivo en uploads
            conn.execute(
                "INSERT INTO producto_imagenes (producto_id, ruta, orden) VALUES (?,?,?)",
                (pid, ruta, orden)
            )
            orden += 1

        for path in self.imagenes_nuevas:
            ext = Path(path).suffix.lower()
            nuevo_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{orden}_{random.randint(100,999)}{ext}"
            try:
                shutil.copy2(path, UPLOADS_DIR / nuevo_name)
            except Exception as e:
                print(f"copy error {path}: {e}")
                continue
            conn.execute(
                "INSERT INTO producto_imagenes (producto_id, ruta, orden) VALUES (?,?,?)",
                (pid, nuevo_name, orden)
            )
            orden += 1

        conn.commit()
        conn.close()
        messagebox.showinfo("Éxito", "Producto guardado")
        if self.on_save:
            try:
                self.on_save()
            except Exception:
                pass
        self._on_close()

class SublimarWindow(ctk.CTkToplevel):
    """
    Una fila por CADA unidad del carrito.
    Si hay 4 playeras talla S → 4 fotos, cada una con TEXTO y NÚMERO propios.
    """

    def __init__(self, parent_app, on_done=None):
        super().__init__(parent_app)
        self.app = parent_app
        self.on_done = on_done
        self.title("Sublimar texto – personalizar cada prenda")
        self.geometry("560x600")
        self.minsize(500, 500)
        self.grab_set()

        ctk.CTkLabel(
            self, text="Personaliza cada prenda por separado",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(12, 2))
        ctk.CTkLabel(
            self, text="Si pediste 4 playeras, verás 4 fotos. Cada una puede llevar texto y número distintos.",
            font=ctk.CTkFont(size=11), text_color="gray"
        ).pack(pady=(0, 8))

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=12, pady=4)

        # Expandir carrito: cada unidad es una fila editable
        self.units = []  # lista de dicts editables (copia expandida)
        for item in self.app.carrito:
            qty = max(1, int(item.get("cantidad") or 1))
            for n in range(qty):
                self.units.append({
                    "producto_id": item["producto_id"],
                    "nombre": item["nombre"],
                    "talla": item["talla"],
                    "precio": item["precio"],
                    "filtro": item.get("filtro"),
                    "categoria": item.get("categoria"),
                    "sublimar": bool(item.get("sublimar")),
                    "texto": item.get("texto") if qty == 1 else None,
                    "numero": item.get("numero") if qty == 1 else None,
                    "unidad": n + 1,
                    "total_unidades": qty,
                })

        self.entries = []  # (unit_index, e_texto, e_num, check_var)

        if not self.units:
            ctk.CTkLabel(self.scroll, text="El carrito está vacío.").pack(pady=30)
        else:
            for idx, unit in enumerate(self.units):
                self._fila(idx, unit)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(pady=12)
        ctk.CTkButton(
            btns, text="Guardar sublimado", width=160, height=36,
            fg_color=VERDE, hover_color=VERDE_HOVER, command=self._guardar
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btns, text="Cerrar", width=90, height=36, fg_color="gray",
            command=self.destroy
        ).pack(side="left", padx=6)

    def _fila(self, idx, unit):
        card = ctk.CTkFrame(self.scroll, corner_radius=8)
        card.pack(fill="x", pady=5, padx=4)

        ruta = primera_imagen(unit["producto_id"])
        if ruta and (UPLOADS_DIR / ruta).exists():
            img = load_ctk_image(UPLOADS_DIR / ruta, IMG_MINI)
            if img:
                lbl = ctk.CTkLabel(card, image=img, text="")
                lbl.image = img
                lbl.pack(side="left", padx=8, pady=8)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=6, pady=6)

        titulo = f"{unit['nombre']}  ·  Talla {unit['talla']}"
        if unit["total_unidades"] > 1:
            titulo += f"  (pieza {unit['unidad']} de {unit['total_unidades']})"
        ctk.CTkLabel(info, text=titulo, font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")

        check_var = ctk.BooleanVar(value=unit["sublimar"])
        ctk.CTkCheckBox(info, text="Sublimar esta prenda", variable=check_var).pack(anchor="w", pady=(4, 2))

        ctk.CTkLabel(info, text="TEXTO", font=ctk.CTkFont(size=11)).pack(anchor="w")
        e_texto = ctk.CTkEntry(info, width=240, placeholder_text="Texto a sublimar")
        e_texto.pack(anchor="w", pady=1)
        if unit.get("texto"):
            e_texto.insert(0, unit["texto"])

        ctk.CTkLabel(info, text="NÚMERO", font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(4, 0))
        e_num = ctk.CTkEntry(info, width=100, placeholder_text="Ej: 10")
        e_num.pack(anchor="w", pady=1)
        if unit.get("numero"):
            e_num.insert(0, unit["numero"])

        self.entries.append((idx, e_texto, e_num, check_var))

    def _guardar(self):
        # Reconstruir carrito: 1 ítem por unidad (cantidad=1) para personalización individual
        nuevo = []
        for idx, e_texto, e_num, check_var in self.entries:
            u = self.units[idx]
            nuevo.append({
                "producto_id": u["producto_id"],
                "nombre": u["nombre"],
                "talla": u["talla"],
                "cantidad": 1,
                "precio": u["precio"],
                "filtro": u.get("filtro"),
                "categoria": u.get("categoria"),
                "sublimar": bool(check_var.get()),
                "texto": e_texto.get().strip() or None,
                "numero": e_num.get().strip() or None,
            })
        self.app.carrito = nuevo
        self.app.actualizar_badge_carrito()
        messagebox.showinfo("Sublimar", "Cada prenda quedó personalizada.\nRevisa el carrito y confirma la venta.")
        if self.on_done:
            self.on_done()
        self.destroy()


# ============================================================
#  CARRITO / VENTA
# ============================================================
class VentaFrame(ctk.CTkFrame):
    def __init__(self, master, app=None, *args, **kwargs):
        super().__init__(master, corner_radius=12)
        self.app = app if app is not None else getattr(master, 'app', master)

        # Layout principal
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Título
        ctk.CTkLabel(
            self, text="Carrito / Cobrar",
            font=ctk.CTkFont(size=22, weight="bold")
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 6))

        # ---------- IZQUIERDA: lista del carrito ----------
        left = ctk.CTkFrame(self, corner_radius=10)
        left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(left, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        ctk.CTkButton(
            top, text="Sublimar texto", width=150, height=34,
            fg_color=AZUL, hover_color="#1d4ed8",
            command=self._abrir_sublimar
        ).pack(side="left")
        ctk.CTkLabel(
            top, text="  (personaliza cada prenda)",
            font=ctk.CTkFont(size=11), text_color="gray"
        ).pack(side="left")

        self.lista = ctk.CTkScrollableFrame(left)
        self.lista.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))

        # ---------- DERECHA: cliente + total ----------
        right = ctk.CTkFrame(self, corner_radius=10)
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 12))

        ctk.CTkLabel(right, text="Datos del cliente",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=14, pady=(14, 6))

        ctk.CTkLabel(right, text="Nombre").pack(anchor="w", padx=14)
        self.cliente_nombre = ctk.CTkEntry(right, placeholder_text="Nombre completo")
        self.cliente_nombre.pack(fill="x", padx=14, pady=3)
        self.cliente_nombre.bind("<KeyRelease>", lambda e: self._recalc())

        ctk.CTkLabel(right, text="Telefono").pack(anchor="w", padx=14, pady=(8, 0))
        self.cliente_tel = ctk.CTkEntry(right, placeholder_text="Numero telefonico")
        self.cliente_tel.pack(fill="x", padx=14, pady=3)
        self.cliente_tel.bind("<KeyRelease>", lambda e: self._recalc())

        ctk.CTkLabel(right, text="Metodo de pago").pack(anchor="w", padx=14, pady=(8, 0))
        self.pago_var = ctk.StringVar(value="Efectivo")
        ctk.CTkOptionMenu(
            right, values=["Efectivo", "Tarjeta", "Transferencia", "Otro"],
            variable=self.pago_var
        ).pack(fill="x", padx=14, pady=3)

        ctk.CTkLabel(right, text="Notas").pack(anchor="w", padx=14, pady=(8, 0))
        self.notas = ctk.CTkTextbox(right, height=50)
        self.notas.pack(fill="x", padx=14, pady=3)

        ctk.CTkLabel(right, text="Descuento manual (pesos)").pack(anchor="w", padx=14, pady=(8, 0))
        self.descuento_pesos = ctk.CTkEntry(right, placeholder_text="0")
        self.descuento_pesos.insert(0, "0")
        self.descuento_pesos.pack(fill="x", padx=14, pady=3)
        self.descuento_pesos.bind("<KeyRelease>", lambda e: self._recalc())

        self.promo_lbl = ctk.CTkLabel(
            right, text="", font=ctk.CTkFont(size=11), text_color=AZUL, wraplength=240, justify="left"
        )
        self.promo_lbl.pack(padx=14, pady=(4, 0))
        self.subtotal_lbl = ctk.CTkLabel(
            right, text="Subtotal: $0.00", font=ctk.CTkFont(size=13)
        )
        self.subtotal_lbl.pack(padx=14, pady=(8, 0))
        self.desc_lbl = ctk.CTkLabel(
            right, text="", font=ctk.CTkFont(size=12), text_color="gray"
        )
        self.desc_lbl.pack(padx=14)
        self.total_lbl = ctk.CTkLabel(
            right, text="Total: $0.00",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=VERDE
        )
        self.total_lbl.pack(pady=(4, 10))

        ctk.CTkButton(
            right, text="Guardar cotizacion", height=38,
            fg_color=AZUL, hover_color="#1d4ed8",
            command=self._guardar_cotizacion
        ).pack(fill="x", padx=14, pady=(0, 6))

        ctk.CTkButton(
            right, text="Confirmar venta", height=42,
            fg_color=VERDE, hover_color=VERDE_HOVER,
            command=self._confirmar
        ).pack(fill="x", padx=14, pady=(0, 6))

        ctk.CTkButton(
            right, text="Vaciar carrito", height=30,
            fg_color=ROJO, hover_color=ROJO_HOVER,
            command=self._vaciar
        ).pack(fill="x", padx=14, pady=(0, 16))

        ctk.CTkLabel(
            right,
            text="Puedes seguir agregando prendas\ndesde Productos sin vaciar el carrito.",
            font=ctk.CTkFont(size=11), text_color="gray", justify="left"
        ).pack(anchor="w", padx=14, pady=(0, 10))

        # Prefill desde cotizacion (si aplica)
        pre = getattr(self.app, "_prefill_cliente", None)
        if pre:
            if pre.get("nombre"):
                self.cliente_nombre.insert(0, pre["nombre"])
            if pre.get("telefono"):
                self.cliente_tel.insert(0, pre["telefono"])
            if pre.get("notas"):
                self.notas.insert("1.0", pre["notas"])
            self.app._prefill_cliente = None

        # Pintar carrito al final
        self._render_carrito()

    def _on_show(self):
        self._render_carrito()

    def _abrir_sublimar(self):
        if not self.app.carrito:
            messagebox.showwarning("Vacio", "Agrega productos al carrito primero.")
            return
        SublimarWindow(self.app, on_done=self._render_carrito)

    def _render_carrito(self):
        try:
            for w in self.lista.winfo_children():
                w.destroy()
        except Exception:
            return

        if not self.app.carrito:
            ctk.CTkLabel(
                self.lista,
                text="El carrito esta vacio.\n\n1) Ve a Productos\n2) Abre una prenda\n3) Elige tallas y Agregar al carrito\n4) Vuelve aqui para cobrar",
                font=ctk.CTkFont(size=13),
                text_color="gray",
                justify="left"
            ).pack(pady=30, padx=20)
            self._recalc()
            return

        for i, item in enumerate(list(self.app.carrito)):
            row = ctk.CTkFrame(self.lista, corner_radius=8)
            row.pack(fill="x", pady=4, padx=4)

            # Mini imagen (si falla, se omite)
            try:
                ruta = primera_imagen(item.get("producto_id"))
                if ruta and (UPLOADS_DIR / ruta).exists():
                    img = load_ctk_image(UPLOADS_DIR / ruta, IMG_MINI)
                    if img:
                        lbl = ctk.CTkLabel(row, image=img, text="")
                        lbl.image = img
                        lbl.pack(side="left", padx=6, pady=6)
            except Exception:
                pass

            sub = ""
            if item.get("sublimar"):
                parts = []
                if item.get("texto"):
                    parts.append(f"Txt: {item['texto']}")
                if item.get("numero"):
                    parts.append(f"No: {item['numero']}")
                sub = "  |  Sublimar" + (f" ({', '.join(parts)})" if parts else "")

            qty = int(item.get("cantidad") or 1)
            nombre = item.get("nombre") or "Producto"
            talla = item.get("talla") or "-"
            precio = float(item.get("precio") or 0)

            txt = f"{nombre}  |  Talla {talla}  x{qty}{sub}"
            ctk.CTkLabel(row, text=txt, anchor="w", font=ctk.CTkFont(size=12)).pack(
                side="left", padx=6, pady=8, fill="x", expand=True
            )
            ctk.CTkLabel(
                row, text=f"${precio * qty:,.2f}",
                font=ctk.CTkFont(weight="bold", size=12)
            ).pack(side="left", padx=6)

            ctk.CTkButton(
                row, text="X", width=28, height=26,
                fg_color=ROJO, hover_color=ROJO_HOVER,
                command=lambda idx=i: self._quitar(idx)
            ).pack(side="right", padx=6)

        self._recalc()

    def _quitar(self, idx):
        if 0 <= idx < len(self.app.carrito):
            self.app.carrito.pop(idx)
        self.app.actualizar_badge_carrito()
        self._render_carrito()

    def _vaciar(self):
        self.app.carrito.clear()
        self.app.actualizar_badge_carrito()
        self._render_carrito()

    def _recalc(self):
        try:
            nombre = self.cliente_nombre.get().strip() if hasattr(self, "cliente_nombre") else ""
            tel = self.cliente_tel.get().strip() if hasattr(self, "cliente_tel") else ""
            try:
                desc_manual = float(self.descuento_pesos.get() or 0)
            except Exception:
                desc_manual = 0.0
            t = totales_venta(self.app.carrito, nombre, tel, desc_manual)
            self.subtotal_lbl.configure(text=f"Subtotal: ${t['subtotal']:,.2f}")
            partes = []
            if t["descuento_promo"] > 0:
                partes.append(f"Promos: -${t['descuento_promo']:,.2f}")
            if t["descuento_manual"] > 0:
                partes.append(f"Desc. manual: -${t['descuento_manual']:,.2f}")
            self.desc_lbl.configure(text="  |  ".join(partes) if partes else "")
            self.total_lbl.configure(text=f"Total: ${t['total']:,.2f}")
            if hasattr(self, "promo_lbl"):
                self.promo_lbl.configure(text=t.get("detalle") or "")
        except Exception as e:
            print("recalc:", e)
            try:
                sub = sum(
                    float(i.get("precio") or 0) * int(i.get("cantidad") or 1)
                    for i in (self.app.carrito or [])
                )
            except Exception:
                sub = 0.0
            try:
                self.subtotal_lbl.configure(text=f"Subtotal: ${sub:,.2f}")
            except Exception:
                pass
            try:
                self.total_lbl.configure(text=f"Total: ${sub:,.2f}")
            except Exception:
                pass

    def _guardar_cotizacion(self):
        """Guarda el carrito como cotizacion (no descuenta stock ni es venta)."""
        if not self.app.carrito:
            messagebox.showwarning("Vacio", "El carrito esta vacio")
            return
        try:
            nombre = self.cliente_nombre.get().strip() or "Cliente"
            tel = self.cliente_tel.get().strip() or None
            notas = self.notas.get("1.0", "end-1c").strip() or None
            try:
                desc_manual = float(self.descuento_pesos.get() or 0)
            except Exception:
                desc_manual = 0.0
            t = totales_venta(self.app.carrito, nombre, tel or "", desc_manual)
            total = t["total"]
            extras = []
            if t["descuento_promo"] > 0:
                extras.append(t.get("detalle") or f"Promo -${t['descuento_promo']:,.2f}")
            if t["descuento_manual"] > 0:
                extras.append(f"Desc. manual ${t['descuento_manual']:,.2f}")
            if extras:
                extra = " | ".join(extras)
                notas = (notas + " | " + extra) if notas else extra

            conn = get_conn()
            try:
                numero = generar_numero_cotizacion(conn)
            except Exception:
                numero = f"C{random.randint(0, 99999):05d}"
            cur = conn.execute(
                """INSERT INTO cotizaciones
                   (numero_cotizacion, cliente_nombre, cliente_telefono, total, notas)
                   VALUES (?,?,?,?,?)""",
                (numero, nombre, tel, total, notas)
            )
            cot_id = cur.lastrowid
            for item in self.app.carrito:
                cant = int(item.get("cantidad") or 1)
                conn.execute(
                    """INSERT INTO cotizacion_items
                       (cotizacion_id, producto_id, producto_nombre, talla, cantidad,
                        precio_unitario, sublimar_texto, texto_sublimado, numero_sublimado)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        cot_id,
                        item.get("producto_id"),
                        item.get("nombre"),
                        item.get("talla"),
                        cant,
                        float(item.get("precio") or 0),
                        1 if item.get("sublimar") else 0,
                        item.get("texto"),
                        item.get("numero"),
                    )
                )
            conn.commit()
            conn.close()
            self.app.carrito.clear()
            self.app.actualizar_badge_carrito()
            try:
                self.cliente_nombre.delete(0, "end")
                self.cliente_tel.delete(0, "end")
                self.notas.delete("1.0", "end")
                if hasattr(self, "descuento_pesos"):
                    self.descuento_pesos.delete(0, "end")
                    self.descuento_pesos.insert(0, "0")
            except Exception:
                pass
            self._render_carrito()
            try:
                self.app._frames["cotizaciones"]._cargar()
            except Exception:
                pass
            messagebox.showinfo(
                "Cotizacion guardada",
                f"Cotizacion #{numero} guardada.\n\n"
                f"Cliente: {nombre}\n"
                f"Total: ${total:,.2f}\n\n"
                "El carrito se vacio. La ves en Cotizaciones."
            )
            self.app._mostrar_frame("cotizaciones")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar la cotizacion:\n{e}")

    def _confirmar(self):
        if not self.app.carrito:
            messagebox.showwarning("Vacio", "El carrito esta vacio")
            return

        try:
            needed = {}
            for item in self.app.carrito:
                key = (item["producto_id"], item["talla"])
                needed[key] = needed.get(key, 0) + int(item.get("cantidad") or 1)

            conn = get_conn()
            for (pid, talla), qty in needed.items():
                row = conn.execute(
                    "SELECT stock FROM producto_stock WHERE producto_id=? AND talla=?",
                    (pid, talla)
                ).fetchone()
                nombre = next(
                    (i["nombre"] for i in self.app.carrito
                     if i["producto_id"] == pid and i["talla"] == talla),
                    "Producto"
                )
                if not row:
                    conn.close()
                    messagebox.showerror(
                        "Stock",
                        f"No hay stock registrado para:\n{nombre} talla {talla}\n\n"
                        "Edita el producto y asigna stock a esa talla."
                    )
                    return
                if row["stock"] < qty:
                    conn.close()
                    messagebox.showerror(
                        "Stock insuficiente",
                        f"{nombre} talla {talla}\nDisponible: {row['stock']}  ·  Pedido: {qty}"
                    )
                    return

            nombre = self.cliente_nombre.get().strip() or "Mostrador"
            tel = self.cliente_tel.get().strip() or None
            notas = self.notas.get("1.0", "end-1c").strip() or None
            try:
                desc_manual = float(self.descuento_pesos.get() or 0)
            except Exception:
                desc_manual = 0.0
            t = totales_venta(self.app.carrito, nombre, tel or "", desc_manual)
            total = t["total"]
            extras = []
            if t["descuento_promo"] > 0:
                extras.append(f"Promo: {t['detalle']} | Subtotal ${t['subtotal']:,.2f} Desc promo ${t['descuento_promo']:,.2f}")
            if t["descuento_manual"] > 0:
                extras.append(f"Desc. manual ${t['descuento_manual']:,.2f}")
            if extras:
                extra = " | ".join(extras)
                notas = (notas + " | " + extra) if notas else extra

            try:
                numero = generar_numero_pedido(conn)
            except Exception:
                numero = f"{random.randint(0, 99999):05d}"
            cur = conn.execute(
                """INSERT INTO pedidos
                   (numero_pedido, cliente_nombre, cliente_telefono, total, estado, metodo_pago, notas)
                   VALUES (?,?,?,?,?,?,?)""",
                (numero, nombre, tel, total, "completado", self.pago_var.get(), notas)
            )
            pedido_id = cur.lastrowid

            for item in self.app.carrito:
                cant = int(item.get("cantidad") or 1)
                conn.execute(
                    """INSERT INTO pedido_items
                       (pedido_id, producto_id, producto_nombre, talla, cantidad,
                        precio_unitario, sublimar_texto, texto_sublimado, numero_sublimado)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        pedido_id,
                        item["producto_id"],
                        item["nombre"],
                        item["talla"],
                        cant,
                        float(item.get("precio") or 0),
                        1 if item.get("sublimar") else 0,
                        item.get("texto"),
                        item.get("numero"),
                    )
                )
                conn.execute(
                    "UPDATE producto_stock SET stock = stock - ? WHERE producto_id=? AND talla=?",
                    (cant, item["producto_id"], item["talla"])
                )

            conn.commit()
            conn.close()

            messagebox.showinfo(
                "Pedido confirmado con exito",
                f"Pedido #{numero} guardado correctamente.\n\n"
                f"Cliente: {nombre}\n"
                f"Total: ${total:,.2f}\n\n"
                "Entrega estimada: 15 dias.\n"
                "Ya aparece en la seccion Pedidos."
            )

            self.app.carrito.clear()
            self.app.actualizar_badge_carrito()
            self.cliente_nombre.delete(0, "end")
            self.cliente_tel.delete(0, "end")
            if hasattr(self, "descuento_pesos"):
                self.descuento_pesos.delete(0, "end")
                self.descuento_pesos.insert(0, "0")
            self.notas.delete("1.0", "end")
            self._render_carrito()
            try:
                self.app._frames["pedidos"]._cargar()
            except Exception:
                pass
            self.app._mostrar_frame("pedidos")

        except Exception as e:
            try:
                conn.close()
            except Exception:
                pass
            messagebox.showerror(
                "Error al confirmar",
                f"No se pudo guardar el pedido.\n\nDetalle:\n{e}"
            )



class CotizacionesFrame(ctk.CTkFrame):
    def __init__(self, master, app=None, *args, **kwargs):
        super().__init__(master, corner_radius=12)
        self.app = app if app is not None else getattr(master, 'app', master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Cotizaciones",
                     font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            head, text="Presupuestos sin confirmar venta",
            font=ctk.CTkFont(size=11), text_color="gray"
        ).grid(row=0, column=1, sticky="e")

        filtros = ctk.CTkFrame(self, fg_color="transparent")
        filtros.grid(row=1, column=0, sticky="ew", padx=14, pady=4)
        self.busqueda = ctk.CTkEntry(
            filtros, width=280,
            placeholder_text="Buscar por No. cotizacion o cliente..."
        )
        self.busqueda.pack(side="left", padx=(0, 8))
        self.busqueda.bind("<Return>", lambda e: self._cargar())
        ctk.CTkButton(filtros, text="Buscar", width=80, fg_color=VERDE, hover_color=VERDE_HOVER,
                      command=self._cargar).pack(side="left", padx=3)
        ctk.CTkButton(filtros, text="Limpiar", width=70, fg_color="gray",
                      command=self._limpiar).pack(side="left", padx=3)

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self._cargar()

    def _on_show(self):
        self._cargar()

    def _limpiar(self):
        self.busqueda.delete(0, "end")
        self._cargar()

    def _cargar(self):
        for w in self.scroll.winfo_children():
            w.destroy()
        q = self.busqueda.get().strip()
        conn = get_conn()
        rows = []
        try:
            if q:
                rows = conn.execute(
                    """SELECT * FROM cotizaciones
                       WHERE IFNULL(numero_cotizacion,'') LIKE ?
                          OR IFNULL(cliente_nombre,'') LIKE ?
                          OR CAST(id AS TEXT) LIKE ?
                       ORDER BY id DESC LIMIT 150""",
                    (f"%{q}%", f"%{q}%", f"%{q}%")
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM cotizaciones ORDER BY id DESC LIMIT 150"
                ).fetchall()
        except Exception as e:
            print("cotizaciones cargar:", e)
            try:
                rows = conn.execute("SELECT * FROM cotizaciones ORDER BY id DESC LIMIT 150").fetchall()
            except Exception:
                rows = []
        conn.close()

        if not rows:
            ctk.CTkLabel(
                self.scroll,
                text="No hay cotizaciones.\nGuarda una desde Carrito / Venta con Guardar cotizacion.",
                font=ctk.CTkFont(size=13), text_color="gray"
            ).pack(pady=40)
            return

        header = ctk.CTkFrame(self.scroll, fg_color=("gray80", "gray25"), corner_radius=6)
        header.pack(fill="x", pady=(0, 2))
        for i, t in enumerate(["No.", "Fecha", "Cliente", "Total", ""]):
            ctk.CTkLabel(header, text=t, font=ctk.CTkFont(weight="bold", size=12),
                         width=100 if i else 80).grid(row=0, column=i, padx=3, pady=4)

        for c in rows:
            num = c["numero_cotizacion"] or str(c["id"])
            row = ctk.CTkFrame(self.scroll, corner_radius=4)
            row.pack(fill="x", pady=1)
            vals = [
                f"#{num}",
                (c["creado_en"] or "")[:16],
                (c["cliente_nombre"] or "—")[:20],
                f"${float(c['total']):,.2f}",
            ]
            for i, v in enumerate(vals):
                lbl = ctk.CTkLabel(row, text=v, width=100 if i else 80, font=ctk.CTkFont(size=12))
                lbl.grid(row=0, column=i, padx=3, pady=4)
                lbl.bind("<Button-1>", lambda e, cid=c["id"]: self.app.abrir_cotizacion_detalle(cid))
            row.bind("<Button-1>", lambda e, cid=c["id"]: self.app.abrir_cotizacion_detalle(cid))
            ctk.CTkButton(
                row, text="Ver", width=50, height=26,
                command=lambda cid=c["id"]: self.app.abrir_cotizacion_detalle(cid)
            ).grid(row=0, column=4, padx=6)


class CotizacionDetalleFrame(ctk.CTkFrame):
    def __init__(self, master, app, cotizacion_id):
        super().__init__(master, corner_radius=12)
        self.app = app
        self.cotizacion_id = cotizacion_id
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        conn = get_conn()
        self.cot = conn.execute(
            "SELECT * FROM cotizaciones WHERE id=?", (cotizacion_id,)
        ).fetchone()
        self.items = conn.execute(
            "SELECT * FROM cotizacion_items WHERE cotizacion_id=? ORDER BY id",
            (cotizacion_id,)
        ).fetchall()
        conn.close()

        if not self.cot:
            ctk.CTkLabel(self, text="Cotizacion no encontrada").pack(pady=30)
            return

        num = self.cot["numero_cotizacion"] or str(self.cot["id"])

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(
            head, text="Volver", width=80, fg_color="gray",
            command=lambda: self.app._mostrar_frame("cotizaciones")
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            head, text=f"Cotizacion #{num}",
            font=ctk.CTkFont(size=20, weight="bold")
        ).grid(row=0, column=1, sticky="w", padx=12)

        info = ctk.CTkFrame(self, corner_radius=8)
        info.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        ctk.CTkLabel(
            info, text=f"Cliente: {self.cot['cliente_nombre'] or '—'}",
            font=ctk.CTkFont(size=13)
        ).pack(side="left", padx=10, pady=8)
        ctk.CTkLabel(
            info, text=f"Tel: {self.cot['cliente_telefono'] or '—'}",
            font=ctk.CTkFont(size=13)
        ).pack(side="left", padx=10)
        ctk.CTkLabel(
            info, text=f"Fecha: {str(self.cot['creado_en'] or '')[:19]}",
            font=ctk.CTkFont(size=12), text_color="gray"
        ).pack(side="left", padx=10)
        ctk.CTkLabel(
            info, text=f"Total: ${float(self.cot['total']):,.2f}",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=VERDE
        ).pack(side="right", padx=12)

        scroll = ctk.CTkScrollableFrame(self)
        scroll.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 4))

        ctk.CTkLabel(
            scroll, text="Prendas cotizadas",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(4, 8))

        for it in self.items:
            card = ctk.CTkFrame(scroll, corner_radius=8)
            card.pack(fill="x", pady=4)
            if it["producto_id"]:
                ruta = primera_imagen(it["producto_id"])
                if ruta and (UPLOADS_DIR / ruta).exists():
                    img = load_ctk_image(UPLOADS_DIR / ruta, IMG_MINI, fast=True)
                    if img:
                        lbl = ctk.CTkLabel(card, image=img, text="")
                        lbl.image = img
                        lbl.pack(side="left", padx=8, pady=8)
            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(side="left", fill="x", expand=True, padx=6, pady=8)
            ctk.CTkLabel(
                body, text=it["producto_nombre"] or "—",
                font=ctk.CTkFont(size=13, weight="bold")
            ).pack(anchor="w")
            ctk.CTkLabel(
                body,
                text=f"Talla: {it['talla'] or '—'}  ·  Cant: {it['cantidad']}  ·  "
                     f"${float(it['precio_unitario']):,.2f}",
                font=ctk.CTkFont(size=12)
            ).pack(anchor="w")
            if it["sublimar_texto"]:
                sub = "Sublimar: Si"
                if it["texto_sublimado"]:
                    sub += f"  Texto: {it['texto_sublimado']}"
                if it["numero_sublimado"]:
                    sub += f"  No: {it['numero_sublimado']}"
                ctk.CTkLabel(body, text=sub, font=ctk.CTkFont(size=12), text_color=AZUL).pack(anchor="w")

        # Acciones
        acciones = ctk.CTkFrame(self, fg_color="transparent")
        acciones.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 14))
        ctk.CTkButton(
            acciones, text="Ir al Carrito a Cobrar", height=42, width=220,
            fg_color=VERDE, hover_color=VERDE_HOVER,
            command=self._ir_a_cobrar
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            acciones, text="Eliminar Cotizacion", height=42, width=180,
            fg_color=ROJO, hover_color=ROJO_HOVER,
            command=self._eliminar
        ).pack(side="left", padx=4)
        ctk.CTkLabel(
            acciones,
            text="  Al cobrar se cargan las prendas al carrito (puedes agregar mas desde Productos).",
            font=ctk.CTkFont(size=11), text_color="gray"
        ).pack(side="left", padx=6)

    def _ir_a_cobrar(self):
        # Cargar items al carrito (reemplaza carrito actual con la cotizacion)
        nuevo = []
        for it in self.items:
            nuevo.append({
                "producto_id": it["producto_id"],
                "nombre": it["producto_nombre"],
                "talla": it["talla"],
                "cantidad": int(it["cantidad"] or 1),
                "precio": float(it["precio_unitario"] or 0),
                "sublimar": bool(it["sublimar_texto"]),
                "texto": it["texto_sublimado"],
                "numero": it["numero_sublimado"],
            })
        self.app.carrito = nuevo
        self.app.actualizar_badge_carrito()
        # Prefill cliente via temporary attrs
        self.app._prefill_cliente = {
            "nombre": self.cot["cliente_nombre"] or "",
            "telefono": self.cot["cliente_telefono"] or "",
            "notas": self.cot["notas"] or "",
        }
        messagebox.showinfo(
            "Carrito listo",
            "La cotizacion se cargo en el carrito.\\n"
            "Puedes agregar mas prendas desde Productos y luego Confirmar venta."
        )
        self.app._mostrar_frame("venta")

    def _eliminar(self):
        num = self.cot["numero_cotizacion"] or str(self.cot["id"])
        if not messagebox.askyesno("Eliminar", f"Eliminar cotizacion #{num}?"):
            return
        conn = get_conn()
        conn.execute("DELETE FROM cotizacion_items WHERE cotizacion_id=?", (self.cotizacion_id,))
        conn.execute("DELETE FROM cotizaciones WHERE id=?", (self.cotizacion_id,))
        conn.commit()
        conn.close()
        messagebox.showinfo("Eliminada", f"Cotizacion #{num} eliminada.")
        self.app._mostrar_frame("cotizaciones")



class PedidosFrame(ctk.CTkFrame):
    def __init__(self, master, app=None, *args, **kwargs):
        super().__init__(master, corner_radius=12)
        self.app = app if app is not None else getattr(master, 'app', master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Historial de Pedidos",
                     font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="w")

        filtros = ctk.CTkFrame(self, fg_color="transparent")
        filtros.grid(row=1, column=0, sticky="ew", padx=14, pady=4)
        self.busqueda = ctk.CTkEntry(
            filtros, width=280,
            placeholder_text="Buscar por No. pedido o nombre de cliente..."
        )
        self.busqueda.pack(side="left", padx=(0, 8))
        self.busqueda.bind("<Return>", lambda e: self._cargar())
        ctk.CTkButton(filtros, text="Buscar", width=80, fg_color=VERDE, hover_color=VERDE_HOVER,
                      command=self._cargar).pack(side="left", padx=3)
        ctk.CTkButton(filtros, text="Limpiar", width=70, fg_color="gray",
                      command=self._limpiar).pack(side="left", padx=3)

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self._cargar()

    def _on_show(self):
        self._cargar()

    def _limpiar(self):
        self.busqueda.delete(0, "end")
        self._cargar()

    def _cargar(self):
        for w in self.scroll.winfo_children():
            w.destroy()

        q = self.busqueda.get().strip()
        conn = get_conn()
        if q:
            pedidos = conn.execute(
                """SELECT * FROM pedidos
                   WHERE numero_pedido LIKE ?
                      OR CAST(id AS TEXT) LIKE ?
                      OR IFNULL(cliente_nombre,'') LIKE ?
                   ORDER BY id DESC LIMIT 150""",
                (f"%{q}%", f"%{q}%", f"%{q}%")
            ).fetchall()
        else:
            pedidos = conn.execute(
                "SELECT * FROM pedidos ORDER BY id DESC LIMIT 150"
            ).fetchall()
        conn.close()

        if not pedidos:
            ctk.CTkLabel(self.scroll, text="No hay pedidos.").pack(pady=30)
            return

        header = ctk.CTkFrame(self.scroll, fg_color=("gray80", "gray25"), corner_radius=6)
        header.pack(fill="x", pady=(0, 2))
        for i, t in enumerate(["No.", "Fecha", "Cliente", "Entrega", "Total", ""]):
            ctk.CTkLabel(header, text=t, font=ctk.CTkFont(weight="bold", size=12),
                         width=90 if i else 70).grid(row=0, column=i, padx=3, pady=4)

        for p in pedidos:
            num = p["numero_pedido"] or str(p["id"])
            ent = fecha_entrega(p["creado_en"])
            row = ctk.CTkFrame(self.scroll, corner_radius=4)
            row.pack(fill="x", pady=1)
            vals = [
                f"#{num}",
                (p["creado_en"] or "")[:16],
                (p["cliente_nombre"] or "—")[:18],
                ent.strftime("%d/%m/%Y"),
                f"${float(p['total']):,.2f}",
            ]
            for i, v in enumerate(vals):
                lbl = ctk.CTkLabel(row, text=v, width=90 if i else 70, font=ctk.CTkFont(size=12))
                lbl.grid(row=0, column=i, padx=3, pady=4)
                lbl.bind("<Button-1>", lambda e, pid=p["id"]: self.app.abrir_pedido_detalle(pid))
            row.bind("<Button-1>", lambda e, pid=p["id"]: self.app.abrir_pedido_detalle(pid))

            btns = ctk.CTkFrame(row, fg_color="transparent")
            btns.grid(row=0, column=5, padx=4)
            ctk.CTkButton(
                btns, text="Ver", width=44, height=26,
                command=lambda pid=p["id"]: self.app.abrir_pedido_detalle(pid)
            ).pack(side="left", padx=2)
            ctk.CTkButton(
                btns, text="Borrar", width=54, height=26,
                fg_color=ROJO, hover_color=ROJO_HOVER,
                command=lambda pid=p["id"], n=num: self._borrar(pid, n)
            ).pack(side="left", padx=2)

    def _borrar(self, pedido_id, num):
        if not messagebox.askyesno("Borrar pedido", f"Eliminar el pedido #{num}?\nEsta accion no se puede deshacer."):
            return
        conn = get_conn()
        conn.execute("DELETE FROM pedido_items WHERE pedido_id=?", (pedido_id,))
        conn.execute("DELETE FROM pedidos WHERE id=?", (pedido_id,))
        conn.commit()
        conn.close()
        messagebox.showinfo("Eliminado", f"Pedido #{num} eliminado.")
        self._cargar()


class PedidoDetalleFrame(ctk.CTkFrame):
    def __init__(self, master, app, pedido_id):
        super().__init__(master, corner_radius=12)
        self.app = app
        self.pedido_id = pedido_id
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        conn = get_conn()
        self.pedido = conn.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        self.items = conn.execute(
            "SELECT * FROM pedido_items WHERE pedido_id=? ORDER BY id", (pedido_id,)
        ).fetchall()
        conn.close()

        if not self.pedido:
            ctk.CTkLabel(self, text="Pedido no encontrado").pack(pady=30)
            return

        num = self.pedido["numero_pedido"] or str(self.pedido["id"])
        ent = fecha_entrega(self.pedido["creado_en"])

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(head, text="Volver", width=80, fg_color="gray",
                      command=lambda: self.app._mostrar_frame("pedidos")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(head, text=f"Pedido #{num}",
                     font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=1, sticky="w", padx=12)

        ctk.CTkButton(
            head, text="Exportar ticket", width=130, fg_color=AZUL, hover_color="#1d4ed8",
            command=self._exportar
        ).grid(row=0, column=2, padx=4)
        ctk.CTkButton(
            head, text="Borrar", width=80, fg_color=ROJO, hover_color=ROJO_HOVER,
            command=self._borrar
        ).grid(row=0, column=3, padx=4)

        info = ctk.CTkFrame(self, corner_radius=8)
        info.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        ctk.CTkLabel(info, text=f"Cliente: {self.pedido['cliente_nombre'] or '—'}",
                     font=ctk.CTkFont(size=13)).pack(side="left", padx=10, pady=8)
        ctk.CTkLabel(info, text=f"Tel: {self.pedido['cliente_telefono'] or '—'}",
                     font=ctk.CTkFont(size=13)).pack(side="left", padx=10)
        ctk.CTkLabel(info, text=f"Pago: {self.pedido['metodo_pago'] or '—'}",
                     font=ctk.CTkFont(size=13)).pack(side="left", padx=10)
        ctk.CTkLabel(info, text=f"Pedido: {str(self.pedido['creado_en'] or '')[:19]}",
                     font=ctk.CTkFont(size=12), text_color="gray").pack(side="left", padx=10)
        ctk.CTkLabel(info, text=f"Entrega: {ent.strftime('%d/%m/%Y')} (15 dias)",
                     font=ctk.CTkFont(size=12, weight="bold"), text_color=VERDE).pack(side="left", padx=10)
        ctk.CTkLabel(info, text=f"Total: ${float(self.pedido['total']):,.2f}",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color=VERDE).pack(side="right", padx=12)

        scroll = ctk.CTkScrollableFrame(self)
        scroll.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 10))

        ctk.CTkLabel(scroll, text="Prendas del pedido",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(4, 8))

        if not self.items:
            ctk.CTkLabel(scroll, text="Sin items.").pack(pady=20)
            return

        for it in self.items:
            card = ctk.CTkFrame(scroll, corner_radius=8)
            card.pack(fill="x", pady=4)

            if it["producto_id"]:
                ruta = primera_imagen(it["producto_id"])
                if ruta and (UPLOADS_DIR / ruta).exists():
                    img = load_ctk_image(UPLOADS_DIR / ruta, IMG_MINI)
                    if img:
                        lbl = ctk.CTkLabel(card, image=img, text="")
                        lbl.image = img
                        lbl.pack(side="left", padx=8, pady=8)

            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(side="left", fill="x", expand=True, padx=6, pady=8)

            ctk.CTkLabel(body, text=it["producto_nombre"] or "—",
                         font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(
                body,
                text=f"Talla: {it['talla'] or '—'}   ·   Cantidad: {it['cantidad']}",
                font=ctk.CTkFont(size=12)
            ).pack(anchor="w")
            ctk.CTkLabel(
                body,
                text=f"Precio unit.: ${float(it['precio_unitario']):,.2f}   ·   "
                     f"Subtotal: ${float(it['precio_unitario']) * it['cantidad']:,.2f}",
                font=ctk.CTkFont(size=12), text_color="gray"
            ).pack(anchor="w")

            if it["sublimar_texto"]:
                sub_txt = "Si"
                if it["texto_sublimado"]:
                    sub_txt += f"  ·  Texto: {it['texto_sublimado']}"
                if it["numero_sublimado"]:
                    sub_txt += f"  ·  Numero: {it['numero_sublimado']}"
                ctk.CTkLabel(body, text=f"Sublimar: {sub_txt}",
                             font=ctk.CTkFont(size=12), text_color=AZUL).pack(anchor="w", pady=(2, 0))
            else:
                ctk.CTkLabel(body, text="Sublimar: No",
                             font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w")

    def _exportar(self):
        from tkinter import filedialog
        num = self.pedido["numero_pedido"] or str(self.pedido["id"])
        default = f"ticket_pedido_{num}.png"
        ruta = filedialog.asksaveasfilename(
            title="Guardar ticket",
            defaultextension=".png",
            initialfile=default,
            filetypes=[("PNG", "*.png")]
        )
        if not ruta:
            return
        try:
            exportar_ticket_imagen(self.pedido, self.items, ruta)
            messagebox.showinfo("Ticket exportado", f"Ticket guardado en:\\n{ruta}")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo exportar el ticket:\\n{e}")

    def _borrar(self):
        num = self.pedido["numero_pedido"] or str(self.pedido["id"])
        if not messagebox.askyesno("Borrar pedido", f"Eliminar el pedido #{num}?"):
            return
        conn = get_conn()
        conn.execute("DELETE FROM pedido_items WHERE pedido_id=?", (self.pedido_id,))
        conn.execute("DELETE FROM pedidos WHERE id=?", (self.pedido_id,))
        conn.commit()
        conn.close()
        messagebox.showinfo("Eliminado", f"Pedido #{num} eliminado.")
        self.app._mostrar_frame("pedidos")



# ============================================================
if __name__ == "__main__":
    import sys
    import traceback

    log = BASE_DIR / "error_inicio.log"
    try:
        ctk.set_appearance_mode("Light")
        ctk.set_default_color_theme("green")
        init_db()
        try:
            n = actualizar_precios_todos_productos()
            print(f"Precios actualizados en {n} producto(s).")
        except Exception as e:
            print("Aviso precios:", e)
        app = App()
        app.mainloop()
    except Exception:
        err = traceback.format_exc()
        try:
            log.write_text(err, encoding="utf-8")
        except Exception:
            pass
        print(err)
        try:
            from tkinter import messagebox as mb
            mb.showerror("Error al iniciar", err[:900])
        except Exception:
            pass
        input("Enter para salir...")
        sys.exit(1)
