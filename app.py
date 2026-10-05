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

# Detección de la base de datos permanente en Neon
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
        cursor.execute('''
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
        ''')

        # 2. Proveedores en Postgres
        cursor.execute('''
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
        ''')

        # 3. Ventas en Postgres
        cursor.execute('''
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
        ''')

        # 4. Detalle Ventas en Postgres
        cursor.execute('''
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
        ''')

        # 5. Usuarios en Postgres
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'cajero'
            )
        ''')
    else:
        # 1. Productos en SQLite
        cursor.execute('''
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
        ''')

        # 2. Proveedores en SQLite
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

        # Parche de seguridad para tablas existentes en SQLite
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
        cursor.execute('''
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
        ''')

        # 4. Detalle Ventas en SQLite
        cursor.execute('''
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
        ''')

        # 5. Usuarios en SQLite
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'cajero'
            )
        ''')

    # Sembrado de usuarios iniciales
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
    if any(k in desc for k in ['gorro', 'guante', 'desechable', 'tapa boca', 'mas¿A cuál de los códigos o proyectos te refieres exactamente? (Por ejemplo: el script de Flask para el inventario/ventas, la lectura/exportación de Excel, o alguna plantilla en HTML). 

Indícame cuál es la función específica o el error que te estaba dando y te lo dejo listo, limpio y probado para copiar y pegar directo.
