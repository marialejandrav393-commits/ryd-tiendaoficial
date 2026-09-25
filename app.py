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

@app.route('/admin')
@role_required('admin')
def admin():
    conn = obtener_conexion()
    productos = conn.execute('SELECT * FROM productos').fetchall()
    
    ventas_rows = []
    try:
        ventas_rows = conn.execute('SELECT * FROM ventas ORDER BY fecha DESC').fetchall()
    except sqlite3.OperationalError:
        ventas_rows = []

    total_ventas = sum(v['subtotal'] for v in ventas_rows) if ventas_rows else 0.0
    
    total_ganancias = 0.0
    for p in productos:
        costo = dict(p).get('costo', 0) or 0
        precio = dict(p).get('precio', 0) or 0
        stock = dict(p).get('stock', 0) or 0
        total_ganancias += (precio - costo) * stock

    conn.close()

    return render_template(
        'admin.html',
        productos=productos,
        ventas=ventas_rows,
        total_ventas=total_ventas,
        total_ganancias=total_ganancias
    )

@app.route('/cierre-caja')
@role_required('admin')
def cierre_caja():
    hoy = date.today().strftime('%Y-%m-%d')
    conn = obtener_conexion()
    ventas_hoy = []
    try:
        ventas_hoy = conn.execute('SELECT * FROM ventas WHERE date(fecha) = ? ORDER BY fecha DESC', (hoy,)).fetchall()
    except sqlite3.OperationalError:
        ventas_hoy = []
    conn.close()

    def obtener_monto(v):
        return dict(v).get('subtotal', 0) or dict(v).get('total', 0) or 0.0

    total_recaudado = sum(obtener_monto(v) for v in ventas_hoy)
    
    # Separación por método de pago:
    total_efectivo_usd = sum(obtener_monto(v) for v in ventas_hoy if dict(v).get('metodo_pago') in ['Efectivo $', 'Efectivo USD', 'Efectivo Divisa', 'Efectivo'])
    total_efectivo_bs = sum(obtener_monto(v) for v in ventas_hoy if dict(v).get('metodo_pago') in ['Efectivo Bs', 'Efectivo Bolívares', 'Bs Efectivo'])
    total_pago_movil = sum(obtener_monto(v) for v in ventas_hoy if dict(v).get('metodo_pago') in ['Pago Móvil', 'Transferencia', 'Transferencia/PagoMóvil', 'Pago Movil'])
    total_binance = sum(obtener_monto(v) for v in ventas_hoy if dict(v).get('metodo_pago') in ['Binance', 'USDT', 'Binance Pay'])

    resumen = {
        'fecha': hoy,
        'cantidad_ventas': len(ventas_hoy),
        'total_general': total_recaudado,
        'efectivo_usd': total_efectivo_usd,
        'efectivo_bs': total_efectivo_bs,
        'pago_movil': total_pago_movil,
        'binance': total_binance
    }

    return render_template('cierre_caja.html', ventas=ventas_hoy, resumen=resumen)

@app.route('/check_codigo')
@role_required('admin')
def check_codigo():
    codigo = request.args.get('codigo', '').strip()
    if not codigo:
        return jsonify({'existe': False})
    
    conn = obtener_conexion()
    prod = conn.execute('SELECT id FROM productos WHERE codigo = ?', (codigo,)).fetchone()
    conn.close()
    
    return jsonify({'existe': bool(prod)})

@app.route('/agregar_producto', methods=['POST'])
@role_required('admin')
def agregar_producto():
    codigo = request.form.get('codigo', '').strip()
    nombre = request.form.get('nombre')
    costo = float(request.form.get('costo', 0))
    precio = float(request.form.get('precio', 0))
    stock = int(request.form.get('stock', 0))
    descuento = float(request.form.get('descuento', 0))
    categoria = request.form.get('categoria', 'General').strip() or 'General'
    
    nombre_imagen = None
    if 'imagen' in request.files:
        file = request.files['imagen']
        if file and file.filename != '' and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            nombre_imagen = f"{codigo}_{filename}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], nombre_imagen))

    conn = obtener_conexion()
    try:
        conn.execute(
            'INSERT INTO productos (codigo, nombre, costo, precio, stock, descuento, categoria, imagen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (codigo, nombre, costo, precio, stock, descuento, categoria, nombre_imagen)
        )
        conn.commit()
        flash('Producto registrado con éxito.')
    except sqlite3.IntegrityError:
        flash(f'Error: El código "{codigo}" ya pertenece a otro producto en la base de datos.')
    except Exception as e:
        flash(f'Ocurrió un error inesperado: {str(e)}')
    finally:
        conn.close()
    
    return redirect(url_for('admin'))

