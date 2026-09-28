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

app = Flask(__name__)
app.secret_key = 'clave_secreta_super_segura_ryd_2026'

UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def normalizar_texto(texto):
    if not isinstance(texto, str):
        return str(texto)
    texto = unicodedata.normalize('NFD', texto)
    texto = ''.join(c for c in texto if unicodedata.category(c) != 'Mn')
    return texto.strip().lower()

def obtener_conexion():
    conn = sqlite3.connect('inventario.db')
    conn.row_factory = sqlite3.Row
    return conn

def inicializar_db():
    conn = obtener_conexion()
    cursor = conn.cursor()

    # 1. Tabla productos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT UNIQUE,
            nombre TEXT NOT NULL,
            costo REAL DEFAULT 0.0,
            precio REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            categoria TEXT DEFAULT 'General',
            descuento REAL DEFAULT 0.0,
            imagen TEXT
        )
    ''')

    for col_def in ['imagen TEXT', 'descuento REAL DEFAULT 0.0', 'costo REAL DEFAULT 0.0']:
        try:
            cursor.execute(f'ALTER TABLE productos ADD COLUMN {col_def}')
        except sqlite3.OperationalError:
            pass

    # 2. Tabla proveedores (Blindada contra columnas faltantes)
    cursor.execute('''
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
    ''')

    for col_prov in [
        'telefono TEXT DEFAULT ""',
        'contacto TEXT DEFAULT ""',
        'direccion TEXT DEFAULT ""',
        'rif TEXT DEFAULT ""',
        'ultima_compra TEXT DEFAULT ""',
        'total_compras REAL DEFAULT 0.0'
    ]:
        try:
            cursor.execute(f'ALTER TABLE proveedores ADD COLUMN {col_prov}')
        except sqlite3.OperationalError:
            pass

    # 3. Tabla ventas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            total REAL DEFAULT 0.0,
            metodo_pago TEXT DEFAULT 'Efectivo $',
            referencia TEXT DEFAULT '',
            usuario TEXT DEFAULT 'Cajero',
            producto_nombre TEXT DEFAULT ''
        )
    ''')

    for col_def in [
        'total REAL DEFAULT 0.0',
        'metodo_pago TEXT DEFAULT "Efectivo $"',
        'referencia TEXT DEFAULT ""',
        'usuario TEXT DEFAULT "Cajero"',
        'producto_nombre TEXT DEFAULT ""'
    ]:
        try:
            cursor.execute(f'ALTER TABLE ventas ADD COLUMN {col_def}')
        except sqlite3.OperationalError:
            pass

    # 4. Tabla detalle de ventas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalle_ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venta_id INTEGER,
            producto_id INTEGER,
            nombre_producto TEXT,
            cantidad INTEGER,
            precio_unitario REAL,
            subtotal REAL,
            FOREIGN KEY (venta_id) REFERENCES ventas(id)
        )
    ''')

    # 5. Tabla usuarios
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            rol TEXT NOT NULL DEFAULT 'cajero'
        )
    ''')

    cursor.execute("SELECT id FROM usuarios WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO usuarios (username, password, rol) VALUES ('admin', 'admin123', 'admin')")

    cursor.execute("SELECT id FROM usuarios WHERE username = 'cajero'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO usuarios (username, password, rol) VALUES ('cajero', 'cajero2026', 'cajero')")

    conn.commit()
    conn.close()

inicializar_db()

