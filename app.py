import os
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'clave_secreta_super_segura'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inventario.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Carpeta para archivos temporales subidos
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)

# ----------------- MODELO DE DATOS ----------------- #
class Producto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(50), unique=True, nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    precio = db.Column(db.Float, nullable=False, default=0.0)
    stock = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return f'<Producto {self.nombre}>'

# Crear tablas al iniciar
with app.app_context():
    db.create_all()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ----------------- RUTAS DE LA APLICACIÓN ----------------- #

@app.route('/')
def index():
    productos = Producto.query.all()
    return render_template('index.html', productos=productos)

@app.route('/agregar', methods=['POST'])
def agregar_producto():
    codigo = request.form.get('codigo')
    nombre = request.form.get('nombre')
    precio = float(request.form.get('precio', 0))
    stock = int(request.form.get('stock', 0))

    if Producto.query.filter_by(codigo=codigo).first():
        flash('El código ya existe en el inventario.', 'warning')
        return redirect(url_for('index'))

    nuevo = Producto(codigo=codigo, nombre=nombre, precio=precio, stock=stock)
    db.session.add(nuevo)
    db.session.commit()
    flash('Producto agregado correctamente.', 'success')
    return redirect(url_for('index'))

@app.route('/importar_excel', methods=['GET', 'POST'])
def importar_excel():
    if request.method == 'POST':
        if 'archivo' not in request.files:
            flash('No se seleccionó ningún archivo.', 'danger')
            return redirect(request.url)

        file = request.files['archivo']

        if file.filename == '':
            flash('El nombre del archivo está vacío.', 'danger')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            try:
                # Lectura flexible para Excel (.xlsx, .xls) y CSV
                if filename.endswith('.csv'):
                    df = pd.read_csv(filepath)
                else:
                    df = pd.read_excel(filepath)

                # Estandarizar nombres de columnas a minúsculas sin espacios
                df.columns = [str(col).strip().lower() for col in df.columns]

                # Verificar columnas requeridas
                columnas_necesarias = {'codigo', 'nombre', 'precio', 'stock'}
                if not columnas_necesarias.issubset(set(df.columns)):
                    flash('El archivo debe tener las columnas: codigo, nombre, precio, stock', 'danger')
                    return redirect(request.url)

                registros_nuevos = 0
                registros_actualizados = 0

                for _, row in df.iterrows():
                    codigo = str(row['codigo']).strip()
                    nombre = str(row['nombre']).strip()
                    
                    # Limpieza básica de números
                    try:
                        precio = float(row['precio'])
                    except (ValueError, TypeError):
                        precio = 0.0

                    try:
                        stock = int(row['stock'])
                    except (ValueError, TypeError):
                        stock = 0

                    # Buscar si el producto ya existe para actualizarlo o crearlo
                    producto = Producto.query.filter_by(codigo=codigo).first()
                    if producto:
                        producto.nombre = nombre
                        producto.precio = precio
                        producto.stock = stock
                        registros_actualizados += 1
                    else:
                        nuevo = Producto(codigo=codigo, nombre=nombre, precio=precio, stock=stock)
                        db.session.add(nuevo)
                        registros_nuevos += 1

                db.session.commit()
                flash(f'Importación exitosa: {registros_nuevos} nuevos, {registros_actualizados} actualizados.', 'success')

            except Exception as e:
                db.session.rollback()
                flash(f'Error al procesar el archivo: {str(e)}', 'danger')
            finally:
                # Borrar archivo temporal subido
                if os.path.exists(filepath):
                    os.remove(filepath)

            return redirect(url_for('index'))

        flash('Formato no permitido. Solo archivos .xlsx, .xls o .csv.', 'warning')
        return redirect(request.url)

    return render_template('importar.html')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