@app.route('/admin/editar_producto/<int:id>', methods=['POST'])
@role_required('admin')
def editar_producto(id):
    codigo = request.form.get('codigo', '').strip()
    nombre = request.form.get('nombre', '').strip()
    costo = float(request.form.get('costo', 0))
    precio = float(request.form.get('precio', 0))
    stock = int(request.form.get('stock', 0))
    categoria = request.form.get('categoria', 'General').strip() or 'General'

    conn = obtener_conexion()
    try:
        if 'imagen' in request.files:
            file = request.files['imagen']
            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                nombre_imagen = f"{codigo}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], nombre_imagen))
                conn.execute('UPDATE productos SET imagen = ? WHERE id = ?', (nombre_imagen, id))

        conn.execute('''
            UPDATE productos 
            SET codigo = ?, nombre = ?, costo = ?, precio = ?, stock = ?, categoria = ?
            WHERE id = ?
        ''', (codigo, nombre, costo, precio, stock, categoria, id))
        conn.commit()
        flash('Producto actualizado correctamente.')
    except sqlite3.IntegrityError:
        flash(f'Error: El código "{codigo}" ya está asignado a otro producto.')
    except Exception as e:
        flash(f'Error al actualizar: {str(e)}')
    finally:
        conn.close()
    
    return redirect(url_for('admin'))

@app.route('/importar_excel', methods=['POST'])
@role_required('admin')
def importar_excel():
    if 'archivo_excel' not in request.files:
        flash('No se seleccionó ningún archivo.')
        return redirect(url_for('admin'))
        
    file = request.files['archivo_excel']
    if file.filename == '':
        flash('No se seleccionó ningún archivo.')
        return redirect(url_for('admin'))

    filename = file.filename.lower()
    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(file)
        elif filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file)
        else:
            flash('Formato no soportado. Usa .xlsx, .xls o .csv')
            return redirect(url_for('admin'))
        
        column_map = {}
        for original_col in df.columns:
            norm = normalizar_texto(original_col)
            if 'cod' in norm:
                column_map[original_col] = 'codigo'
            elif 'nom' in norm or 'prod' in norm or 'descrip' in norm:
                column_map[original_col] = 'nombre'
            elif 'precio' in norm or 'pvp' in norm:
                column_map[original_col] = 'precio'
            elif 'stock' in norm or 'cant' in norm:
                column_map[original_col] = 'stock'
            elif 'cost' in norm:
                column_map[original_col] = 'costo'
            elif 'cat' in norm:
                column_map[original_col] = 'categoria'
            elif 'descuento' in norm:
                column_map[original_col] = 'descuento'

        df.rename(columns=column_map, inplace=True)
        
        columnas_requeridas = ['codigo', 'nombre', 'precio', 'stock']
        for col in columnas_requeridas:
            if col not in df.columns:
                flash(f'El archivo debe incluir al menos las columnas: codigo, nombre, precio, stock.')
                return redirect(url_for('admin'))

        conn = obtener_conexion()
        procesados = 0

        for _, row in df.iterrows():
            codigo = str(row['codigo']).strip()
            if not codigo or pd.isna(row['codigo']) or codigo.lower() == 'nan':
                continue

            nombre = str(row['nombre']).strip()
            precio = float(row.get('precio', 0) or 0)
            stock = int(row.get('stock', 0) or 0)
            costo = float(row.get('costo', 0) or 0) if 'costo' in df.columns and not pd.isna(row.get('costo')) else 0.0
            
            cat_val = row.get('categoria')
            if 'categoria' in df.columns and not pd.isna(cat_val) and str(cat_val).strip() != '' and str(cat_val).lower() != 'nan':
                categoria = str(cat_val).strip().capitalize()
            else:
                categoria = 'General'

            descuento = float(row.get('descuento', 0) or 0) if 'descuento' in df.columns and not pd.isna(row.get('descuento')) else 0.0

            conn.execute('''
                INSERT INTO productos (codigo, nombre, costo, precio, stock, descuento, categoria)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(codigo) DO UPDATE SET
                    nombre=excluded.nombre,
                    costo=excluded.costo,
                    precio=excluded.precio,
                    stock=excluded.stock,
                    descuento=excluded.descuento,
                    categoria=excluded.categoria
            ''', (codigo, nombre, costo, precio, stock, descuento, categoria))
            procesados += 1

        conn.commit()
        conn.close()
        flash(f'¡Éxito! Se procesaron {procesados} productos desde el archivo.')

    except Exception as e:
        flash(f'Error al procesar el archivo: {str(e)}')

    return redirect(url_for('admin'))

@app.route('/eliminar_producto/<int:id>')
@role_required('admin')
def eliminar_producto(id):
    conn = obtener_conexion()
    conn.execute('DELETE FROM productos WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    
    flash('Producto eliminado.')
    return redirect(url_for('admin'))

@app.route('/agregar_carrito', methods=['POST'])
def agregar_carrito():
    producto_id = int(request.form['producto_id'])
    cantidad = int(request.form['cantidad'])

    conn = obtener_conexion()
    producto = conn.execute('SELECT * FROM productos WHERE id = ?', (producto_id,)).fetchone()
    conn.close()

    if producto and producto['stock'] >= cantidad:
        subtotal = producto['precio'] * cantidad
        carrito = session.get('carrito', [])
        
        carrito.append({
            'id': producto['id'],
            'nombre': producto['nombre'],
            'precio': producto['precio'],
            'cantidad': cantidad,
            'subtotal': subtotal
        })

        session['carrito'] = carrito

    if session.get('user_role') == 'cajero':
        return redirect(url_for('pos_cajero'))
    return redirect(url_for('index'))

@app.route('/vaciar_carrito')
def vaciar_carrito():
    session['carrito'] = []
    if session.get('user_role') == 'cajero':
        return redirect(url_for('pos_cajero'))
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
