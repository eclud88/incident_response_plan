from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import uuid
import json
import platform
import shutil
import subprocess
import os, tempfile
from docx import Document
from docx2pdf import convert
from copy import deepcopy
from docxtpl import DocxTemplate, InlineImage
from docx.shared import Inches


app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy()
db.init_app(app)


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg'}


def load_incident_classes():
    with open('incidents.json') as f:
        raw_data = json.load(f)
    return {
        entry["class"]: [t["type"] for t in entry["types"]]
        for entry in raw_data
    }


def load_incident_steps():
    with open('incident_steps.json') as f:
        return json.load(f)


def generate_uuid():
    return str(uuid.uuid4())


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Incident(db.Model):
    __tablename__ = 'incidents'
    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    incident_id = db.Column(db.String, unique=True, nullable=False)
    incident_class = db.Column(db.String)
    incident_type = db.Column(db.String)
    start_datetime = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String, default='in_progress')
    percent_complete = db.Column(db.Integer, default=0)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    user = db.relationship('User', backref='incidents')

    steps = db.relationship('IncidentStep', backref='incident', cascade="all, delete-orphan", lazy=True)
    evidences = db.relationship('Evidence', backref='incident', cascade="all, delete-orphan", lazy=True)
    lessons_learned = db.relationship('LessonsLearned', backref='incident', uselist=False, cascade="all, delete-orphan")
    sub_steps = db.relationship('SubStep', backref='incident', cascade="all, delete-orphan", lazy=True)

class IncidentStep(db.Model):
    __tablename__ = 'incident_steps'
    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    incident_id = db.Column(db.String, db.ForeignKey('incidents.incident_id'), nullable=False)
    step_id = db.Column(db.Integer, nullable=False)
    step_description = db.Column(db.Text, nullable=False)
    substeps = db.relationship('SubStep', backref='step', cascade="all, delete-orphan", lazy=True)

class SubStep(db.Model):
    __tablename__ = 'sub_steps'
    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    incident_id = db.Column(db.String, db.ForeignKey('incidents.incident_id'), nullable=False)
    step_id = db.Column(db.String, db.ForeignKey('incident_steps.step_id'), nullable=False)
    sub_step_description = db.Column(db.Text, nullable=False)

class Evidence(db.Model):
    __tablename__ = 'evidence'
    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    incident_id = db.Column(db.String, db.ForeignKey('incidents.incident_id'), nullable=False)
    step_id = db.Column(db.String, db.ForeignKey('incident_steps.incident_id'), nullable=False)
    attachment_name = db.Column(db.String)
    upload_status = db.Column(db.String)
    description = db.Column(db.Text)

class LessonsLearned(db.Model):
    __tablename__ = 'lessons_learned'
    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    incident_id = db.Column(db.String, db.ForeignKey('incidents.incident_id'), nullable=False, unique=True)
    improvements = db.Column(db.Text)
    observations = db.Column(db.Text)
    end_datetime = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()

        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return redirect(url_for('register'))

        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/', methods=['GET', 'POST'])
def index():
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = request.form['username']
        pwd = request.form['password']

        db_user = User.query.filter_by(username=user).first()

        if db_user and db_user.check_password(pwd):
            session['user'] = db_user.username
            flash('Successfully logged in.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.', 'danger')

    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        flash('Precisas de iniciar sessão.', 'warning')
        return redirect(url_for('login'))

    current_user = User.query.filter_by(username=session['user']).first()
    if not current_user:
        flash('Utilizador inválido.', 'danger')
        return redirect(url_for('login'))

    user_id = current_user.id

    in_progress = Incident.query.filter(
        Incident.status != 'completed',
        Incident.user_id == user_id
    ).all()

    completed = LessonsLearned.query \
        .join(Incident, LessonsLearned.incident_id == Incident.incident_id) \
        .filter(
            Incident.user_id == user_id,
            LessonsLearned.end_datetime.isnot(None)
        ).all()

    return render_template('dashboard.html', in_progress=in_progress, completed=completed)

