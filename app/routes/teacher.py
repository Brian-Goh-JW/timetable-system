from flask import Blueprint

teacher_bp = Blueprint('teacher', __name__, url_prefix='/teacher')


@teacher_bp.route('/dashboard')
def dashboard():
    return 'Teacher dashboard coming soon'
