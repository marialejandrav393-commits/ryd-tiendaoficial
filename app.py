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

# Detección de la base de datos permanente en Neon / PostgreSQL
DATABASE_URL = os.environ.get('DATABASE_URL')

def es_postgres():
    return bool(DATABASE_URL and DATABASE_URL.startswith(('postgres://', 'postgresql://')))

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
        # 1. Productos en Postgres
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

        # 2. Proveedores en Postgres
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

        # 3. Ventas en Postgres
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

        # 4. Detalle Ventas en Postgres
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

        # 5. Usuarios en Postgres
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'cajero'
            )
        """)
    else:
        # 1. Productos en SQLite
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

        # 2. Proveedores en SQLite
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

        # 3. Ventas en SQLite
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

        # 4. Detalle Ventas en SQLite
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

        # 5. Usuarios en SQLite
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'cajero'
            )
        """)

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
    desc = descripcion.lower()
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
    productos = ejecutar_consulta('SELECT * FROM productos WHERE stock > 0 ORDER BY nombre ASC', fetchall=True) or []
    categorias_rows = ejecutar_consulta('SELECT DISTINCT categoria FROM productos WHERE stock > 0', fetchall=True) or []
    categorias = [row['categoria'] for row in categorias_rows if row['categoria']]
    return render_template('pos.html', productos=productos, categorias=categorias)

@app.route('/procesar_venta', methods=['POST'])
def procesar_venta():
    if not session.get('logged_in'):
        return jsonify({'exito': False, 'mensaje': 'Sesión vencida. Vuelve a iniciar sesión.'}), 401

    data = request.get_json() or {}
    items = data.get('items', [])
    metodo_pago = data.get('metodo_pago', 'Efectivo $')
    referencia = data.get('referencia', 'N/A')
    tasa_cambio = float(data.get('tasa_cambio', 50.0))
    desglose_pago = data.get('desglose_pago', '')
    cliente_nombre = data.get('cliente_nombre', 'Cliente Mostrador')
    cliente_telefono = data.get('cliente_telefono', '')
    usuario = session.get('username', 'Cajero')

    if not items:
        return jsonify({'exito': False, 'mensaje': 'El carrito está vacío'}), 400

    try:
        total_venta = sum(float(item['precio']) * int(item['cantidad']) for item in items)
        total_unidades = sum(int(item.get('cantidad', 1)) for item in items)
        monto_bs = round(total_venta * tasa_cambio, 2)
        fecha_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        resumen_nombres = ", ".join([it['nombre'] for it in items[:3]])
        if len(items) > 3:
            resumen_nombres += f" (+{len(items)-3} más)"

        venta_id = ejecutar_consulta("""
            INSERT INTO ventas (fecha, total, metodo_pago, referencia, usuario, producto_nombre, tasa_cambio, monto_bs, desglose_pago, cliente_nombre, cliente_telefono, cantidad, precio)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fecha_hora, round(total_venta, 2), metodo_pago, referencia, usuario,
            resumen_nombres, tasa_cambio, monto_bs, desglose_pago, cliente_nombre,
            cliente_telefono, total_unidades, round(total_venta, 2)
        ), commit=True, lastrowid=True)

        for it in items:
            cod = it.get('codigo', '')
            nom = it.get('nombre', '')
            cant = int(it.get('cantidad', 1))
            p_unit = float(it.get('precio', 0.0))
            p_unit_bs = round(float(it.get('precio_bs', p_unit * tasa_cambio)), 2)
            subt = round(p_unit * cant, 2)

            ejecutar_consulta('UPDATE productos SET stock = stock - ? WHERE codigo = ? OR nombre = ?', (cant, cod, nom), commit=True)
            prod_row = ejecutar_consulta('SELECT id FROM productos WHERE codigo = ? OR nombre = ? LIMIT 1', (cod, nom), fetchone=True)
            prod_id = prod_row['id'] if prod_row else None

            ejecutar_consulta("""
                INSERT INTO detalle_ventas (venta_id, producto_id, nombre_producto, cantidad, precio_unitario, precio_unitario_bs, subtotal)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (venta_id, prod_id, nom, cant, p_unit, p_unit_bs, subt), commit=True)

        return jsonify({'exito': True, 'venta_id': venta_id})
    except Exception as e:
        return jsonify({'exito': False, 'mensaje': f"Error al procesar: {str(e)}"}), 500

@app.route('/ticket/<int:venta_id>')
def ticket(venta_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    venta = ejecutar_consulta('SELECT * FROM ventas WHERE id = ?', (venta_id,), fetchone=True)
    if not venta:
        return "Comprobante no encontrado", 404

    detalles = ejecutar_consulta('SELECT * FROM detalle_ventas WHERE venta_id = ?', (venta_id,), fetchall=True) or []
    return render_template('ticket.html', venta=venta, detalles=detalles)

@app.route('/admin')
@role_required('admin')
def admin():
    productos_raw = ejecutar_consulta('SELECT * FROM productos ORDER BY id DESC', fetchall=True) or []

    total_costo_inversion = 0.0
    total_valor_venta = 0.0
    ganancia_estimada = 0.0

    for p in productos_raw:
        costo_u = float(p['costo'] or 0.0)
        precio_u = float(p['precio'] or 0.0)
        stock_u = int(p['stock'] or 0)

        total_costo_inversion += (costo_u * stock_u)
        total_valor_venta += (precio_u * stock_u)
        ganancia_estimada += ((precio_u - costo_u) * stock_u)

    total_ventas_usd = 0.0
    try:
        ventas_total_row = ejecutar_consulta('SELECT SUM(total) as total_ventas FROM ventas', fetchone=True)
        if ventas_total_row and ventas_total_row['total_ventas']:
            total_ventas_usd = float(ventas_total_row['total_ventas'])
    except Exception:
        total_ventas_usd = 0.0

    return render_template(
        'admin.html',
        productos=productos_raw,
        ganancia_estimada=round(ganancia_estimada, 2),
        total_costo_inversion=round(total_costo_inversion, 2),
        total_valor_venta=round(total_valor_venta, 2),
        total_ventas_usd=round(total_ventas_usd, 2)
    )