@app.route('/new_incident', methods=['GET', 'POST'])
def new_incident():
    if 'user' not in session:
        flash('Precisas de iniciar sessão.', 'warning')
        return redirect(url_for('login'))

    current_user = User.query.filter_by(username=session['user']).first()
    if not current_user:
        flash('Utilizador inválido.', 'danger')
        return redirect(url_for('login'))

    incident_class_data = load_incident_classes()

    if request.method == 'POST':
        selected_class = request.form['incident_class']
        selected_type = request.form['incident_type']

        last_incident = db.session.query(Incident).order_by(
            db.cast(Incident.incident_id, db.Integer).desc()
        ).first()
        new_id = int(last_incident.incident_id) + 1 if last_incident else 1

        new_incident = Incident(
            incident_id=str(new_id),
            incident_class=selected_class,
            incident_type=selected_type,
            user_id=current_user.id
        )
        db.session.add(new_incident)
        db.session.commit()

        steps = get_steps_for_class_and_type(selected_class, selected_type)

        for i, s in enumerate(steps, start=1):
            step = IncidentStep(
                incident_id=str(new_id),
                step_id=i,
                step_description=s['step']
            )
            db.session.add(step)
            db.session.flush()

            for sub in s.get('sub_steps', []):
                substep = SubStep(
                    incident_id=str(new_id),
                    step_id=step.id,
                    sub_step_description=sub
                )
                db.session.add(substep)

        db.session.commit()
        return redirect(url_for('incident_step', incident_id=new_id, step_id=1))

    return render_template('new_incident.html', incident_class=incident_class_data)

def get_steps_for_class_and_type(incident_class, incident_type):
    all_data = load_incident_steps()
    for c in all_data:
        if c['class'] == incident_class:
            for t in c['types']:
                if t['type'] == incident_type:
                    return t['steps']
    return []


@app.route('/incident/<int:incident_id>/step/<int:step_id>', methods=['GET', 'POST'])
def incident_step(incident_id, step_id):
    # Pega o incidente
    incident = Incident.query.filter_by(incident_id=str(incident_id)).first()
    if not incident:
        flash('Incidente não encontrado.', 'danger')
        return redirect(url_for('dashboard'))

    # Todos os passos para esse incidente
    steps_db = IncidentStep.query.filter_by(incident_id=str(incident_id)).order_by(IncidentStep.step_id).all()
    total_steps = len(steps_db)

    if step_id < 1 or step_id > total_steps:
        flash('Passo inválido.', 'danger')
        return redirect(url_for('dashboard'))

    # Pega o passo atual
    step = steps_db[step_id - 1]

    # Substeps (agora usando step.id, que é UUID)
    substeps_db = SubStep.query.filter_by(step_id=step.id, incident_id=str(incident_id)).all()
    substeps_list = [s.sub_step_description for s in substeps_db]

    # Evidence existente
    evidence = Evidence.query.filter_by(incident_id=str(incident_id), step_id=step.id).first()
    evidence_text = evidence.description if evidence else ''
    evidence_attachment = evidence.attachment_name if evidence and evidence.attachment_name else ''

    # Substeps previamente marcados
    completed_substeps = []
    if evidence and evidence.upload_status and evidence.upload_status.startswith("json:"):
        try:
            completed_substeps = json.loads(evidence.upload_status[5:])
        except Exception as e:
            print(f"Erro ao carregar substeps do JSON: {e}")

    completed_steps = Evidence.query.filter(
        Evidence.incident_id == str(incident_id),
        Evidence.attachment_name.isnot(None)
    ).count()

    if request.method == 'POST':
        action = request.form.get('action')
        evidence_text = request.form.get('evidence', '').strip()
        checked_substeps = request.form.getlist('substeps')
        file = request.files.get('file')

        if not evidence:
            evidence = Evidence(incident_id=str(incident_id), step_id=step.id)

        evidence.description = evidence_text
        evidence.upload_status = "json:" + json.dumps(checked_substeps)

        if file and file.filename:
            filename = secure_filename(file.filename)
            upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(incident_id), str(step_id))
            os.makedirs(upload_dir, exist_ok=True)
            file.save(os.path.join(upload_dir, filename))
            evidence.attachment_name = filename

        db.session.add(evidence)
        db.session.commit()

        # Recalcular progresso
        has_attachment = bool(evidence.attachment_name)
        is_step_complete = bool(evidence_text and has_attachment and checked_substeps)

        completed_steps = Evidence.query.filter(
            Evidence.incident_id == str(incident_id),
            Evidence.attachment_name.isnot(None)
        ).count()

        incident.percent_complete = int((completed_steps / total_steps) * 50)
        db.session.add(incident)
        db.session.commit()

        if action == 'back':
            return redirect(url_for('incident_step', incident_id=incident_id, step_id=step_id - 1))

        elif action == 'next':
            if is_step_complete:
                return redirect(url_for('incident_step', incident_id=incident_id, step_id=step_id + 1))
            else:
                flash('Você precisa preencher todos os campos, anexar e marcar substeps.', 'danger')

        elif action == 'lessons_learned':
            return redirect(url_for('lessons_learned', incident_id=incident_id))

        elif action == 'dashboard':
            return redirect(url_for('dashboard', incident_id=incident_id))

    return render_template(
        'incident_step.html',
        incident=incident,
        step_id=step_id,
        step_title=step.step_description,
        substeps=substeps_list,
        completed_substeps=completed_substeps,
        is_first_step=(step_id == 1),
        is_last_step=(step_id == total_steps),
        evidence_text=evidence_text,
        evidence_attachment=evidence_attachment,
        total_steps=total_steps,
        completed_steps=completed_steps
    )


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        # Verifica se o arquivo está na requisição
        if 'file' not in request.files:
            return "Nenhum arquivo enviado", 400
        file = request.files['file']

        # Se o usuário não selecionar arquivo
        if file.filename == '':
            return "Nenhum arquivo selecionado", 400

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            return f"Arquivo {filename} salvo com sucesso!"

    return


