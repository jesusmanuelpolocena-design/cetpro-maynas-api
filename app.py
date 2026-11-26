from datetime import datetime
import os
import random
from flask import Flask, jsonify, request
from flask_cors import CORS
import mysql.connector
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)

# Habilita CORS para permitir peticiones desde el frontend (Netlify y entorno local)
CORS(app, origins=[
    "https://cetpro-maynas.netlify.app",
    "http://localhost:5500",
    "http://127.0.0.1:5500"
])

# Configuración de conexión a MySQL (usa variables de entorno si existen)
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 3306)),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', 'COMPUTO27'),
    'database': os.environ.get('DB_NAME', 'cetpro_maynas'),
    'ssl_disabled': False,
}


def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)


# ---------------------------------------------------------
# 0. COMPROBACIÓN DE ESTADO DEL SERVIDOR
# ---------------------------------------------------------
@app.route('/', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'estado': 'ok', 'mensaje': 'API funcionando correctamente'}), 200


# ---------------------------------------------------------
# 1. ENDPOINT PÚBLICO: Obtener catálogo de carreras (RF-02, RF-03)
# ---------------------------------------------------------
@app.route('/api/carreras', methods=['GET'])
def obtener_carreras():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT * FROM carreras WHERE estado = 'publicada' ORDER BY id_carrera ASC"
        )
        carreras = cursor.fetchall()

        for carrera in carreras:
            cursor.execute(
                """
                SELECT id_turno, nombre_turno, vacantes
                FROM secciones_turnos
                WHERE id_carrera = %s
                ORDER BY id_turno ASC
                """,
                (carrera['id_carrera'],)
            )
            carrera['turnos'] = cursor.fetchall()

        return jsonify(carreras), 200

    except mysql.connector.Error as err:
        return jsonify({'error': f'Error de conexión o consulta en MySQL: {err}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# ---------------------------------------------------------
# 2. REGISTRO DE ESTUDIANTE (RF-06, RNF-03)
# ---------------------------------------------------------
@app.route('/api/registro', methods=['POST'])
def registrar_estudiante():
    datos = request.get_json(silent=True) or {}
    campos_req = ['dni', 'nombres', 'apellidos', 'correo', 'telefono', 'password']

    if not all(str(datos.get(c) or '').strip() for c in campos_req):
        return jsonify({'error': 'Todos los campos son obligatorios'}), 400

    dni = str(datos['dni']).strip()
    correo = str(datos['correo']).strip()
    nombres = str(datos['nombres']).strip()
    apellidos = str(datos['apellidos']).strip()
    telefono = str(datos['telefono']).strip()
    password = str(datos['password'])

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        password_hash = generate_password_hash(password)

        cursor.execute(
            """
            INSERT INTO usuarios (nombre_usuario, correo, hash_contrasena, rol, estado)
            VALUES (%s, %s, %s, 'estudiante', 'activo')
            """,
            (dni, correo, password_hash)
        )
        id_usuario = cursor.lastrowid

        cursor.execute(
            """
            INSERT INTO estudiantes (id_usuario, documento, nombres, apellidos, telefono)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (id_usuario, dni, nombres, apellidos, telefono)
        )

        conn.commit()
        return jsonify({'mensaje': 'Cuenta creada correctamente. Ahora puedes iniciar sesión.'}), 201

    except mysql.connector.IntegrityError:
        if conn:
            conn.rollback()
        return jsonify({'error': 'El DNI o correo electrónico ya se encuentra registrado.'}), 409
    except mysql.connector.Error as err:
        if conn:
            conn.rollback()
        return jsonify({'error': f'Error de base de datos: {err}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# ---------------------------------------------------------
# 3. INICIO DE SESIÓN (RF-07, RNF-01)
# ---------------------------------------------------------
@app.route('/api/login', methods=['POST'])
def login():
    datos = request.get_json(silent=True) or {}
    usuario = (datos.get('usuario') or '').strip()
    password = datos.get('password') or ''

    if not usuario or not password:
        return jsonify({'error': 'Debe ingresar usuario/correo y contraseña.'}), 400

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT u.id_usuario, u.nombre_usuario, u.correo, u.hash_contrasena,
                   u.rol, u.estado, e.id_estudiante, e.nombres, e.apellidos, e.documento
            FROM usuarios u
            LEFT JOIN estudiantes e ON u.id_usuario = e.id_usuario
            WHERE u.nombre_usuario = %s OR u.correo = %s
            LIMIT 1
            """,
            (usuario, usuario)
        )
        user = cursor.fetchone()

        if user and check_password_hash(user['hash_contrasena'], password):
            if user['estado'] != 'activo':
                return jsonify({'error': 'El usuario se encuentra inactivo.'}), 403

            del user['hash_contrasena']
            del user['estado']
            return jsonify({'mensaje': 'Acceso exitoso', 'usuario': user}), 200

        return jsonify({'error': 'Credenciales incorrectas'}), 401

    except mysql.connector.Error as err:
        return jsonify({'error': f'Error de base de datos: {err}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# ---------------------------------------------------------
# 4. ENVIAR SOLICITUD DE AUTOMATRÍCULA (RF-10, RF-12)
# ---------------------------------------------------------
@app.route('/api/matriculas', methods=['POST'])
def crear_matricula():
    datos = request.get_json(silent=True) or {}
    id_estudiante = datos.get('id_estudiante')
    id_carrera = datos.get('id_carrera')
    id_turno = datos.get('id_turno')

    if not all([id_estudiante, id_carrera, id_turno]):
        return jsonify({'error': 'Debe iniciar sesión como estudiante y seleccionar carrera y turno.'}), 400

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        # Verificar que el estudiante exista
        cursor.execute("SELECT id_estudiante FROM estudiantes WHERE id_estudiante = %s", (id_estudiante,))
        if not cursor.fetchone():
            conn.rollback()
            return jsonify({'error': 'El perfil de estudiante no es válido.'}), 404

        # Bloquear la fila del turno para evitar condiciones de carrera
        cursor.execute(
            """
            SELECT vacantes
            FROM secciones_turnos
            WHERE id_turno = %s AND id_carrera = %s
            FOR UPDATE
            """,
            (id_turno, id_carrera)
        )
        turno = cursor.fetchone()
        if not turno:
            conn.rollback()
            return jsonify({'error': 'El turno seleccionado no corresponde a la carrera elegida.'}), 400
        if turno['vacantes'] <= 0:
            conn.rollback()
            return jsonify({'error': 'Lo sentimos, ya no quedan vacantes en este turno.'}), 400

        # Obtener periodo activo
        cursor.execute(
            """
            SELECT id_periodo
            FROM periodos
            WHERE estado = 'activo'
            ORDER BY id_periodo DESC
            LIMIT 1
            """
        )
        periodo = cursor.fetchone()
        if not periodo:
            conn.rollback()
            return jsonify({'error': 'No hay un periodo académico activo en este momento.'}), 400

        # Evitar matrícula duplicada en la misma carrera y periodo
        cursor.execute(
            """
            SELECT id_matricula
            FROM matriculas
            WHERE id_estudiante = %s
              AND id_carrera = %s
              AND id_periodo = %s
              AND estado NOT IN ('Anulada', 'Rechazada')
            LIMIT 1
            """,
            (id_estudiante, id_carrera, periodo['id_periodo'])
        )
        if cursor.fetchone():
            conn.rollback()
            return jsonify({'error': 'Ya tienes una solicitud o matrícula activa para esta carrera en el periodo actual.'}), 409

        # Generar código único de seguimiento
        codigo = None
        for _ in range(10):
            candidato = f"MAT-{datetime.now().year}-{random.randint(1000, 9999)}"
            try:
                cursor.execute(
                    """
                    INSERT INTO matriculas (codigo_seguimiento, id_estudiante, id_carrera, id_turno, id_periodo, estado)
                    VALUES (%s, %s, %s, %s, %s, 'Enviada')
                    """,
                    (candidato, id_estudiante, id_carrera, id_turno, periodo['id_periodo'])
                )
                codigo = candidato
                break
            except mysql.connector.IntegrityError:
                continue

        if not codigo:
            conn.rollback()
            return jsonify({'error': 'No se pudo generar un código de seguimiento único, intente de nuevo.'}), 500

        # Descontar 1 vacante de forma segura
        cursor.execute(
            """
            UPDATE secciones_turnos
            SET vacantes = vacantes - 1
            WHERE id_turno = %s AND id_carrera = %s AND vacantes > 0
            """,
            (id_turno, id_carrera)
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({'error': 'No se pudo reservar la vacante; intente nuevamente.'}), 409

        conn.commit()
        return jsonify({
            'mensaje': 'Solicitud de automatrícula enviada con éxito.',
            'codigo_seguimiento': codigo,
            'estado': 'Enviada'
        }), 201

    except mysql.connector.Error as err:
        if conn:
            conn.rollback()
        return
