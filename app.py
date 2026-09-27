import os
import io
import re
import sqlite3
import unicodedata
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from pypdf import PdfReader

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

    # Tabla productos
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

    # Columnas opcionales por si la base ya existía
    try:
        cursor.execute('ALTER TABLE productos ADD COLUMN imagen TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE productos ADD COLUMN descuento REAL DEFAULT 0.0')
    except sqlite3.OperationalError:
        pass

    # Tabla ventas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            total REAL NOT NULL,
            metodo_pago TEXT NOT NULL,
            referencia TEXT,
            usuario TEXT DEFAULT 'Cajero'
        )
    ''')

    # Tabla detalle de ventas
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

    # Tabla usuarios
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            rol TEXT NOT NULL DEFAULT 'cajero'
        )
    ''')

    # Usuarios maestros predeterminados
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
    if any(k in desc for k in ['gorro', 'guante', 'desechable', 'tapa boca', 'mascarilla', 'toalla', 'separador', 'palitos']):
        return "Desechables"
    if any(k in desc for k in ['peine', 'difusor', 'ondas', 'cepillo', 'shampoo', 'keratina', 'tinte', 'plancha']):
        return "Cabello"
    if any(k in desc for k in ['intimo', 'jabon intimo', 'cera depilatoria', 'roll on', 'banda depilacion']):
        return "Íntimo"
    if any(k in desc for k in ['alcohol', 'acetona', 'algodon', 'cleanser', 'sanitizante', 'exfoliante', 'espuma', 'sponge', 'mantequilla', 'gota cicatrizante']):
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


# --- RUTAS DE AUTENTICACIÓN ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = (request.form.get('username') or '').strip().lower()
        password = (request.form.get('password') or '').strip()

        # 1. Acceso Directo de Emergencia Garantizado
        if usuario == 'admin' and password in ['admin123', 'admin2026', 'admin']:
            session['logged_in'] = True
            session['user_id'] = 1
            session['username'] = 'admin'
            session['user_role'] = 'admin'
            session['carrito'] = []
            return redirect(url_for('admin'))

        if usuario == 'cajero' and password in ['cajero2026', 'cajero123', 'cajero']:
            session['logged_in'] = True
            session['user_id'] = 2
            session['username'] = 'cajero'
            session['user_role'] = 'cajero'
            session['carrito'] = []
            return redirect(url_for('pos_cajero'))

        # 2. Verificación en Base de Datos
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
            session['carrito'] = []

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


@app.route('/resetear_clave_admin')
def resetear_clave_admin():
    conn = obtener_conexion()
    conn.execute("UPDATE usuarios SET password = 'admin123' WHERE username = 'admin'")
    conn.commit()
    conn.close()
    return "<h2 style='font-family: sans-serif; color: #9D7B38; text-align: center; margin-top: 50px;'>Clave restablecida a: <b>admin123</b><br><br><a href='/login'>Ir a Iniciar Sesión</a></h2>"


# --- RUTAS DE TIENDA Y PUNTO DE VENTA ---

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
    referencia = data.get('referencia', '')
    usuario = session.get('username', 'Cajero')

    if not items:
        return jsonify({'exito': False, 'mensaje': 'El carrito está vacío'}), 400

    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        total_venta = sum(float(item['precio']) * int(item['cantidad']) for item in items)
        fecha_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        cursor.execute('''
            INSERT INTO ventas (fecha, total, metodo_pago, referencia, usuario)
            VALUES (?, ?, ?, ?, ?)
        ''', (fecha_hora, round(total_venta, 2), metodo_pago, referencia, usuario))
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
        return jsonify({'exito': False, 'mensaje': str(e)}), 500


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


# --- PANEL ADMINISTRADOR CON CONTABILIDAD EXACTA ---