@app.route('/lessons_learned/<incident_id>', methods=['GET', 'POST'])
def lessons_learned(incident_id):
    incident = Incident.query.filter_by(incident_id=incident_id).first_or_404()
    incident_class = incident.incident_class
    incident_type = incident.incident_type


    if not incident.lessons_learned:
        lessons = LessonsLearned(incident_id=incident.incident_id)
        db.session.add(lessons)
        db.session.commit()
    else:
        lessons = incident.lessons_learned

    if request.method == 'POST':
        improvements = request.form.get('improvements', '').strip()
        observations = request.form.get('observations', '').strip()
        action = request.form.get('action')

        # Atualiza os dados do LessonsLearned
        lessons.improvements = improvements
        lessons.observations = observations

        # Atualiza o progresso do incidente
        percent = 50  # Assume que os steps já valem 50%

        if improvements:
            percent += 25
        if observations:
            percent += 25

        incident.percent_complete = percent

        if percent == 100:
            incident.status = 'completed'
            lessons.end_datetime = datetime.now()

        if action == 'generate_report':
            return redirect(url_for('generate_report', incident_id=incident.incident_id))
        if action == 'save':
            db.session.commit()
            session.modified = True
            flash("Salvo com sucesso!", "success")

    return render_template('lessons_learned.html', incident_class=incident_class, incident_type=incident_type, incident=incident, lessons=lessons)


def generate_docx_with_data(data, template_path):
    temp_dir = tempfile.mkdtemp()
    output_docx = os.path.join(temp_dir, 'incident_report.docx')

    doc = DocxTemplate(template_path)

    # Deep copy para evitar modificar o original
    data_copy = deepcopy(data)

    for step in data_copy.get('steps', []):
        images = []
        for path in step.get('attachments', []):
            full_path = os.path.join(os.getcwd(), path)
            if os.path.exists(full_path):
                images.append(InlineImage(doc, full_path, width=Inches(3)))
        step['attachments'] = images

    doc.render(data_copy)
    doc.save(output_docx)

    return convert_to_pdf_with_libreoffice(output_docx)


