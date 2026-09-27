import os
import sqlite3
import pandas as pd
import unicodedata
from datetime import date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'clave_secreta_super_segura_ryd'

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
    try:
        conn.execute('ALTER TABLE productos ADD COLUMN imagen TEXT')
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.close()

inicializar_db()

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('logged_in'):
                flash('Por favor inicia sesión para acceder.')
                return redirect(url_for('login'))
            if session.get('user_role') not in roles:
                flash('Acceso denegado: No tienes permisos para acceder a esta sección.')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --- RUTAS ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = obtener_conexion()
        user_info = conn.execute(
            'SELECT * FROM usuarios WHERE username = ? AND password = ?',
            (username, password)
        ).fetchone()
        conn.close()

        if user_info:
            session['logged_in'] = True
            session['username'] = user_info['username']
            session['user_role'] = user_info['role']
            session['carrito'] = []

            if user_info['role'] == 'admin':
                return redirect(url_for('admin'))
            elif user_info['role'] == 'cajero':
                return redirect(url_for('pos_cajero'))
            else:
                return redirect(url_for('index'))
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
    conn = obtener_conexion()
    productos = conn.execute('SELECT * FROM productos WHERE stock > 0').fetchall()
    conn.close()

    carrito = session.get('carrito', [])
    total_carrito = sum(item['subtotal'] for item in carrito)

    return render_template('index.html', productos=productos, carrito=carrito, total_carrito=total_carrito)

@app.route('/cajero/pos')
@role_required('admin', 'cajero')
def pos_cajero():
    conn = obtener_conexion()
    productos = conn.execute('SELECT * FROM productos WHERE stock > 0').fetchall()
    categorias_rows = conn.execute('SELECT DISTINCT categoria FROM productos WHERE stock > 0').fetchall()
    categorias = [row['categoria'] for row in categorias_rows if row['categoria']]
    conn.close()
    
    carrito = session.get('carrito', [])
    total_carrito = sum(item['subtotal'] for item in carrito)
    
    return render_template('pos.html', productos=productos, categorias=categorias, carrito=carrito, total_carrito=total_carrito)

# --- PANEL ADMINISTRADOR CON MATEMÁTICA CONTABLE EXACTA ---

@app.route('/admin')
def admin():
    if 'user_id' not in session or session.get('username') != 'admin':
        return redirect(url_for('login'))

    conn = obtener_conexion()
    
    # Asegurar que la tabla productos tenga la columna descuento para evitar caídas
    try:
        conn.execute('ALTER TABLE productos ADD COLUMN descuento REAL DEFAULT 0')
        conn.commit()
    except:
        pass

    productos_raw = conn.execute('SELECT * FROM productos ORDER BY id DESC').fetchall()

    # Cálculo contable riguroso al centavo
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

    # Ventas totales registradas históricas
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


# --- AGREGAR PRODUCTO INDIVIDUAL (SIN ERRORES DE BASE DE DATOS) ---

@app.route('/agregar', methods=['GET', 'POST'])
def agregar():
    if 'user_id' not in session or session.get('username') != 'admin':
        return redirect(url_for('login'))

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
        # Verificar duplicado
        existente = conn.execute('SELECT id FROM productos WHERE UPPER(codigo) = ?', (codigo,)).fetchone()
        if existente:
            conn.close()
            flash(f"El código {codigo} ya está registrado en el inventario.")
            return redirect(url_for('agregar'))

        conn.execute('''
            INSERT INTO productos (codigo, nombre, costo, precio, stock, categoria, descuento)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (codigo, nombre, costo, precio, stock, categoria, descuento))
        conn.commit()
        conn.close()

        return redirect(url_for('admin'))

    return render_template('agregar.html')


# --- GUARDAR FACTURA PROVEEDOR CON PRECISIÓN MATEMÁTICA ---

@app.route('/guardar_factura_proveedor', methods=['POST'])
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

        return jsonify({
            'exito': True,
            'comercio': comercio,
            'telefono': telefono,
            'total_paginas': total_paginas,
            'items': items
        })

    except Exception as e:
        return jsonify({'exito': False, 'mensaje': f'Error en procesamiento: {str(e)}'}), 500

        return jsonify({
            'exito': True,
            'comercio': comercio,
            'telefono': telefono,
            'total_paginas': total_paginas,
            'items': items
        })

    except Exception as e:
        return jsonify({'exito': False, 'mensaje': f'Error en procesamiento: {str(e)}'}), 500

        return jsonify({
            'exito': True,
            'comercio': comercio,
            'telefono': telefono,
            'total_paginas': total_paginas,
            'items': items
        })

    except Exception as e:
        return jsonify({'exito': False, 'mensaje': f'Error en procesamiento: {str(e)}'}), 500

        return jsonify({
            'exito': True,
            'comercio': comercio,
            'telefono': telefono,
            'total_paginas': total_paginas,
            'items': items
        })

    except Exception as e:
        return jsonify({'exito': False, 'mensaje': f'Error en procesamiento: {str(e)}'}), 500
# --- MÓDULO HISTORIAL DE VENTAS ---

@app.route('/ventas')
def historial_ventas():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = obtener_conexion()
    try:
        # Consulta todas las ventas ordenadas desde la más reciente
        ventas_raw = conn.execute('''
            SELECT id, fecha, total, metodo_pago, referencia, usuario 
            FROM ventas 
            ORDER BY id DESC
        ''').fetchall()
        
        ventas_lista = []
        for v in ventas_raw:
            # Extrae los artículos que componen cada ticket
            detalles = conn.execute('''
                SELECT nombre_producto, cantidad, precio_unitario, subtotal 
                FROM detalle_ventas 
                WHERE venta_id = ?
            ''', (v['id'],)).fetchall()
            
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
    except Exception as e:
        conn.close()
        # Si la tabla aún no tiene registros o columnas opcionales, renderiza lista vacía sin error
        return render_template('ventas.html', ventas=[])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
