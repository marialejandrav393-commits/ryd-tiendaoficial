import os
import io
import re
import json
import sqlite3
import threading
import urllib.request
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.utils import secure_filename
from pypdf import PdfReader
import pandas as pd
import traceback
from decimal import Decimal, ROUND_HALF_UP

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception:
    psycopg2 = None
    RealDictCursor = None

app = Flask(__name__)
app.secret_key = 'clave_secreta_super_segura_ryd_2026'

# --- CONFIGURACIÓN DEL MENSAJERO INVISIBLE (PUENTE LAPTOP -> WEB) ---
URL_NUBE_RENDER = "https://ryd-tiendaoficial.onrender.com"
CLAVE_SYNC_SECRETA = "ryd_puente_secreto_2026"

UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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
        cursor.execute("CREATE TABLE IF NOT EXISTS productos (id SERIAL PRIMARY KEY, codigo TEXT, nombre TEXT NOT NULL, costo NUMERIC DEFAULT 0.0, precio_bs NUMERIC DEFAULT 0.0, precio NUMERIC NOT NULL, stock INTEGER NOT NULL DEFAULT 0, categoria TEXT DEFAULT 'General', descuento NUMERIC DEFAULT 0.0, imagen TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS proveedores (id SERIAL PRIMARY KEY, nombre TEXT UNIQUE NOT NULL, telefono TEXT DEFAULT '', contacto TEXT DEFAULT '', direccion TEXT DEFAULT '', rif TEXT DEFAULT '', ultima_compra TEXT DEFAULT '', total_compras NUMERIC DEFAULT 0.0)")
        cursor.execute("CREATE TABLE IF NOT EXISTS ventas (id SERIAL PRIMARY KEY, fecha TEXT NOT NULL, total NUMERIC DEFAULT 0.0, metodo_pago TEXT DEFAULT 'Efectivo $', referencia TEXT DEFAULT '', usuario TEXT DEFAULT 'Cajero', producto_nombre TEXT DEFAULT '', tasa_cambio NUMERIC DEFAULT 50.0, monto_bs NUMERIC DEFAULT 0.0, desglose_pago TEXT DEFAULT '', cliente_nombre TEXT DEFAULT 'Cliente', cliente_telefono TEXT DEFAULT '', cantidad INTEGER DEFAULT 1, precio NUMERIC DEFAULT 0.0)")
        cursor.execute("CREATE TABLE IF NOT EXISTS detalle_ventas (id SERIAL PRIMARY KEY, venta_id INTEGER REFERENCES ventas(id) ON DELETE CASCADE, producto_id INTEGER, nombre_producto TEXT, cantidad INTEGER, precio_unitario NUMERIC, precio_unitario_bs NUMERIC DEFAULT 0.0, subtotal NUMERIC)")
        cursor.execute("CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, rol TEXT NOT NULL DEFAULT 'cajero')")
        try:
            cursor.execute("ALTER TABLE productos ADD COLUMN IF NOT EXISTS precio_bs NUMERIC DEFAULT 0.0")
        except Exception:
            pass
    else:
        cursor.execute("CREATE TABLE IF NOT EXISTS productos (id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT, nombre TEXT NOT NULL, costo REAL DEFAULT 0.0, precio_bs REAL DEFAULT 0.0, precio REAL NOT NULL, stock INTEGER NOT NULL DEFAULT 0, categoria TEXT DEFAULT 'General', descuento REAL DEFAULT 0.0, imagen TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS proveedores (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT UNIQUE NOT NULL, telefono TEXT DEFAULT '', contacto TEXT DEFAULT '', direccion TEXT DEFAULT '', rif TEXT DEFAULT '', ultima_compra TEXT DEFAULT '', total_compras REAL DEFAULT 0.0)")
        cols_existentes_prov = [c[1] for c in cursor.execute("PRAGMA table_info(proveedores)").fetchall()]
        for col_nom, col_tipo in [('telefono', 'TEXT DEFAULT ""'), ('contacto', 'TEXT DEFAULT ""'), ('direccion', 'TEXT DEFAULT ""'), ('rif', 'TEXT DEFAULT ""'), ('ultima_compra', 'TEXT DEFAULT ""'), ('total_compras', 'REAL DEFAULT 0.0')]:
            if col_nom not in cols_existentes_prov:
                try:
                    cursor.execute(f'ALTER TABLE proveedores ADD COLUMN {col_nom} {col_tipo}')
                except Exception:
                    pass
        cursor.execute("CREATE TABLE IF NOT EXISTS ventas (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, total REAL DEFAULT 0.0, metodo_pago TEXT DEFAULT 'Efectivo $', referencia TEXT DEFAULT '', usuario TEXT DEFAULT 'Cajero', producto_nombre TEXT DEFAULT '', tasa_cambio REAL DEFAULT 50.0, monto_bs REAL DEFAULT 0.0, desglose_pago TEXT DEFAULT '', cliente_nombre TEXT DEFAULT 'Cliente', cliente_telefono TEXT DEFAULT '', cantidad INTEGER DEFAULT 1, precio REAL DEFAULT 0.0)")
        cursor.execute("CREATE TABLE IF NOT EXISTS detalle_ventas (id INTEGER PRIMARY KEY AUTOINCREMENT, venta_id INTEGER, producto_id INTEGER, nombre_producto TEXT, cantidad INTEGER, precio_unitario REAL, precio_unitario_bs REAL DEFAULT 0.0, subtotal REAL)")
        cursor.execute("CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, rol TEXT NOT NULL DEFAULT 'cajero')")

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