def convert_to_pdf_with_libreoffice(docx_path):
    output_dir = os.path.dirname(docx_path)

    os_name = platform.system()
    if os_name == "Windows":
        libreoffice_path = r"C:\Program Files\LibreOffice\program\soffice.exe"
    elif os_name == "Darwin":
        libreoffice_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    else:
        libreoffice_path = "libreoffice"

    subprocess.run([
        libreoffice_path, "--headless", "--convert-to", "pdf", "--outdir", output_dir, docx_path
    ], check=True)

    return os.path.splitext(docx_path)[0] + ".pdf"


@app.route('/generate_report/<incident_id>')
def generate_report(incident_id):
    incident = Incident.query.filter_by(incident_id=incident_id).first_or_404()
    steps = IncidentStep.query.filter_by(incident_id=incident_id).all()
    substeps = SubStep.query.filter_by(incident_id=incident_id).all()
    evidences = Evidence.query.filter_by(incident_id=incident_id).all()
    lessons_learned = LessonsLearned.query.filter_by(incident_id=incident_id).first()

    # Map substeps and evidences by step_id
    substep_map = {}
    for sub in substeps:
        substep_map.setdefault(str(sub.step_id), []).append(sub.sub_step_description)

    evidence_map = {}
    for ev in evidences:
        evidence_map.setdefault(str(ev.id), []).append(ev.description)

    # Build structured steps
    structured_steps = []
    for idx, step in enumerate(steps):
        index = str(idx + 1)
        upload_path = os.path.join("uploads", incident_id, index)
        attached_files = []

        if os.path.exists(upload_path):
            for file in os.listdir(upload_path):
                attached_files.append(os.path.join("uploads", incident_id, index, file))

        structured_steps.append({
            'step': step.step_description,
            'substeps': substep_map.get(str(step.id), []),
            'evidences': evidence_map.get(str(step.id), []),
            'attachments': attached_files
        })

    data_for_template = {
        'current_date': datetime.now().strftime('%d/%m/%Y'),
        'incident_id': incident.incident_id,
        'selected_class': incident.incident_class or '',
        'selected_type': incident.incident_type or '',
        'start_time': incident.start_datetime.strftime('%d/%m/%Y %H:%M'),
        'end_time': lessons_learned.end_datetime.strftime('%d/%m/%Y %H:%M') if lessons_learned.end_datetime else '',
        'steps': structured_steps,
        'attachments': [file for step in structured_steps for file in step['attachments']],
        'substeps': [sub for step in structured_steps for sub in step['substeps']],
        'evidences': [ev for step in structured_steps for ev in step['evidences']],
        'improvements': getattr(lessons_learned, 'improvements', ''),
        'observations': getattr(lessons_learned, 'observations', '')
    }

    template_path = os.path.join(app.root_path, 'word_template', 'incidentreport_template.docx')

    try:
        temp_pdf_path = generate_docx_with_data(data_for_template, template_path)

        # Save permanently
        final_dir = os.path.join(app.root_path, f'reports/{incident_id}')
        os.makedirs(final_dir, exist_ok=True)
        final_pdf_path = os.path.join(final_dir, f'incident_{incident_id}.pdf')
        shutil.copy(temp_pdf_path, final_pdf_path)

    except Exception as e:
        print(f"Error generating report: {e}", "danger")
        return redirect(url_for('dashboard'))

    filename = f"incident_report_{incident_id}.pdf"
    return send_file(final_pdf_path, as_attachment=True, download_name=filename)


@app.route('/delete_incident/<int:incident_id>', methods=['POST'])
def delete_incident(incident_id):
    try:
        # Buscar o incidente com status 'completed'
        incident = Incident.query.filter_by(incident_id=incident_id, status='completed').first()

        if not incident:
            return jsonify({'status': 'error', 'message': 'Incidente não encontrado ou não está concluído'}), 404

        db.session.delete(incident)
        db.session.commit()
        session.modified = True

        return jsonify({'status': 'success'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(debug=True)