def clasificar_categoria_ryd(descripcion):
    desc = descripcion.lower()
    if any(k in desc for k in ['olla', 'sm-200', 'ventilador', 'lampara', 'extractor', 'pulidor', 'drill', 'esterilizador', 'maquina']):
        return "Aparatología"
    if any(k in desc for k in ['pestañ', 'ceja', 'henna', 'lash', 'brow', 'volumen', 'pigmento']):
        return "Cejas y Pestañas"
    if any(k in desc for k in ['esmalte', 'lipstick', 'brush on', 'gel', 'finish', 'rubber', 'cuticula', 'protein', 'polygel', 'acrygel', 'serum', 'nail', 'primer', 'ultrabond', 'blossom', 'base coat', 'builder', 'tijera', 'cortauna', 'lima', 'punta', 'jelly', 'pincel', 'bledo', 'dappen', 'guillotina', 'empujador']):
        return "Uñas"
    if any(k in desc for k in ['gorro', 'guante', 'desechable', 'tapa boca', 'mascarilla', 'toalla', 'separador', 'palitos', 'hisopo']):
        return "Desechables"
    if any(k in desc for k in ['shampoo', 'alisado', 'laminado', 'termoprotector', 'blower', 'tratamiento', 'peine', 'difusor', 'ondas', 'cepillo', 'keratina', 'tinte', 'plancha', 'cabello', 'acondicionador']):
        return "Cabello"
    if any(k in desc for k in ['intimo', 'jabon', 'arandano', 'manzanilla', 'cera depilatoria', 'roll on', 'banda depilacion']):
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

        conn = obtener_conexion()
        user_info = conn.execute(
            'SELECT * FROM usuarios WHERE LOWER(username) = ? AND password = ?',
            (usuario, password)
        ).fetchone()
        conn.close()

        if user_info:
            session['logged_in'] = True
            session['user_id'] = user_info['id']
            session['username'] = user_info['username']
            rol_obtenido = user_info['rol'] if 'rol' in user_info.keys() else 'cajero'
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
    conn = obtener_conexion()
    productos = conn.execute('SELECT * FROM productos WHERE stock > 0 ORDER BY nombre ASC').fetchall()
    categorias_rows = conn.execute('SELECT DISTINCT categoria FROM productos WHERE stock > 0').fetchall()
    categorias = [row['categoria'] for row in categorias_rows if row['categoria']]
    conn.close()
    return render_template('pos.html', productos=productos, categorias=categorias)

@app.route('/procesar_venta', methods=['POST'])
def procesar_venta():
    if not session.get('logged_in'):
        return jsonify({'exito': False, 'mensaje': 'Sesión vencida'}), 401

    data = request.get_json() or {}
    items = data.get('items', [])
    metodo_pago = data.get('metodo_pago', 'Efectivo $')
    referencia = data.get('referencia', 'N/A')
    usuario = session.get('username', 'Cajero')

    if not items:
        return jsonify({'exito': False, 'mensaje': 'El carrito está vacío'}), 400

    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        total_venta = sum(float(item['precio']) * int(item['cantidad']) for item in items)
        fecha_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        resumen_nombres = ", ".join([it['nombre'] for it in items[:3]])
        if len(items) > 3:
            resumen_nombres += f" (+{len(items)-3} más)"

        cols_ventas = [col[1] for col in cursor.execute("PRAGMA table_info(ventas)").fetchall()]
        campos = ['fecha', 'total', 'metodo_pago', 'referencia', 'usuario']
        valores = [fecha_hora, round(total_venta, 2), metodo_pago, referencia, usuario]

        if 'producto_nombre' in cols_ventas:
            campos.append('producto_nombre')
            valores.append(resumen_nombres)

        placeholders = ', '.join(['?'] * len(campos))
        columnas_str = ', '.join(campos)

        cursor.execute(f'INSERT INTO ventas ({columnas_str}) VALUES ({placeholders})', valores)
        venta_id = cursor.lastrowid

        for it in items:
            cod = it['codigo']
            cant = int(it['cantidad'])
            p_unit = float(it['precio'])
            subt = round(p_unit * cant, 2)
            nom = it['nombre']

            cursor.execute('UPDATE productos SET stock = stock - ? WHERE codigo = ?', (cant, cod))
            cursor.execute('''
                INSERT INTO detalle_ventas (venta_id, producto_id, nombre_producto, cantidad, precio_unitario, subtotal)
                VALUES (?, (SELECT id FROM productos WHERE codigo = ?), ?, ?, ?, ?)
            ''', (venta_id, cod, nom, cant, p_unit, subt))

        conn.commit()
        conn.close()
        return jsonify({'exito': True, 'venta_id': venta_id})
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'exito': False, 'mensaje': f"Error al procesar: {str(e)}"}), 500

