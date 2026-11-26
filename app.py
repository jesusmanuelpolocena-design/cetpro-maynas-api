from datetime import datetime
import random
from flask import Flask, jsonify, request
from flask_cors import CORS
import mysql.connector
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
CORS(app)  # Permite que el frontend HTML/JS se comunique con esta API

# Configuración de conexión a MySQL
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',          # Cambia por tu usuario de MySQL
    'password': 'COMPUTO2',          # Cambia por tu contraseña de MySQL
    'database': 'cetpro_maynas'
}


def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)


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

        cursor.execute("SELECT * FROM carreras WHERE estado = 'publicada' ORDER BY id_carrera ASC")
        carreras = cursor.fetchall()

        # Agregar turnos y vacantes disponibles a cada carrera (RF-09)
        for carrera in carreras:
            cursor.execute(
                "SELECT id_turno, nombre_turno, vacantes FROM secciones_turnos WHERE id_carrera = %s ORDER BY id_turno ASC",
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

    # Validación de datos obligatorios (Regla de negocio 3)
    if not all(datos.get(c) for c in campos_req):
        return jsonify({'error': 'Todos los campos son obligatorios'}), 400

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Encriptar contraseña con hash seguro (RNF-03)
        password_hash = generate_password_hash(datos['password'])

        cursor.execute(
            "INSERT INTO usuarios (nombre_usuario, correo, hash_contrasena, rol) VALUES (%s, %s, %s, 'estudiante')",
            (str(datos['dni']).strip(), str(datos['correo']).strip(), password_hash)
        )
        id_usuario = cursor.lastrowid

        cursor.execute(
            "INSERT INTO estudiantes (id_usuario, documento, nombres, apellidos, telefono) VALUES (%s, %s, %s, %s, %s)",
            (
                id_usuario,
                str(datos['dni']).strip(),
                str(datos['nombres']).strip(),
                str(datos['apellidos']).strip(),
                str(datos['telefono']).strip()
            )
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
    usuario = (datos.get('usuario') or '').strip()  # DNI o correo
    password = datos.get('password') or ''

    if not usuario or not password:
        return jsonify({'error': 'Debe ingresar usuario/correo y contraseña.'}), 400

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT u.id_usuario, u.hash_contrasena, u.rol, u.estado,
                   e.id_estudiante, e.nombres, e.apellidos, e.documento
            FROM usuarios u
            LEFT JOIN estudiantes e ON u.id_usuario = e.id_usuario
            WHERE u.nombre_usuario = %s OR u.correo = %s
            LIMIT 1
        """, (usuario, usuario))
        user = cursor.fetchone()

        if user and check_password_hash(user['hash_contrasena'], password):
            if user['estado'] != 'activo':
                return jsonify({'error': 'El usuario se encuentra inactivo.'}), 403
            del user['hash_contrasena']  # Nunca devolver el hash al cliente
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
        return jsonify({'error': 'Debe seleccionar carrera y turno.'}), 400

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Verificar que el turno pertenezca a la carrera y tenga vacantes disponibles (Regla de negocio 2)
        cursor.execute(
            "SELECT vacantes FROM secciones_turnos WHERE id_turno = %s AND id_carrera = %s",
            (id_turno, id_carrera)
        )
        turno = cursor.fetchone()
        if not turno:
            return jsonify({'error': 'El turno seleccionado no corresponde a la carrera elegida.'}), 400
        if turno['vacantes'] <= 0:
            return jsonify({'error': 'Lo sentimos, ya no quedan vacantes en este turno.'}), 400

        # Obtener periodo activo
        cursor.execute("SELECT id_periodo FROM periodos WHERE estado = 'activo' ORDER BY id_periodo DESC LIMIT 1")
        periodo = cursor.fetchone()
        if not periodo:
            return jsonify({'error': 'No hay un periodo académico activo en este momento.'}), 400

        # Generar código único de seguimiento y registrar matrícula (RF-12)
        codigo = None
        for _ in range(5):
            candidato = f"MAT-{datetime.now().year}-{random.randint(1000, 9999)}"
            try:
                cursor.execute("""
                    INSERT INTO matriculas (codigo_seguimiento, id_estudiante, id_carrera, id_turno, id_periodo, estado)
                    VALUES (%s, %s, %s, %s, %s, 'Enviada')
                """, (candidato, id_estudiante, id_carrera, id_turno, periodo['id_periodo']))
                codigo = candidato
                break
            except mysql.connector.IntegrityError:
                continue

        if not codigo:
            conn.rollback()
            return jsonify({'error': 'No se pudo generar un código de seguimiento único, intente de nuevo.'}), 500

        # Descontar 1 vacante temporalmente
        cursor.execute(
            "UPDATE secciones_turnos SET vacantes = vacantes - 1 WHERE id_turno = %s AND vacantes > 0",
            (id_turno,)
        )

        conn.commit()
        return jsonify({
            'mensaje': 'Solicitud de automatrícula enviada con éxito.',
            'codigo_seguimiento': codigo,
            'estado': 'Enviada'
        }), 201

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
# 5. CONSULTAR MIS SOLICITUDES (RF-13)
# ---------------------------------------------------------
@app.route('/api/matriculas/<int:id_estudiante>', methods=['GET'])
def mis_matriculas(id_estudiante):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT m.codigo_seguimiento, m.estado, m.observacion, m.fecha_solicitud,
                   c.nombre AS carrera, t.nombre_turno AS turno, p.nombre AS periodo
            FROM matriculas m
            JOIN carreras c ON m.id_carrera = c.id_carrera
            JOIN secciones_turnos t ON m.id_turno = t.id_turno
            JOIN periodos p ON m.id_periodo = p.id_periodo
            WHERE m.id_estudiante = %s
            ORDER BY m.fecha_solicitud DESC
        """, (id_estudiante,))

        lista = cursor.fetchall()
        return jsonify(lista), 200

    except mysql.connector.Error as err:
        return jsonify({'error': f'Error de base de datos: {err}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


if __name__ == '__main__':
    app.run(debug=True, port=5000)