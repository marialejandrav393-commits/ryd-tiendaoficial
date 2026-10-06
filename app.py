import os
import io
import re
import sqlite3
import unicodedata
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.utils import secure_filename
from pypdf import PdfReader
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = 'clave_secreta_super_segura_ryd_2026'

UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Detección de base de datos permanente en Neon / PostgreSQL
DATABASE_URL = os.environ.get('DATABASE_URL')

def es_postgres():
    return bool(DATABASE_URL and (DATABASE_URL.startswith('postgres://') or DATABASE_URL.startswith('postgresql://')))

def obtener_conexion():
    if es_postgres():
        url = DATABASE_URL
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        return psycopg2.connect(url, sslmode='require')
    else:
        conn = sqlite3.connect('inventario.db')
        conn.row_factory = sqlite3.Row
        return conn

def ejecutar_consulta(query, params=(), fetchone=False, fetchall=False, commit=False, lastrowid=False):
    conn = obtener_conexion()
    if es_postgres():
        query_pg = query.replace('?', '%s')
        if lastrowid and 'INSERT' in query_pg.upper() and 'RETURNING' not in query_pg.upper():
            query_pg += ' RETURNING id'

        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(query_pg, params)
        res = None
        if lastrowid:
            fila = cursor.fetchone()
            res = fila['id'] if fila else None
        elif fetchone:
            res = cursor.fetchone()
        elif fetchall:
            res = cursor.fetchall()

        if commit:
            conn.commit()
        cursor.close()
        conn.close()
        return res
    else:
        cursor = conn.cursor()
        cursor.execute(query, params)
        res = None
        if lastrowid:
            res = cursor.lastrowid
        elif fetchone:
            res = cursor.fetchone()
        elif fetchall:
            res = cursor.fetchall()

        if commit:
            conn.commit()
        cursor.close()
        conn.close()
        return res