@app.route('/ticket/<int:venta_id>')
def ticket(venta_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = obtener_conexion()
    venta = conn.execute('SELECT * FROM ventas WHERE id = ?', (venta_id,)).fetchone()
    if not venta:
        conn.close()
        return "Comprobante no encontrado", 404

    detalles = conn.execute('SELECT * FROM detalle_ventas WHERE venta_id = ?', (venta_id,)).fetchall()
    conn.close()
    return render_template('ticket.html', venta=venta, detalles=detalles)


# --- PANEL ADMINISTRADOR Y EXPORTACIÓN EXCEL ---

@app.route('/admin')
@role_required('admin')
def admin():
    conn = obtener_conexion()
    productos_raw = conn.execute('SELECT * FROM productos ORDER BY id DESC').fetchall()

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
        ventas_total_row = conn.execute('SELECT SUM(total) as total_ventas FROM ventas').fetchone()
        if ventas_total_row and ventas_total_row['total_ventas']:
            total_ventas_usd = float(ventas_total_row['total_ventas'])
    except Exception:
        total_ventas_usd = 0.0

    conn.close()

    return render_template(
        'admin.html',
        productos=productos_raw,
        ganancia_estimada=round(ganancia_estimada, 2),
        total_costo_inversion=round(total_costo_inversion, 2),
        total_valor_venta=round(total_valor_venta, 2),
        total_ventas_usd=round(total_ventas_usd, 2)
    )

@app.route('/exportar_inventario_excel')
@role_required('admin')
def exportar_inventario_excel():
    conn = obtener_conexion()
    productos = conn.execute('SELECT * FROM productos ORDER BY categoria ASC, nombre ASC').fetchall()
    conn.close()

    filas = []
    for p in productos:
        costo = float(p['costo'] or 0.0)
        precio = float(p['precio'] or 0.0)
        stock = int(p['stock'] or 0)
        margen = round(((precio - costo) / costo) * 100, 1) if costo > 0 else 30.0
        inv_costo = round(costo * stock, 2)
        val_venta = round(precio * stock, 2)

        filas.append({
            'Categoría': p['categoria'],
            'Código / SKU': p['codigo'],
            'Producto / Descripción': p['nombre'],
            'Costo Unit. ($)': costo,
            'Descuento ($)': float(p['descuento'] or 0.0),
            'Costo Net Unit. ($)': costo,
            'Precio Venta ($)': precio,
            '% Margen': f"{margen}%",
            'Stock': stock,
            'Inversión Total Costo ($)': inv_costo,
            'Valor Total Venta ($)': val_venta
        })

    df = pd.DataFrame(filas)
    salida = io.BytesIO()
    with pd.ExcelWriter(salida, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Inventario RyD')

    salida.seek(0)
    nombre_archivo = f"Inventario_RyD_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
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
        precio = float(request.form.get('precio', 0) or 0)
        stock = int(request.form.get('stock', 0) or 0)
        categoria = request.form.get('categoria', 'General').strip()
        descuento = float(request.form.get('descuento', 0) or 0)

        if not codigo or not nombre:
            flash("Código y Nombre son obligatorios")
            return redirect(url_for('agregar'))

        conn = obtener_conexion()
        existente = conn.execute('SELECT id FROM productos WHERE UPPER(codigo) = ?', (codigo,)).fetchone()
        if existente:
            conn.close()
            flash(f"El código {codigo} ya está registrado.")
            return redirect(url_for('agregar'))

        conn.execute('''
            INSERT INTO productos (codigo, nombre, costo, precio, stock, categoria, descuento)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (codigo, nombre, costo, precio, stock, categoria, descuento))
        conn.commit()
        conn.close()
        return redirect(url_for('admin'))

    return render_template('agregar.html')

@app.route('/editar/<int:id>', methods=['GET', 'POST'])
@role_required('admin')
def editar(id):
    conn = obtener_conexion()
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        costo = float(request.form.get('costo', 0) or 0)
        precio = float(request.form.get('precio', 0) or 0)
        stock = int(request.form.get('stock', 0) or 0)
        categoria = request.form.get('categoria', 'General').strip()

        conn.execute('''
            UPDATE productos 
            SET nombre = ?, costo = ?, precio = ?, stock = ?, categoria = ? 
            WHERE id = ?
        ''', (nombre, costo, precio, stock, categoria, id))
        conn.commit()
        conn.close()
        return redirect(url_for('admin'))

    producto = conn.execute('SELECT * FROM productos WHERE id = ?', (id,)).fetchone()
    conn.close()
    if not producto:
        return "Producto no encontrado", 404
    return render_template('editar.html', producto=producto)

@app.route('/eliminar/<int:id>')
@role_required('admin')
def eliminar(id):
    conn = obtener_conexion()
    conn.execute('DELETE FROM productos WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))


# --- MÓDULO DE PROVEEDORES Y PARSER UNIVERSAL MULTI-FORMATO ---

@app.route('/proveedores')
@role_required('admin')
def proveedores():
    conn = obtener_conexion()
    proveedores_lista = conn.execute('SELECT * FROM proveedores ORDER BY nombre ASC').fetchall()
    conn.close()
    return render_template('proveedores.html', proveedores_guardados=proveedores_lista)

@app.route('/guardar_factura_proveedor', methods=['POST'])
@role_required('admin')
def guardar_factura_proveedor():
    data = request.get_json() or {}
    items = data.get('items', [])
    proveedor_nom = (data.get('proveedor') or '').strip().title()
    telefono_prov = (data.get('telefono') or '').strip()

    if not items:
        return jsonify({'exito': False, 'mensaje': 'Sin productos válidos para guardar'}), 400

    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        # 1. Asegurar dinámicamente las columnas en proveedores antes de insertar
        cols_prov = [c[1] for c in cursor.execute("PRAGMA table_info(proveedores)").fetchall()]
        for col_name, col_type in [('ultima_compra', 'TEXT DEFAULT ""'), ('total_compras', 'REAL DEFAULT 0.0'), ('telefono', 'TEXT DEFAULT ""')]:
            if col_name not in cols_prov:
                try:
                    cursor.execute(f"ALTER TABLE proveedores ADD COLUMN {col_name} {col_type}")
                except Exception:
                    pass

        # 2. Registro o actualización de proveedor en el Directorio
        if proveedor_nom and proveedor_nom != "Proveedor General":
            monto_compra_actual = sum(float(it.get('costo', 0)) * int(it.get('stock', 0)) for it in items)
            fecha_hoy = date.today().strftime('%Y-%m-%d')

            prov_existente = cursor.execute('SELECT id FROM proveedores WHERE UPPER(nombre) = ?', (proveedor_nom.upper(),)).fetchone()
            if prov_existente:
                cursor.execute('''
                    UPDATE proveedores 
                    SET telefono = CASE WHEN ? != '' THEN ? ELSE telefono END,
                        ultima_compra = ?,
                        total_compras = total_compras + ?
                    WHERE id = ?
                ''', (telefono_prov, telefono_prov, fecha_hoy, round(monto_compra_actual, 2), prov_existente['id']))
            else:
                cursor.execute('''
                    INSERT INTO proveedores (nombre, telefono, ultima_compra, total_compras)
                    VALUES (?, ?, ?, ?)
                ''', (proveedor_nom, telefono_prov, fecha_hoy, round(monto_compra_actual, 2)))

        # 3. Guardado o actualización de inventario anti-duplicados
        for it in items:
            cod = str(it.get('codigo', '')).strip().upper()
            nom = str(it.get('nombre', '')).strip()
            costo = round(float(it.get('costo', 0) or 0), 2)
            precio = round(float(it.get('precio', 0) or 0), 2)
            stock_nuevo = int(it.get('stock', 0) or 0)
            cat = str(it.get('categoria', 'General')).strip()
            desc = float(it.get('descuento', 0) or 0)

            if not cod or not nom:
                continue

            existente = cursor.execute(
                'SELECT id, stock FROM productos WHERE UPPER(codigo) = ? OR UPPER(TRIM(nombre)) = ?',
                (cod, nom.upper())
            ).fetchone()

            if existente:
                cursor.execute('''
                    UPDATE productos 
                    SET costo = ?, precio = ?, stock = stock + ?, categoria = ?, descuento = ?
                    WHERE id = ?
                ''', (costo, precio, stock_nuevo, cat, desc, existente['id']))
            else:
                cursor.execute('''
                    INSERT INTO productos (codigo, nombre, costo, precio, stock, categoria, descuento)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (cod, nom, costo, precio, stock_nuevo, cat, desc))

        conn.commit()
        conn.close()
        return jsonify({'exito': True})
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'exito': False, 'mensaje': str(e)}), 500