@app.route('/admin')
@role_required('admin')
def admin():
    conn = obtener_conexion()
    productos_raw = conn.execute('SELECT * FROM productos ORDER BY id DESC').fetchall()

    # Cálculo contable al centavo
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

    ventas_total_row = conn.execute('SELECT SUM(total) as total_ventas FROM ventas').fetchone()
    total_ventas_usd = float(ventas_total_row['total_ventas'] or 0.0) if ventas_total_row else 0.0

    conn.close()

    return render_template(
        'admin.html',
        productos=productos_raw,
        ganancia_estimada=round(ganancia_estimada, 2),
        total_costo_inversion=round(total_costo_inversion, 2),
        total_valor_venta=round(total_valor_venta, 2),
        total_ventas_usd=round(total_ventas_usd, 2)
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


# --- MÓDULO DE PROVEEDORES, FACTURAS Y OCR ---

@app.route('/proveedores')
@role_required('admin')
def proveedores():
    return render_template('proveedores.html')


@app.route('/guardar_factura_proveedor', methods=['POST'])
@role_required('admin')
def guardar_factura_proveedor():
    data = request.get_json() or {}
    items = data.get('items', [])

    if not items:
        return jsonify({'exito': False, 'mensaje': 'Sin productos válidos para guardar'}), 400

    conn = obtener_conexion()
    try:
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

            existente = conn.execute(
                'SELECT id, stock FROM productos WHERE UPPER(codigo) = ? OR UPPER(TRIM(nombre)) = ?',
                (cod, nom.upper())
            ).fetchone()

            if existente:
                conn.execute('''
                    UPDATE productos 
                    SET costo = ?, precio = ?, stock = stock + ?, categoria = ?, descuento = ?
                    WHERE id = ?
                ''', (costo, precio, stock_nuevo, cat, desc, existente['id']))
            else:
                conn.execute('''
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
    if 'factura' not in request.files:
        return jsonify({'exito': False, 'mensaje': 'No se cargó ningún archivo'}), 400

    archivo = request.files['factura']
    nombre = (archivo.filename or "").lower()

    comercio = ""
    telefono = ""
    items = []
    total_paginas = 0

    try:
        if nombre.endswith('.pdf'):
            reader = PdfReader(io.BytesIO(archivo.read()))
            total_paginas = len(reader.pages)
            texto_completo = ""

            for page in reader.pages:
                texto_completo += "\n" + (page.extract_text() or "")

            if "GOOD TIMES" in texto_completo.upper():
                comercio = "Inversiones J.S Good Times C.A"
            elif "AURA" in texto_completo.upper():
                comercio = "Aura Profesional"
            else:
                lineas_sup = [l.strip() for l in texto_completo.split('\n') if l.strip()][:15]
                for l in lineas_sup:
                    if any(k in l.upper() for k in ["C.A", "S.A", "INVERSIONES", "DISTRIBUIDORA", "COMERCIAL"]):
                        comercio = l.title()
                        break
                if not comercio and lineas_sup:
                    comercio = lineas_sup[0].title()

            m_tel = re.search(r'(?:Telf|Tel|Cel|WhatsApp)?[:\s]*(04\d{2}[\s\-]?\d{7}|\+?58[\s\-]?\d{10})', texto_completo, re.IGNORECASE)
            if m_tel:
                telefono = m_tel.group(1).replace(" ", "").replace("-", "")

            lineas = [l.strip() for l in texto_completo.split('\n') if l.strip()]

            # Formato A: Nota de Despacho POS
            i = 0
            while i < len(lineas):
                linea_actual = lineas[i]
                if "Lineas" in linea_actual or "SUBTTL" in linea_actual or ("TOTAL" in linea_actual and len(items) > 5):
                    break

                m_qty = re.match(r'^(\d+)[,\.]00$', linea_actual)
                if m_qty and (i + 2) < len(lineas):
                    cant = int(m_qty.group(1))
                    desc = lineas[i + 1].strip()
                    price_line = lineas[i + 2].strip()

                    costo_encontrado = None
                    for split_pos in range(1, len(price_line)):
                        s1 = price_line[:split_pos].replace(',', '.')
                        s2 = price_line[split_pos:].replace(',', '.')
                        try:
                            f1 = float(s1)
                            f2 = float(s2)
                            if abs(cant * f2 - f1) < 0.05:
                                costo_encontrado = f2
                                break
                            if abs(cant * f1 - f2) < 0.05:
                                costo_encontrado = f1
                                break
                        except:
                            continue

                    if costo_encontrado is None:
                        partes = re.findall(r'\d+[,\.]\d{2}', price_line)
                        if partes:
                            costo_encontrado = float(partes[-1].replace(',', '.'))
                        else:
                            costo_encontrado = 0.0

                    if desc and costo_encontrado > 0:
                        cat = clasificar_categoria_ryd(desc)
                        items.append({
                            'codigo': f"PRV-{str(len(items) + 1).zfill(3)}",
                            'nombre': desc.title(),
                            'costo': round(costo_encontrado, 2),
                            'unidades_empaque': 1,
                            'cant_comprada': cant,
                            'stock': cant,
                            'categoria': cat
                        })
                        i += 3
                        continue
                i += 1

            # Formato B: Factura Presupuesto Estándar
            if not items:
                clean_lines = [l.replace('|', ' ').strip() for l in lineas]
                idx = 0
                while idx < len(clean_lines):
                    linea = clean_lines[idx]
                    m_inline = re.match(r'^(\d+)\s*(?:Und\.?|Pza\.?|Unidad)?\s+(.+?)\s+(\d+[\.,]\d{2})\s+(\d+[\.,]\d{2})$', linea, re.IGNORECASE)
                    if m_inline:
                        cant = int(m_inline.group(1))
                        desc = m_inline.group(2).strip()
                        costo_compra = float(m_inline.group(3).replace(',', '.'))
                        idx += 1
                    else:
                        m_und = re.match(r'^(\d+)\s*(?:Und\.?|Pza\.?|Unidad)?$', linea, re.IGNORECASE)
                        if m_und:
                            cant = int(m_und.group(1))
                            idx += 1
                            desc = ""
                            costo_compra = 0.0
                            if idx < len(clean_lines):
                                desc = clean_lines[idx].strip()
                                idx += 1
                            if idx < len(clean_lines):
                                m_p = re.search(r'(\d+[\.,]\d{2})', clean_lines[idx])
                                if m_p:
                                    costo_compra = float(m_p.group(1).replace(',', '.'))
                                    idx += 1
                            if idx < len(clean_lines) and re.match(r'^\d+[\.,]\d{2}$', clean_lines[idx].strip()):
                                idx += 1
                        else:
                            idx += 1
                            continue

                    if desc and costo_compra > 0:
                        cat = clasificar_categoria_ryd(desc)
                        items.append({
                            'codigo': f"PRV-{str(len(items) + 1).zfill(3)}",
                            'nombre': desc.title(),
                            'costo': round(costo_compra, 2),
                            'unidades_empaque': 1,
                            'cant_comprada': cant,
                            'stock': cant,
                            'categoria': cat
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


# --- MÓDULOS DE HISTORIAL Y CIERRE DE CAJA ---

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
                'total': v['total'],
                'metodo_pago': v['metodo_pago'],
                'referencia': v['referencia'] or 'N/A',
                'usuario': v['usuario'] or 'Cajero',
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
    ventas_hoy = conn.execute("SELECT * FROM ventas WHERE fecha LIKE ? ORDER BY id DESC", (f"{hoy}%",)).fetchall()

    total_usd = sum(float(v['total']) for v in ventas_hoy)
    metodos_totales = {}
    for v in ventas_hoy:
        m = v['metodo_pago']
        metodos_totales[m] = metodos_totales.get(m, 0.0) + float(v['total'])

    conn.close()
    return render_template('cierre_caja.html', ventas=ventas_hoy, total_usd=round(total_usd, 2), metodos=metodos_totales, fecha=hoy)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