def redondear(valor):
    try:
        return float(Decimal(str(float(valor))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0

# --- FUNCIÓN DEL MENSAJERO EN SEGUNDO PLANO ---
def enviar_mensaje_a_la_nube(accion, datos):
    if es_postgres():
        return
    def tarea_silenciosa():
        try:
            payload = json.dumps({
                'clave': CLAVE_SYNC_SECRETA,
                'accion': accion,
                'datos': datos
            }).encode('utf-8')
            req = urllib.request.Request(
                f"{URL_NUBE_RENDER}/api/sincronizar_nube",
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            urllib.request.urlopen(req, timeout=20)
        except Exception:
            pass
    hilo = threading.Thread(target=tarea_silenciosa, daemon=True)
    hilo.start()

def sincronizar_catalogo_completo_a_nube():
    if es_postgres():
        return
    try:
        prods = ejecutar_consulta('SELECT codigo, nombre, costo, precio_bs, precio, stock, categoria, descuento FROM productos', fetchall=True) or []
        lista = [dict(p) for p in prods]
        enviar_mensaje_a_la_nube('reemplazar_catalogo', lista)
    except Exception:
        pass

@app.route('/api/sincronizar_nube', methods=['POST'])
def api_sincronizar_nube():
    data = request.get_json() or {}
    if data.get('clave') != CLAVE_SYNC_SECRETA:
        return jsonify({'exito': False, 'mensaje': 'No autorizado'}), 403

    accion = data.get('accion')
    datos = data.get('datos', [])

    conn = None
    try:
        conn = obtener_conexion()
        cursor = conn.cursor(cursor_factory=RealDictCursor) if es_postgres() else conn.cursor()
        def fmt(q):
            return q.replace('?', '%s') if es_postgres() else q

        if accion == 'descontar_stock':
            for it in datos:
                nom = str(it.get('nombre', '') or '').strip()
                cant = int(it.get('cantidad', 1))
                cursor.execute(fmt('UPDATE productos SET stock = GREATEST(0, stock - ?) WHERE UPPER(TRIM(nombre)) = UPPER(?)') if es_postgres() else fmt('UPDATE productos SET stock = MAX(0, stock - ?) WHERE UPPER(TRIM(nombre)) = UPPER(?)'), (cant, nom))

        elif accion == 'reemplazar_catalogo':
            for it in datos:
                cod = str(it.get('codigo', '') or '').strip().upper()
                nom = str(it.get('nombre', '') or '').strip()
                costo = redondear(it.get('costo', 0))
                precio_bs = redondear(it.get('precio_bs', 0) or (costo * 1.5525))
                precio = redondear(it.get('precio', 0) or (costo * 1.35))
                stock = int(it.get('stock', 0) or 0)
                cat = str(it.get('categoria', 'General') or 'General').strip()
                desc = float(it.get('descuento', 0) or 0)

                if not nom:
                    continue

                cursor.execute(fmt('SELECT id FROM productos WHERE UPPER(TRIM(nombre)) = UPPER(?)'), (nom,))
                existente = cursor.fetchone()
                if existente:
                    prod_id = existente['id'] if es_postgres() else existente[0]
                    cursor.execute(fmt("UPDATE productos SET codigo = ?, costo = ?, precio_bs = ?, precio = ?, stock = ?, categoria = ?, descuento = ? WHERE id = ?"), (cod, costo, precio_bs, precio, stock, cat, desc, prod_id))
                else:
                    cursor.execute(fmt("INSERT INTO productos (codigo, nombre, costo, precio_bs, precio, stock, categoria, descuento) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"), (cod, nom, costo, precio_bs, precio, stock, cat, desc))

        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'exito': True})
    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({'exito': False, 'mensaje': str(e)}), 500

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

        user_info = ejecutar_consulta('SELECT * FROM usuarios WHERE LOWER(username) = ? AND password = ?', (usuario, password), fetchone=True)

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

# --- CATÁLOGO WEB PÚBLICO Y CARRITO ---

@app.route('/')
def index():
    productos_raw = ejecutar_consulta('SELECT * FROM productos WHERE stock > 0 ORDER BY nombre ASC', fetchall=True) or []
    productos = []
    for p in productos_raw:
        p_dict = dict(p)
        costo_val = float(p_dict.get('costo') or 0.0)
        precio_val = float(p_dict.get('precio') or 0.0)
        precio_bs_val = float(p_dict.get('precio_bs') or (costo_val * 1.5525 if costo_val > 0 else precio_val))
        p_dict['precio'] = redondear(precio_val)
        p_dict['precio_bs'] = redondear(precio_bs_val)
        p_dict['costo'] = redondear(costo_val)
        p_dict['descuento'] = float(p_dict.get('descuento') or 0.0)
        productos.append(p_dict)

    carrito_session = session.get('carrito', [])
    carrito_vista = []
    total_carrito_usd = 0.0
    total_carrito_bs = 0.0

    for item in carrito_session:
        cant = int(item.get('cantidad', 1))
        p_usd = float(item.get('precio', 0.0))
        p_bs = float(item.get('precio_bs', p_usd))
        sub_usd = redondear(p_usd * cant)
        sub_bs = redondear(p_bs * cant)
        total_carrito_usd += sub_usd
        total_carrito_bs += sub_bs
        carrito_vista.append({
            'id': item.get('id'),
            'nombre': item.get('nombre'),
            'cantidad': cant,
            'precio': p_usd,
            'precio_bs': p_bs,
            'subtotal': sub_usd,
            'subtotal_usd': sub_usd,
            'subtotal_bs': sub_bs
        })

    return render_template(
        'index.html',
        productos=productos,
        carrito=carrito_vista,
        total_carrito=redondear(total_carrito_usd),
        total_carrito_usd=redondear(total_carrito_usd),
        total_carrito_bs=redondear(total_carrito_bs)
    )

@app.route('/agregar_carrito', methods=['POST'])
def agregar_carrito():
    prod_id = request.form.get('producto_id')
    cantidad = int(request.form.get('cantidad', 1) or 1)
    producto = ejecutar_consulta('SELECT * FROM productos WHERE id = ?', (prod_id,), fetchone=True)
    if producto:
        p_dict = dict(producto)
        costo_val = float(p_dict.get('costo') or 0.0)
        p_usd = redondear(float(p_dict.get('precio') or 0.0))
        p_bs = redondear(float(p_dict.get('precio_bs') or (costo_val * 1.5525 if costo_val > 0 else p_usd)))
        carrito = session.get('carrito', [])
        encontrado = False
        for item in carrito:
            if str(item.get('id')) == str(p_dict['id']):
                nueva_cant = item['cantidad'] + cantidad
                item['cantidad'] = min(nueva_cant, int(p_dict.get('stock') or nueva_cant))
                encontrado = True
                break
        if not encontrado:
            carrito.append({
                'id': p_dict['id'],
                'nombre': p_dict['nombre'],
                'precio': p_usd,
                'precio_bs': p_bs,
                'cantidad': cantidad
            })
        session['carrito'] = carrito
        flash(f"Se agregó {p_dict['nombre']} al carrito.")
    return redirect(url_for('index'))

@app.route('/vaciar_carrito')
def vaciar_carrito():
    session.pop('carrito', None)
    flash("El carrito ha sido vaciado.")
    return redirect(url_for('index'))

# --- PUNTO DE VENTA (POS) ---

@app.route('/cajero/pos')
@role_required('admin', 'cajero')
def pos_cajero():
    try:
        productos_raw = ejecutar_consulta('SELECT * FROM productos WHERE stock > 0 ORDER BY nombre ASC', fetchall=True) or []
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
    except Exception:
        return f"<h1>Error del sistema:</h1><pre>{traceback.format_exc()}</pre>"

@app.route('/procesar_venta', methods=['POST'])
def procesar_venta():
    if not session.get('logged_in'):
        return jsonify({'exito': False, 'mensaje': 'Sesión vencida. Vuelve a iniciar sesión.'})

    conn = None
    try:
        data = request.get_json() or {}
        items = data.get('items', [])
        metodo_pago = data.get('metodo_pago', 'Efectivo $')
        referencia = data.get('referencia', 'N/A')
        
        tasa_raw = str(data.get('tasa_cambio', '50.0')).replace(',', '.')
        tasa_cambio = float(tasa_raw)
        
        desglose_pago = data.get('desglose_pago', '')
        cliente_nombre = data.get('cliente_nombre', 'Cliente Mostrador')
        cliente_telefono = data.get('cliente_telefono', '')
        usuario = session.get('username', 'Cajero')

        if not items:
            return jsonify({'exito': False, 'mensaje': 'El carrito está vacío'})

        total_venta = sum(float(item['precio']) * int(item['cantidad']) for item in items)
        total_unidades = sum(int(item.get('cantidad', 1)) for item in items)
        monto_bs = redondear(total_venta * tasa_cambio)
        fecha_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        resumen_nombres = ", ".join([f"{it.get('cantidad', 1)}x {it['nombre']}" for it in items[:3]])
        if len(items) > 3:
            resumen_nombres += f" (+{len(items)-3} más)"

        conn = obtener_conexion()
        if es_postgres():
            cursor = conn.cursor(cursor_factory=RealDictCursor)
        else:
            cursor = conn.cursor()

        def fmt(q):
            return q.replace('?', '%s') if es_postgres() else q

        query_venta = fmt("INSERT INTO ventas (fecha, total, metodo_pago, referencia, usuario, producto_nombre, tasa_cambio, monto_bs, desglose_pago, cliente_nombre, cliente_telefono, cantidad, precio) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
        if es_postgres():
            query_venta += ' RETURNING id'
        
        cursor.execute(query_venta, (
            fecha_hora, redondear(total_venta), metodo_pago, referencia, usuario,
            resumen_nombres, tasa_cambio, monto_bs, desglose_pago, cliente_nombre,
            cliente_telefono, total_unidades, redondear(total_venta)
        ))
        
        if es_postgres():
            venta_id = cursor.fetchone()['id']
        else:
            venta_id = cursor.lastrowid

        for it in items:
            cod = str(it.get('codigo', '') or '').strip()
            nom = str(it.get('nombre', '') or '').strip()
            cant = int(it.get('cantidad', 1))
            p_unit = float(it.get('precio', 0.0))
            p_unit_bs = redondear(p_unit * tasa_cambio)
            subt = redondear(p_unit * cant)

            prod_row = None
            if cod:
                cursor.execute(fmt('SELECT id FROM productos WHERE UPPER(TRIM(codigo)) = UPPER(?) AND UPPER(TRIM(nombre)) = UPPER(?) LIMIT 1'), (cod, nom))
                prod_row = cursor.fetchone()
            if not prod_row:
                cursor.execute(fmt('SELECT id FROM productos WHERE UPPER(TRIM(nombre)) = UPPER(?) LIMIT 1'), (nom,))
                prod_row = cursor.fetchone()
            
            if es_postgres():
                prod_id = prod_row['id'] if prod_row else None
            else:
                prod_id = prod_row[0] if prod_row else None

            if prod_id is not None:
                cursor.execute(fmt('UPDATE productos SET stock = stock - ? WHERE id = ?'), (cant, prod_id))
            else:
                cursor.execute(fmt('UPDATE productos SET stock = stock - ? WHERE UPPER(TRIM(nombre)) = UPPER(?)'), (cant, nom))

            cursor.execute(fmt("INSERT INTO detalle_ventas (venta_id, producto_id, nombre_producto, cantidad, precio_unitario, precio_unitario_bs, subtotal) VALUES (?, ?, ?, ?, ?, ?, ?)"), (venta_id, prod_id, nom, cant, p_unit, p_unit_bs, subt))

        conn.commit()
        cursor.close()
        conn.close()

        # El mensajero invisible avisa a la página web que descuente estos productos
        enviar_mensaje_a_la_nube('descontar_stock', items)

        return jsonify({'exito': True, 'venta_id': venta_id})

    except Exception as e:
        error_detallado = traceback.format_exc()
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({'exito': False, 'mensaje': f"ERROR AL COBRAR:\n{str(e)}\n\nDETALLE:\n{error_detallado}"})

@app.route('/ticket/<int:venta_id>')
def ticket(venta_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    venta = ejecutar_consulta('SELECT * FROM ventas WHERE id = ?', (venta_id,), fetchone=True)
    if not venta:
        return "Comprobante no encontrado", 404

    v_dict = dict(venta)
    v_dict['total'] = float(v_dict['total'] or 0.0)
    v_dict['monto_bs'] = float(v_dict['monto_bs'] or 0.0)
    v_dict['tasa_cambio'] = float(v_dict['tasa_cambio'] or 0.0)

    detalles_raw = ejecutar_consulta('SELECT * FROM detalle_ventas WHERE venta_id = ?', (venta_id,), fetchall=True) or []
    detalles = []
    for d in detalles_raw:
        d_dict = dict(d)
        d_dict['precio_unitario'] = float(d_dict['precio_unitario'] or 0.0)
        d_dict['precio_unitario_bs'] = float(d_dict['precio_unitario_bs'] or 0.0)
        d_dict['subtotal'] = float(d_dict['subtotal'] or 0.0)
        detalles.append(d_dict)

    return render_template('ticket.html', venta=v_dict, detalles=detalles)

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
        ganancia_estimada=redondear(ganancia_estimada),
        total_costo_inversion=redondear(total_costo_inversion),
        total_valor_venta=redondear(total_valor_venta),
        total_ventas_usd=redondear(total_ventas_usd)
    )

@app.route('/importar')
@role_required('admin')
def importar():
    return render_template('importar.html')

@app.route('/exportar_inventario_excel')
@role_required('admin')
def exportar_inventario_excel():
    productos = ejecutar_consulta('SELECT * FROM productos ORDER BY categoria ASC, nombre ASC', fetchall=True) or []
    filas_productos = []
    for p in productos:
        costo = float(p['costo'] or 0.0)
        p_bs = float(p['precio_bs'] or (costo * 1.5525))
        p_div = float(p['precio'] or 0.0)
        stock = int(p['stock'] or 0)

        filas_productos.append({
            'codigo': p['codigo'],
            'nombre': p['nombre'],
            'costo': costo,
            'precio bs': redondear(p_bs),
            'precio': redondear(p_div),
            'stock': stock,
            'categoria': p['categoria'],
            'descuento': float(p['descuento'] or 0.0)
        })

    ventas = ejecutar_consulta('SELECT * FROM ventas ORDER BY id DESC', fetchall=True) or []
    filas_ventas = []
    for v in ventas:
        filas_ventas.append({
            'Nro Comprobante': f"#{v['id']:06d}",
            'Fecha y Hora': v['fecha'],
            'Cliente': v['cliente_nombre'] if 'cliente_nombre' in v else 'Cliente',
            'Teléfono WhatsApp': v['cliente_telefono'] if 'cliente_telefono' in v else '',
            'Total USD ($)': float(v['total']),
            'Total Bs': float(v['monto_bs'] or 0.0),
            'Modalidad Pago': v['metodo_pago'],
            'Referencia': v['referencia'],
            'Cajero': v['usuario'],
            'Desglose': v['desglose_pago'] if 'desglose_pago' in v else ''
        })

    salida = io.BytesIO()
    with pd.ExcelWriter(salida, engine='openpyxl') as writer:
        pd.DataFrame(filas_productos).to_excel(writer, index=False, sheet_name='Inventario Maestro')
        pd.DataFrame(filas_ventas).to_excel(writer, index=False, sheet_name='Historial Ventas')

    salida.seek(0)
    nombre_archivo = f"Respaldo_Completo_RyD_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(
        salida,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=nombre_archivo
    )

@app.route('/agregar', methods=['GET', 'POST'])
@role_required('admin')
def agregar():
    if request.method == 'POST':
        codigo = request.form.get('codigo', '').strip().upper()
        nombre = request.form.get('nombre', '').strip()
        costo = float(request.form.get('costo', 0) or 0)
        precio_bs = float(request.form.get('precio_bs', 0) or (costo * 1.5525))
        precio = float(request.form.get('precio', 0) or 0)
        stock = int(request.form.get('stock', 0) or 0)
        categoria = request.form.get('categoria', 'General').strip()
        descuento = float(request.form.get('descuento', 0) or 0)

        if not codigo or not nombre:
            flash("Código y Nombre son obligatorios")
            return redirect(url_for('agregar'))

        ejecutar_consulta("INSERT INTO productos (codigo, nombre, costo, precio_bs, precio, stock, categoria, descuento) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (codigo, nombre, redondear(costo), redondear(precio_bs), redondear(precio), stock, categoria, descuento), commit=True)
        sincronizar_catalogo_completo_a_nube()
        return redirect(url_for('admin'))

    return render_template('agregar.html')

@app.route('/editar/<int:id>', methods=['GET', 'POST'])
@role_required('admin')
def editar(id):
    if request.method == 'POST':
        codigo = request.form.get('codigo', '').strip()
        nombre = request.form.get('nombre', '').strip()
        costo = float(request.form.get('costo', 0) or 0)
        precio_bs = float(request.form.get('precio_bs', 0) or 0)
        precio = float(request.form.get('precio', 0) or 0)
        stock = int(request.form.get('stock', 0) or 0)
        categoria = request.form.get('categoria', 'General').strip()

        ejecutar_consulta("UPDATE productos SET codigo = ?, nombre = ?, costo = ?, precio_bs = ?, precio = ?, stock = ?, categoria = ? WHERE id = ?", (codigo, nombre, redondear(costo), redondear(precio_bs), redondear(precio), stock, categoria, id), commit=True)
        sincronizar_catalogo_completo_a_nube()
        return redirect(url_for('admin'))

    producto = ejecutar_consulta('SELECT * FROM productos WHERE id = ?', (id,), fetchone=True)
    if not producto:
        return "Producto no encontrado", 404
    return render_template('editar.html', producto=producto)

@app.route('/eliminar/<int:id>')
@role_required('admin')
def eliminar(id):
    ejecutar_consulta('DELETE FROM productos WHERE id = ?', (id,), commit=True)
    return redirect(url_for('admin'))

@app.route('/proveedores')
@role_required('admin')
def proveedores():
    proveedores_lista = ejecutar_consulta('SELECT * FROM proveedores ORDER BY nombre ASC', fetchall=True) or []
    return render_template('proveedores.html', proveedores_guardados=proveedores_lista)

@app.route('/guardar_factura_proveedor', methods=['POST'])
def guardar_factura_proveedor():
    data = request.get_json() or {}
    items = data.get('items', [])
    proveedor_nom = (data.get('proveedor') or '').strip().title()
    telefono_prov = (data.get('telefono') or '').strip()

    if not items:
        return jsonify({'exito': False, 'mensaje': 'Sin productos válidos para guardar'})

    conn = None
    try:
        conn = obtener_conexion()
        if es_postgres():
            cursor = conn.cursor(cursor_factory=RealDictCursor)
        else:
            cursor = conn.cursor()

        def fmt(q):
            return q.replace('?', '%s') if es_postgres() else q

        if proveedor_nom and proveedor_nom != "Proveedor General":
            monto_compra_actual = sum(float(it.get('costo', 0)) * int(it.get('stock', 0)) for it in items)
            fecha_hoy = date.today().strftime('%Y-%m-%d')

            cursor.execute(fmt('SELECT id FROM proveedores WHERE UPPER(nombre) = ?'), (proveedor_nom.upper(),))
            prov_existente = cursor.fetchone()

            if prov_existente:
                prov_id = prov_existente['id'] if es_postgres() else prov_existente[0]
                cursor.execute(fmt("UPDATE proveedores SET telefono = CASE WHEN ? != '' THEN ? ELSE telefono END, ultima_compra = ?, total_compras = total_compras + ? WHERE id = ?"), (telefono_prov, telefono_prov, fecha_hoy, redondear(monto_compra_actual), prov_id))
            else:
                cursor.execute(fmt("INSERT INTO proveedores (nombre, telefono, ultima_compra, total_compras) VALUES (?, ?, ?, ?)"), (proveedor_nom, telefono_prov, fecha_hoy, redondear(monto_compra_actual)))

        for it in items:
            cod = str(it.get('codigo', '')).strip().upper()
            nom = str(it.get('nombre', '')).strip()
            
            costo = redondear(it.get('costo', 0))
            precio_bs = redondear(it.get('precio_bs', 0) or (costo * 1.5525))
            precio = redondear(it.get('precio', 0) or (costo * 1.35))
            
            stock_nuevo = int(it.get('stock', 0) or 0)
            cat = str(it.get('categoria', 'General')).strip()
            desc = float(it.get('descuento', 0) or 0)

            if not nom:
                continue

            cursor.execute(fmt('SELECT id FROM productos WHERE UPPER(TRIM(nombre)) = ?'), (nom.upper(),))
            existente = cursor.fetchone()

            if existente:
                prod_id = existente['id'] if es_postgres() else existente[0]
                cursor.execute(fmt("UPDATE productos SET codigo = ?, costo = ?, precio_bs = ?, precio = ?, stock = stock + ?, categoria = ?, descuento = ? WHERE id = ?"), (cod, costo, precio_bs, precio, stock_nuevo, cat, desc, prod_id))
            else:
                cursor.execute(fmt("INSERT INTO productos (codigo, nombre, costo, precio_bs, precio, stock, categoria, descuento) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"), (cod, nom, costo, precio_bs, precio, stock_nuevo, cat, desc))

        conn.commit()
        cursor.close()
        conn.close()

        # El mensajero invisible actualiza todo el catálogo en la web
        sincronizar_catalogo_completo_a_nube()

        return jsonify({'exito': True})

    except Exception as e:
        error_detallado = traceback.format_exc()
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({'exito': False, 'mensaje': f"ERROR INTERNO:\n{str(e)}\n\nDETALLE:\n{error_detallado}"})

@app.route('/actualizar_proveedor_telefono', methods=['POST'])
def actualizar_proveedor_telefono():
    data = request.get_json() or {}
    prov_id = data.get('id')
    nuevo_tel = (data.get('telefono') or '').strip()

    if not prov_id:
        return jsonify({'exito': False, 'mensaje': 'ID requerido'})

    try:
        ejecutar_consulta('UPDATE proveedores SET telefono = ? WHERE id = ?', (nuevo_tel, prov_id), commit=True)
        return jsonify({'exito': True})
    except Exception as e:
        return jsonify({'exito': False, 'mensaje': str(e)})

@app.route('/procesar_factura_ocr', methods=['POST'])
def procesar_factura_ocr():
    texto_directo = request.form.get('texto_ocr', '')
    archivo = request.files.get('factura')

    comercio = ""
    telefono = ""
    items = []
    total_paginas = 1

    try:
        texto_completo = ""

        if archivo and any(archivo.filename.lower().endswith(ext) for ext in ['.xlsx', '.xls', '.csv']):
            df_in = pd.read_excel(archivo) if not archivo.filename.lower().endswith('.csv') else pd.read_csv(archivo)
            
            col_cod = None
            col_nom = None
            col_costo = None
            col_precio_bs = None
            col_precio = None
            col_stock = None
            col_cat = None

            for c in df_in.columns:
                c_str = str(c).lower().strip()
                if c_str in ['codigo', 'código', 'sku']:
                    col_cod = c
                elif any(k in c_str for k in ['producto', 'descripcion', 'descripci', 'nombre']):
                    col_nom = c
                elif any(k in c_str for k in ['costo unit', 'costo net', 'costo']):
                    col_costo = c
                elif c_str in ['precio bs', 'precio_bs', 'pvp bs', 'precio tasa']:
                    col_precio_bs = c
                elif c_str in ['precio', 'precio venta', 'pvp', 'precio ($)', 'precio usd', 'precio divisa']:
                    col_precio = c
                elif any(k in c_str for k in ['stock', 'cantidad', 'cant']):
                    col_stock = c
                elif any(k in c_str for k in ['categoria', 'categor']):
                    col_cat = c

            if col_nom is None and len(df_in.columns) >= 2:
                col_nom = df_in.columns[1]
            if col_costo is None and len(df_in.columns) >= 3:
                col_costo = df_in.columns[2]

            for _, row in df_in.iterrows():
                nom = str(row[col_nom]).strip() if col_nom and pd.notna(row[col_nom]) else ""
                if not nom or nom.lower() in ['nan', 'producto', 'total', 'subtotal']:
                    continue

                cod_val = str(row[col_cod]).strip() if col_cod and pd.notna(row[col_cod]) else ""
                
                try:
                    c_val = float(row[col_costo]) if col_costo and pd.notna(row[col_costo]) else 0.0
                except Exception:
                    c_val = 0.0

                try:
                    s_val = int(row[col_stock]) if col_stock and pd.notna(row[col_stock]) else 1
                except Exception:
                    s_val = 1

                try:
                    p_bs_val = float(row[col_precio_bs]) if col_precio_bs and pd.notna(row[col_precio_bs]) else (c_val * 1.5525)
                except Exception:
                    p_bs_val = c_val * 1.5525

                try:
                    p_val = float(row[col_precio]) if col_precio and pd.notna(row[col_precio]) else (c_val * 1.35)
                except Exception:
                    p_val = c_val * 1.35

                cat_val = str(row[col_cat]).strip() if col_cat and pd.notna(row[col_cat]) else clasificar_categoria_ryd(nom)

                items.append({
                    'codigo': cod_val,
                    'nombre': nom.title(),
                    'costo': redondear(c_val),
                    'precio_bs': redondear(p_bs_val),
                    'precio': redondear(p_val),
                    'stock': s_val,
                    'categoria': cat_val
                })

            return jsonify({
                'exito': True,
                'comercio': 'Catálogo Maestro Excel',
                'telefono': '',
                'total_paginas': 1,
                'items': items
            })

        if archivo and (archivo.filename or "").lower().endswith('.pdf'):
            reader = PdfReader(io.BytesIO(archivo.read()))
            total_paginas = len(reader.pages)
            for page in reader.pages:
                texto_completo += "\n" + (page.extract_text() or "")
        elif texto_directo:
            texto_completo = texto_directo

        up = texto_completo.upper()
        if "TODOBELLA" in up or "TODO BELLA" in up:
            comercio = "Todo Bella"
            telefono = "04127494517"
        elif "MICELI" in up:
            comercio = "Comercializadora Miceli Corp, S.A."
            telefono = "04129583694"
        elif "STOREFIT" in up:
            comercio = "Storefit Internacional, C.A."
            telefono = "04121100769"
        elif "HOGAR IDEAL" in up:
            comercio = "Hogar Ideal 1441, C.A."
        elif "GOOD TIMES" in up:
            comercio = "Inversiones J.S Good Times C.A"
        elif "AURA" in up:
            comercio = "Aura Profesional"
            telefono = "04127494813"

        if not telefono:
            m_tel = re.search(r'(?:04\d{2}[\s\-]?\d{7}|\+?58[\s\-]?\d{10})', texto_completo)
            if m_tel:
                telefono = m_tel.group(0).replace(" ", "").replace("-", "")

        lines = [l.strip() for l in texto_completo.split('\n') if l.strip()]

        for line in lines:
            l_clean = line.replace('|', ' ').strip()
            m_tb = re.match(r'^(\d+)\s*(?:Und\.?|Pza\.?|Unidad(?:es)?)?\s+(.+?)\s+([0-9]+[\.,][0-9]{2})\s+([0-9]+[\.,][0-9]{2})$', l_clean, re.IGNORECASE)
            if m_tb:
                cant = int(m_tb.group(1))
                desc = m_tb.group(2).strip()
                costo = float(m_tb.group(3).replace(',', '.'))
                if not any(k in desc.upper() for k in ['SUBTOTAL', 'TOTAL', 'DESCRIPCION', 'CANTIDAD', 'ITEMS']):
                    items.append({
                        'codigo': '',
                        'nombre': desc.title(),
                        'costo': redondear(costo),
                        'precio_bs': redondear(costo * 1.5525),
                        'precio': redondear(costo * 1.35),
                        'stock': cant,
                        'categoria': clasificar_categoria_ryd(desc)
                    })

        return jsonify({
            'exito': True,
            'comercio': comercio,
            'telefono': telefono,
            'total_paginas': total_paginas,
            'items': items
        })

    except Exception as e:
        return jsonify({'exito': False, 'mensaje': f'Error en procesamiento: {str(e)}'})

class Objetoflexible(dict):
    def __getattr__(self, name):
        return self.get(name, 0.0)

@app.route('/ventas')
@role_required('admin')
def historial_ventas():
    try:
        ventas_raw = ejecutar_consulta('SELECT * FROM ventas ORDER BY id DESC', fetchall=True) or []
        ventas_lista = []
        total_general_usd = 0.0
        total_general_bs = 0.0

        for v in ventas_raw:
            v_dict = dict(v)
            detalles_raw = ejecutar_consulta('SELECT * FROM detalle_ventas WHERE venta_id = ?', (v_dict['id'],), fetchall=True) or []
            detalles = []
            for d in detalles_raw:
                d_dict = dict(d)
                d_dict['nombre'] = d_dict.get('nombre_producto', '')
                d_dict['precio'] = float(d_dict.get('precio_unitario', 0.0) or 0.0)
                d_dict['precio_unitario'] = float(d_dict.get('precio_unitario', 0.0) or 0.0)
                d_dict['precio_unitario_bs'] = float(d_dict.get('precio_unitario_bs', 0.0) or 0.0)
                d_dict['subtotal'] = float(d_dict.get('subtotal', 0.0) or 0.0)
                detalles.append(d_dict)
            
            tot_v = float(v_dict.get('total') or 0.0)
            bs_v = float(v_dict.get('monto_bs') or 0.0)
            total_general_usd += tot_v
            total_general_bs += bs_v

            resumen_txt = v_dict.get('producto_nombre') or ", ".join([f"{d['cantidad']}x {d['nombre_producto']}" for d in detalles])

            ventas_lista.append({
                'id': v_dict['id'],
                'fecha': v_dict['fecha'],
                'total': redondear(tot_v),
                'precio': redondear(tot_v),
                'monto_bs': redondear(bs_v),
                'tasa_cambio': float(v_dict.get('tasa_cambio') or 50.0),
                'cantidad': int(v_dict.get('cantidad') or len(detalles) or 1),
                'producto_nombre': resumen_txt,
                'metodo_pago': v_dict.get('metodo_pago', 'Efectivo $'),
                'referencia': v_dict.get('referencia', 'N/A'),
                'usuario': v_dict.get('usuario', 'Cajero'),
                'desglose_pago': v_dict.get('desglose_pago', ''),
                'cliente_nombre': v_dict.get('cliente_nombre', 'Cliente Mostrador'),
                'cliente_telefono': v_dict.get('cliente_telefono', ''),
                'items': detalles,
                'detalles': detalles
            })

        return render_template(
            'ventas.html',
            ventas=ventas_lista,
            total_ventas=redondear(total_general_usd),
            total_usd=redondear(total_general_usd),
            total_bs=redondear(total_general_bs)
        )
    except Exception:
        return f"<h1>Error en Historial de Ventas:</h1><pre>{traceback.format_exc()}</pre>"

@app.route('/cierre-caja')
@role_required('admin', 'cajero')
def cierre_caja():
    hoy = date.today().strftime('%Y-%m-%d')
    try:
        ventas_raw = ejecutar_consulta("SELECT * FROM ventas WHERE fecha LIKE ? ORDER BY id DESC", (f"{hoy}%",), fetchall=True) or []
        ventas_hoy = []
        total_usd = 0.0
        total_bs = 0.0
        total_unidades = 0
        metodos_totales = {}

        efectivo_usd = 0.0
        efectivo_bs = 0.0
        pago_movil = 0.0
        punto_venta = 0.0
        binance = 0.0
        mixto = 0.0

        for v in ventas_raw:
            v_dict = dict(v)
            tot = float(v_dict.get('total') or 0.0)
            bs = float(v_dict.get('monto_bs') or 0.0)
            cant = int(v_dict.get('cantidad') or 1)
            m = v_dict.get('metodo_pago', 'Efectivo $')
            
            v_dict['total'] = redondear(tot)
            v_dict['monto_bs'] = redondear(bs)
            
            total_usd += tot
            total_bs += bs
            total_unidades += cant
            metodos_totales[m] = redondear(metodos_totales.get(m, 0.0) + tot)

            m_low = m.lower()
            if 'efectivo $' in m_low or 'divisa' in m_low:
                efectivo_usd += tot
            elif 'efectivo bs' in m_low:
                efectivo_bs += tot
            elif 'móvil' in m_low or 'movil' in m_low:
                pago_movil += tot
            elif 'punto' in m_low:
                punto_venta += tot
            elif 'binance' in m_low:
                binance += tot
            else:
                mixto += tot

            ventas_hoy.append(v_dict)

        resumen_obj = Objetoflexible({
            'fecha': hoy,
            'total_usd': redondear(total_usd),
            'total_bs': redondear(total_bs),
            'total_ventas': len(ventas_hoy),
            'cantidad_ventas': len(ventas_hoy),
            'transacciones': len(ventas_hoy),
            'total_unidades': total_unidades,
            'unidades_vendidas': total_unidades,
            'efectivo_usd': redondear(efectivo_usd),
            'efectivo_bs': redondear(efectivo_bs),
            'pago_movil': redondear(pago_movil),
            'punto_venta': redondear(punto_venta),
            'binance': redondear(binance),
            'mixto': redondear(mixto),
            'metodos': metodos_totales,
            'por_metodo': metodos_totales
        })

        return render_template(
            'cierre_caja.html',
            ventas=ventas_hoy,
            total_usd=redondear(total_usd),
            total_bs=redondear(total_bs),
            metodos=metodos_totales,
            fecha=hoy,
            resumen=resumen_obj
        )
    except Exception:
        return f"<h1>Error en Cierre de Caja:</h1><pre>{traceback.format_exc()}</pre>"

if __name__ == '__main__':
    sincronizar_catalogo_completo_a_nube()
    app.run(host='0.0.0.0', port=5000, debug=True)