@app.route('/procesar_factura_ocr', methods=['POST'])
@role_required('admin')
def procesar_factura_ocr():
    texto_directo = request.form.get('texto_ocr', '')
    archivo = request.files.get('factura')

    comercio = ""
    telefono = ""
    items = []
    total_paginas = 1

    try:
        texto_completo = ""

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

        # FORMATO 1: Presupuestos y Notas Tabulares (Todo Bella, Aura, etc.)
        for line in lines:
            l_clean = line.replace('|', ' ').strip()
            m_tb = re.match(r'^(\d+)\s*(?:Und\.?|Pza\.?|Unidad(?:es)?)?\s+(.+?)\s+([0-9]+[\.,][0-9]{2})\s+([0-9]+[\.,][0-9]{2})$', l_clean, re.IGNORECASE)
            if m_tb:
                cant = int(m_tb.group(1))
                desc = m_tb.group(2).strip()
                costo = float(m_tb.group(3).replace(',', '.'))
                if not any(k in desc.upper() for k in ['SUBTOTAL', 'TOTAL', 'DESCRIPCION', 'CANTIDAD', 'ITEMS']):
                    items.append({
                        'codigo': f"PRV-{str(len(items) + 1).zfill(3)}",
                        'nombre': desc.title(),
                        'costo': costo,
                        'unidades_empaque': 1,
                        'cant_comprada': cant,
                        'stock': cant,
                        'categoria': clasificar_categoria_ryd(desc)
                    })

        # FORMATO 2: Nota POS de Despacho (Good Times / fact roysviner)
        if not items:
            i = 0
            while i < len(lines):
                linea_act = lines[i]
                if "Lineas" in linea_act or "SUBTTL" in linea_act or ("TOTAL" in linea_act and len(items) > 5):
                    break
                m_qty = re.match(r'^(\d+)[,\.]00$', linea_act)
                if m_qty and (i + 2) < len(lines):
                    cant = int(m_qty.group(1))
                    desc = lines[i + 1].strip()
                    price_line = lines[i + 2].strip()

                    costo_encontrado = None
                    for split_pos in range(1, len(price_line)):
                        s1 = price_line[:split_pos].replace(',', '.')
                        s2 = price_line[split_pos:].replace(',', '.')
                        try:
                            f1, f2 = float(s1), float(s2)
                            if abs(cant * f2 - f1) < 0.05:
                                costo_encontrado = f2; break
                            if abs(cant * f1 - f2) < 0.05:
                                costo_encontrado = f1; break
                        except:
                            continue

                    if costo_encontrado is None:
                        partes = re.findall(r'\d+[,\.]\d{2}', price_line)
                        costo_encontrado = float(partes[-1].replace(',', '.')) if partes else 0.0

                    if desc and costo_encontrado > 0:
                        items.append({
                            'codigo': f"PRV-{str(len(items) + 1).zfill(3)}",
                            'nombre': desc.title(),
                            'costo': round(costo_encontrado, 2),
                            'unidades_empaque': 1,
                            'cant_comprada': cant,
                            'stock': cant,
                            'categoria': clasificar_categoria_ryd(desc)
                        })
                        i += 3
                        continue
                i += 1

        # FORMATO 3: Notas de Entrega con Código inicial (Miceli Corp / NE ROYVINER)
        if not items:
            for linea in lines:
                m_miceli = re.match(r'^([A-Z0-9\-_]{3,18})\s+(.+?)\s+(\d+)\s+([0-9]+[,\.][0-9]{2})\s+([0-9]+[,\.][0-9]{2})$', linea, re.IGNORECASE)
                if m_miceli:
                    cod, desc, cant, p_unit, _ = m_miceli.groups()
                    items.append({
                        'codigo': cod.upper(),
                        'nombre': desc.strip().title(),
                        'costo': float(p_unit.replace(',', '.')),
                        'unidades_empaque': 1,
                        'cant_comprada': int(cant),
                        'stock': int(cant),
                        'categoria': clasificar_categoria_ryd(desc)
                    })

        # FORMATO 4: Notas con Cantidad y Signo Dólar (Storefit)
        if not items:
            for linea in lines:
                m_store = re.match(r'^(\d+)\s+(.+?)\s+\$?([0-9]+[,\.][0-9]{2})\s+\$?([0-9]+[,\.][0-9]{2})$', linea, re.IGNORECASE)
                if m_store:
                    cant, desc, p_unit, _ = m_store.groups()
                    desc_limpia = desc.strip()
                    if not any(k in desc_limpia.upper() for k in ['TOTAL', 'SUBTOTAL', 'SUB-TOTAL', 'ORDEN', 'DESCRIPCION']):
                        items.append({
                            'codigo': f"PRV-{str(len(items) + 1).zfill(3)}",
                            'nombre': desc_limpia.title(),
                            'costo': float(p_unit.replace(',', '.')),
                            'unidades_empaque': 1,
                            'cant_comprada': int(cant),
                            'stock': int(cant),
                            'categoria': clasificar_categoria_ryd(desc_limpia)
                        })

        # FORMATO 5: Tickets de Caja Térmicos / SENIAT (Hogar Ideal)
        if not items:
            for i, linea in enumerate(lines):
                if '/' in linea and not any(k in linea.upper() for k in ['FECHA', 'RAZ', 'CEDULA', 'RIF', 'DIR', 'VALENCIA']):
                    partes = linea.split('/', 1)
                    sku = partes[0].strip().replace(' ', '')
                    nom = partes[1].strip()
                    
                    qty = 1
                    cost = 0.0

                    for offset in [-1, 1]:
                        idx_check = i + offset
                        if 0 <= idx_check < len(lines):
                            m_q = re.search(r'(\d+)(?:[,\.]\d+)?\s*[xX]\s*(?:Bs\.?|\$)?\s*([0-9\.,]+)', lines[idx_check])
                            if m_q:
                                qty = int(m_q.group(1))
                                p_clean = m_q.group(2).replace('.', '').replace(',', '.') if (',' in m_q.group(2) and '.' in m_q.group(2)) else m_q.group(2).replace(',', '.')
                                try:
                                    cost = float(p_clean)
                                    break
                                except:
                                    pass

                    if not any(k in nom.upper() for k in ['TOTAL', 'SUBTOTAL', 'DESCUENTO', 'EXENTO', 'IVA']):
                        items.append({
                            'codigo': sku.upper() if len(sku) > 2 else f"PRV-{str(len(items)+1).zfill(3)}",
                            'nombre': nom.title(),
                            'costo': round(cost, 2),
                            'unidades_empaque': 1,
                            'cant_comprada': qty,
                            'stock': qty,
                            'categoria': clasificar_categoria_ryd(nom)
                        })

        return jsonify({
            'exito': True,
            'comercio': comercio,
            'telefono': telefono,
            'total_paginas': total_paginas,
            'items': items
        })

    except Exception as e:
        return jsonify({'exito': False, 'mensaje': f'Error en procesamiento: {str(e)}'}), 500