def inicializar_db():
    conn = obtener_conexion()
    cursor = conn.cursor()

    if es_postgres():
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS productos (
                id SERIAL PRIMARY KEY,
                codigo TEXT,
                nombre TEXT NOT NULL,
                costo NUMERIC DEFAULT 0.0,
                precio_bs NUMERIC DEFAULT 0.0,
                precio NUMERIC NOT NULL,
                stock INTEGER NOT NULL DEFAULT 0,
                categoria TEXT DEFAULT 'General',
                descuento NUMERIC DEFAULT 0.0,
                imagen TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS proveedores (
                id SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL,
                telefono TEXT DEFAULT '',
                contacto TEXT DEFAULT '',
                direccion TEXT DEFAULT '',
                rif TEXT DEFAULT '',
                ultima_compra TEXT DEFAULT '',
                total_compras NUMERIC DEFAULT 0.0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ventas (
                id SERIAL PRIMARY KEY,
                fecha TEXT NOT NULL,
                total NUMERIC DEFAULT 0.0,
                metodo_pago TEXT DEFAULT 'Efectivo $',
                referencia TEXT DEFAULT '',
                usuario TEXT DEFAULT 'Cajero',
                producto_nombre TEXT DEFAULT '',
                tasa_cambio NUMERIC DEFAULT 50.0,
                monto_bs NUMERIC DEFAULT 0.0,
                desglose_pago TEXT DEFAULT '',
                cliente_nombre TEXT DEFAULT 'Cliente',
                cliente_telefono TEXT DEFAULT '',
                cantidad INTEGER DEFAULT 1,
                precio NUMERIC DEFAULT 0.0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS detalle_ventas (
                id SERIAL PRIMARY KEY,
                venta_id INTEGER REFERENCES ventas(id) ON DELETE CASCADE,
                producto_id INTEGER,
                nombre_producto TEXT,
                cantidad INTEGER,
                precio_unitario NUMERIC,
                precio_unitario_bs NUMERIC DEFAULT 0.0,
                subtotal NUMERIC
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'cajero'
            )
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT,
                nombre TEXT NOT NULL,
                costo REAL DEFAULT 0.0,
                precio_bs REAL DEFAULT 0.0,
                precio REAL NOT NULL,
                stock INTEGER NOT NULL DEFAULT 0,
                categoria TEXT DEFAULT 'General',
                descuento REAL DEFAULT 0.0,
                imagen TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS proveedores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT UNIQUE NOT NULL,
                telefono TEXT DEFAULT '',
                contacto TEXT DEFAULT '',
                direccion TEXT DEFAULT '',
                rif TEXT DEFAULT '',
                ultima_compra TEXT DEFAULT '',
                total_compras REAL DEFAULT 0.0
            )
        """)
        cols_existentes_prov = [c[1] for c in cursor.execute("PRAGMA table_info(proveedores)").fetchall()]
        for col_nom, col_tipo in [
            ('telefono', 'TEXT DEFAULT ""'),
            ('contacto', 'TEXT DEFAULT ""'),
            ('direccion', 'TEXT DEFAULT ""'),
            ('rif', 'TEXT DEFAULT ""'),
            ('ultima_compra', 'TEXT DEFAULT ""'),
            ('total_compras', 'REAL DEFAULT 0.0')
        ]:
            if col_nom not in cols_existentes_prov:
                try:
                    cursor.execute(f'ALTER TABLE proveedores ADD COLUMN {col_nom} {col_tipo}')
                except Exception:
                    pass
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ventas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                total REAL DEFAULT 0.0,
                metodo_pago TEXT DEFAULT 'Efectivo $',
                referencia TEXT DEFAULT '',
                usuario TEXT DEFAULT 'Cajero',
                producto_nombre TEXT DEFAULT '',
                tasa_cambio REAL DEFAULT 50.0,
                monto_bs REAL DEFAULT 0.0,
                desglose_pago TEXT DEFAULT '',
                cliente_nombre TEXT DEFAULT 'Cliente',
                cliente_telefono TEXT DEFAULT '',
                cantidad INTEGER DEFAULT 1,
                precio REAL DEFAULT 0.0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS detalle_ventas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                venta_id INTEGER,
                producto_id INTEGER,
                nombre_producto TEXT,
                cantidad INTEGER,
                precio_unitario REAL,
                precio_unitario_bs REAL DEFAULT 0.0,
                subtotal REAL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'cajero'
            )
        """)

    if es_postgres():
        cursor.execute("SELECT id FROM usuarios WHERE username = 'admin'")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO usuarios (username, password, rol) VALUES ('admin', 'admin123', 'admin')")
        cursor.execute("SELECT id FROM usuarios WHERE username = 'cajero'")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO usuarios (username, password, rol) VALUES ('cajero', 'cajero2026', 'cajero')")
    else:
        cursor.execute("SELECT id FROM usuarios WHERE username = 'admin'")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO usuarios (username, password, rol) VALUES ('admin', 'admin123', 'admin')")
        cursor.execute("SELECT id FROM usuarios WHERE username = 'cajero'")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO usuarios (username, password, rol) VALUES ('cajero', 'cajero2026', 'cajero')")

    conn.commit()
    cursor.close()
    conn.close()

inicializar_db()

def clasificar_categoria_ryd(descripcion):
    desc = (descripcion or '').lower()
    if any(k in desc for k in ['olla', 'sm-200', 'ventilador', 'lampara', 'extractor', 'pulidor', 'drill', 'esterilizador', 'maquina', 'aparatologia']):
        return "Aparatología"
    if any(k in desc for k in ['pestañ', 'ceja', 'henna', 'lash', 'brow', 'volumen', 'pigmento']):
        return "Cejas y Pestañas"
    if any(k in desc for k in ['esmalte', 'lipstick', 'brush on', 'gel', 'finish', 'rubber', 'cuticula', 'protein', 'polygel', 'acrygel', 'serum', 'nail', 'primer', 'ultrabond', 'blossom', 'base coat', 'builder', 'tijera', 'cortauna', 'lima', 'punta', 'jelly', 'pincel', 'bledo', 'dappen', 'guillotina', 'empujador', 'uñas', 'uña']):
        return "Uñas"
    if any(k in desc for k in ['gorro', 'guante', 'desechable', 'tapa boca', 'mascarilla', 'toalla', 'separador', 'palitos', 'hisopo']):
        return "Desechables"
    if any(k in desc for k in ['shampoo', 'alisado', 'laminado', 'termoprotector', 'blower', 'tratamiento', 'peine', 'difusor', 'ondas', 'cepillo', 'keratina', 'tinte', 'plancha', 'cabello', 'acondicionador', 'cuidado capilar']):
        return "Cuidado Capilar"
    if any(k in desc for k in ['intimo', 'jabon', 'arandano', 'manzanilla', 'cera depilatoria', 'roll on', 'banda depilacion', 'corporal']):
        return "Íntimo"
    if any(k in desc for k in ['alcohol', 'acetona', 'algodon', 'cleanser', 'sanitizante', 'exfoliante', 'espuma', 'sponge', 'mantequilla', 'gota', 'atomizador', 'organizador', 'envase', 'boligrafo', 'espejo', 'cesta']):
        return "Insumos"
    return "General"

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('logged_in'):
                flash('Por favor inicia sesión para acceder.')
                return redirect(url_for('login'))
            if session.get('user_role') not in roles:
                flash('Acceso denegado: No tienes permisos para acceder a esta sección.')
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --- AUTENTICACIÓN ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = (request.form.get('username') or '').strip().lower()
        password = (request.form.get('password') or '').strip()

        if usuario == 'admin' and password in ['admin123', 'admin2026', 'admin']:
            session['logged_in'] = True
            session['user_id'] = 1
            session['username'] = 'admin'
            session['user_role'] = 'admin'
            return redirect(url_for('admin'))

        if usuario == 'cajero' and password in ['cajero2026', 'cajero123', 'cajero']:
            session['logged_in'] = True
            session['user_id'] = 2
            session['username'] = 'cajero'
            session['user_role'] = 'cajero'
            return redirect(url_for('pos_cajero'))

        user_info = ejecutar_consulta(
            'SELECT * FROM usuarios WHERE LOWER(username) = ? AND password = ?',
            (usuario, password),
            fetchone=True
        )

        if user_info:
            session['logged_in'] = True
            session['user_id'] = user_info['id']
            session['username'] = user_info['username']
            rol_obtenido = user_info['rol'] if 'rol' in user_info else 'cajero'
            session['user_role'] = rol_obtenido

            if rol_obtenido == 'admin':
                return redirect(url_for('admin'))
            return redirect(url_for('pos_cajero'))
        else:
            flash('Usuario o contraseña incorrectos.')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Has cerrado sesión.')
    return redirect(url_for('login'))

# --- PUNTO DE VENTA (POS) ---

@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if session.get('user_role') == 'admin':
        return redirect(url_for('admin'))
    return redirect(url_for('pos_cajero'))

@app.route('/cajero/pos')
@role_required('admin', 'cajero')
def pos_cajero():
    try:
        productos_raw = ejecutar_consulta('SELECT * FROM productos WHERE stock > 0 ORDER BY nombre ASC', fetchall=True) or []
        
        # Convertimos los precios de Neon a números estándar para evitar choques en el HTML
        productos = []
        for p in productos_raw:
            p_dict = dict(p)
            p_dict['precio'] = float(p_dict['precio'] or 0.0)
            p_dict['precio_bs'] = float(p_dict['precio_bs'] or 0.0)
            p_dict['costo'] = float(p_dict['costo'] or 0.0)
            p_dict['descuento'] = float(p_dict['descuento'] or 0.0)
            productos.append(p_dict)

        categorias_rows = ejecutar_consulta('SELECT DISTINCT categoria FROM productos WHERE stock > 0', fetchall=True) or []
        categorias = [row['categoria'] for row in categorias_rows if row['categoria']]
        
        return render_template('pos.html', productos=productos, categorias=categorias)
    except Exception as e:
        import traceback
        return f"<h1>Error del sistema:</h1><pre>{traceback.format_exc()}</pre>"

@app.route('/procesar_venta', methods=['POST'])
def procesar_venta():
    if not session.get('logged_in'):
        return jsonify({'exito': False, 'mensaje': 'Sesión vencida. Vuelve a iniciar sesión.'}), 401

    data = request.get_json() or {}
    items = data.get('items', [])
    metodo_