# --- HISTORIAL Y CIERRE ---

@app.route('/ventas')
@role_required('admin')
def historial_ventas():
    conn = obtener_conexion()
    try:
        ventas_raw = conn.execute('SELECT * FROM ventas ORDER BY id DESC').fetchall()
        ventas_lista = []
        for v in ventas_raw:
            detalles = conn.execute('SELECT * FROM detalle_ventas WHERE venta_id = ?', (v['id'],)).fetchall()
            ventas_lista.append({
                'id': v['id'],
                'fecha': v['fecha'],
                'total': v['total'] if 'total' in v.keys() else 0.0,
                'metodo_pago': v['metodo_pago'] if 'metodo_pago' in v.keys() else 'Efectivo $',
                'referencia': v['referencia'] if 'referencia' in v.keys() and v['referencia'] else 'N/A',
                'usuario': v['usuario'] if 'usuario' in v.keys() and v['usuario'] else 'Cajero',
                'items': detalles
            })
        conn.close()
        return render_template('ventas.html', ventas=ventas_lista)
    except Exception:
        conn.close()
        return render_template('ventas.html', ventas=[])

@app.route('/cierre-caja')
@role_required('admin')
def cierre_caja():
    conn = obtener_conexion()
    hoy = date.today().strftime('%Y-%m-%d')
    try:
        ventas_hoy = conn.execute("SELECT * FROM ventas WHERE fecha LIKE ? ORDER BY id DESC", (f"{hoy}%",)).fetchall()
        total_usd = sum(float(v['total'] or 0.0) for v in ventas_hoy if 'total' in v.keys())
        metodos_totales = {}
        for v in ventas_hoy:
            m = v['metodo_pago'] if 'metodo_pago' in v.keys() else 'Efectivo $'
            tot = float(v['total'] or 0.0) if 'total' in v.keys() else 0.0
            metodos_totales[m] = metodos_totales.get(m, 0.0) + tot
    except Exception:
        ventas_hoy = []
        total_usd = 0.0
        metodos_totales = {}

    conn.close()
    return render_template('cierre_caja.html', ventas=ventas_hoy, total_usd=round(total_usd, 2), metodos=metodos_totales, fecha=hoy)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
