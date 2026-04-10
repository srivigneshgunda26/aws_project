from flask import Flask, render_template, request, redirect, session, flash
import boto3, os, uuid, datetime, random
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from collections import defaultdict

load_dotenv(override=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "cloud-test-platform-secret-2024")

# ─────────────────────────────────────────────
#  AWS SETUP
#  Uses standard boto3 env vars: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
#  AWS_DEFAULT_REGION — loaded from .env via load_dotenv() above.
# ─────────────────────────────────────────────
_key    = os.getenv('AWS_ACCESS_KEY_ID', '')
_secret = os.getenv('AWS_SECRET_ACCESS_KEY', '')
_region = os.getenv('AWS_DEFAULT_REGION', 'ap-south-1')

try:
    # Only pass explicit credentials when both are provided
    _kwargs = {'region_name': _region}
    if _key and _secret:
        _kwargs['aws_access_key_id']     = _key
        _kwargs['aws_secret_access_key'] = _secret

    dynamodb = boto3.resource('dynamodb', **_kwargs)
    sns      = boto3.client('sns', **_kwargs)

    users_table   = dynamodb.Table('Users')
    admins_table  = dynamodb.Table('Admins')
    tests_table   = dynamodb.Table('Tests')
    results_table = dynamodb.Table('Results')
    AWS_READY = True
    print(f"[AWS] Setup OK — region={_region}, key={_key[:8]}..." if _key else "[AWS] Setup OK — using default credential chain")
except Exception as e:
    print(f"[AWS] Setup error: {e}")
    AWS_READY = False

# In-memory OTP store  {email: otp}
otp_store = {}


# ─────────────────────────────────────────────
#  DB HELPERS  (safe wrappers around DynamoDB)
# ─────────────────────────────────────────────
def db_get_user(email):
    try:
        return users_table.get_item(Key={'email': email})
    except Exception as e:
        print(f"[DB] get_user error: {e}")
        return {}

def db_put_user(item):
    try:
        users_table.put_item(Item=item)
        return True
    except Exception as e:
        print(f"[DB] put_user error: {e}")
        return False

def db_get_admin(email):
    try:
        return admins_table.get_item(Key={'email': email})
    except Exception as e:
        print(f"[DB] get_admin error: {e}")
        return {}

def db_put_admin(item):
    try:
        admins_table.put_item(Item=item)
        return True
    except Exception as e:
        print(f"[DB] put_admin error: {e}")
        return False

def db_update_password(email, hashed_pw, is_admin=False):
    try:
        table = admins_table if is_admin else users_table
        table.update_item(
            Key={'email': email},
            UpdateExpression="SET password = :p",
            ExpressionAttributeValues={':p': hashed_pw}
        )
        return True
    except Exception as e:
        print(f"[DB] update_password error: {e}")
        return False

def db_put_test(item):
    try:
        tests_table.put_item(Item=item)
        return True
    except Exception as e:
        print(f"[DB] put_test error: {e}")
        return False

def db_get_tests():
    try:
        return tests_table.scan().get('Items', [])
    except Exception as e:
        print(f"[DB] get_tests error: {e}")
        return []

def db_put_result(item):
    try:
        results_table.put_item(Item=item)
        return True
    except Exception as e:
        print(f"[DB] put_result error: {e}")
        return False

def db_get_results():
    try:
        return results_table.scan().get('Items', [])
    except Exception as e:
        print(f"[DB] get_results error: {e}")
        return []

def sns_publish(message, subject="Cloud Test Platform"):
    try:
        sns.publish(
            TopicArn=os.getenv('SNS_TOPIC_ARN'),
            Message=message,
            Subject=subject
        )
        return True
    except Exception as e:
        print(f"[SNS] publish error: {e}")
        return False


# ─────────────────────────────────────────────
#  DECORATORS
# ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            flash("Please login first.")
            return redirect('/')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            flash("Admin access required.")
            return redirect('/dashboard')
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────
#  HEALTH / DIAGNOSTIC ROUTE
# ─────────────────────────────────────────────
@app.route('/health')
def health():
    """Diagnostic page — accessible without login to help debug AWS setup."""
    region = os.getenv('AWS_DEFAULT_REGION', '')
    key    = os.getenv('AWS_ACCESS_KEY_ID', '')
    secret = os.getenv('AWS_SECRET_ACCESS_KEY', '')

    sns_arn = os.getenv('SNS_TOPIC_ARN', '')
    checks = {
        'AWS_DEFAULT_REGION set':    bool(region),
        'AWS_ACCESS_KEY_ID set':     bool(key),
        'AWS_SECRET_ACCESS_KEY set': bool(secret),
        'SNS_TOPIC_ARN set':         bool(sns_arn),
    }

    db_status  = 'not tested'
    db_tables  = []
    db_error   = None

    if checks['AWS_DEFAULT_REGION set'] and checks['AWS_ACCESS_KEY_ID set'] and checks['AWS_SECRET_ACCESS_KEY set']:
        try:
            client = boto3.client(
                'dynamodb',
                region_name=region,
                aws_access_key_id=key,
                aws_secret_access_key=secret
            )
            db_tables = client.list_tables().get('TableNames', [])
            db_status = 'connected'
        except Exception as e:
            db_status = 'error'
            db_error  = str(e)

    required_tables = ['Admins', 'Users', 'Tests', 'Results']
    table_ok = {t: t in db_tables for t in required_tables}

    # Mask credentials for display
    def mask(v):
        if not v or len(v) < 8:
            return '(not set)'
        return v[:4] + '****' + v[-4:]

    return render_template('health.html',
        checks=checks,
        db_status=db_status,
        db_error=db_error,
        db_tables=db_tables,
        table_ok=table_ok,
        region=region,
        key_masked=mask(key),
        secret_masked=mask(secret)
    )

# ─────────────────────────────────────────────
#  ERROR HANDLER
# ─────────────────────────────────────────────
@app.errorhandler(500)
def internal_error(e):
    return render_template('error.html', error=str(e)), 500

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', error="Page not found (404)"), 404


# ─────────────────────────────────────────────
#  ADMIN SETUP  (create first admin account)
# ─────────────────────────────────────────────
@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """First-time admin account creation. Protected by ADMIN_SETUP_KEY in .env."""
    if 'user' in session:
        return redirect('/dashboard')

    setup_key = os.getenv('ADMIN_SETUP_KEY', 'admin123')

    if request.method == 'POST':
        provided_key = request.form.get('setup_key', '').strip()
        name         = request.form.get('name', '').strip()
        email        = request.form.get('email', '').strip().lower()
        password     = request.form.get('password', '')

        if provided_key != setup_key:
            flash("Invalid setup key!")
            return render_template('setup.html')

        if not name or not email or not password:
            flash("All fields are required!")
            return render_template('setup.html')

        if len(password) < 6:
            flash("Password must be at least 6 characters.")
            return render_template('setup.html')

        # Check if admin already exists in Admins table
        res = db_get_admin(email)
        if 'Item' in res:
            flash(f"Admin account for {email} already exists! Please login.")
            return redirect('/')

        # Create new admin in Admins table
        success = db_put_admin({
            'email':    email,
            'name':     name,
            'password': generate_password_hash(password),
        })

        if success:
            flash(f"✅ Admin account created for {email}! Please login.")
            return redirect('/')
        else:
            flash("Failed to create admin. Check AWS credentials and ensure Admins table exists.")

    return render_template('setup.html')


# ─────────────────────────────────────────────
#  REGISTER  (students)
# ─────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user' in session:
        return redirect('/dashboard')

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        name     = request.form.get('name', '').strip()
        password = request.form.get('password', '')

        if not email or not name or not password:
            flash("All fields are required!")
            return render_template('register.html')

        if len(password) < 6:
            flash("Password must be at least 6 characters.")
            return render_template('register.html')

        res = db_get_user(email)
        if res is None or 'Item' not in res and 'ResponseMetadata' not in res:
            flash("Database connection error. Please check AWS credentials in .env")
            return render_template('register.html')

        if 'Item' in res:
            flash("An account with this email already exists!")
            return render_template('register.html')

        success = db_put_user({
            'email':    email,
            'name':     name,
            'password': generate_password_hash(password),
            'role':     'student'
        })

        if not success:
            flash("Registration failed. Please check your AWS credentials in .env and try again.")
            return render_template('register.html')

        flash("Account created successfully! Please login.")
        return redirect('/')

    return render_template('register.html')


# ─────────────────────────────────────────────
#  LOGIN
# ─────────────────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect('/dashboard')

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            flash("Email and password are required!")
            return render_template('login.html')

        # ── Check Admins table first ──
        admin_res = db_get_admin(email)
        if 'Item' in admin_res:
            if check_password_hash(admin_res['Item']['password'], password):
                session['user'] = email
                session['name'] = admin_res['Item'].get('name', email)
                session['role'] = 'admin'
                return redirect('/dashboard')
            else:
                flash("Invalid email or password!")
                return render_template('login.html')

        # ── Then check Users (students) table ──
        user_res = db_get_user(email)
        if not user_res:
            flash("Database connection error. Please check AWS credentials in .env")
            return render_template('login.html')

        if 'Item' in user_res and check_password_hash(user_res['Item']['password'], password):
            session['user'] = email
            session['name'] = user_res['Item'].get('name', email)
            session['role'] = 'student'
            return redirect('/dashboard')

        flash("Invalid email or password!")

    return render_template('login.html')



# ─────────────────────────────────────────────
#  FORGOT PASSWORD  (OTP via SNS)
# ─────────────────────────────────────────────
@app.route('/forgot', methods=['GET', 'POST'])
def forgot():
    if request.method == 'POST':
        action = request.form.get('action')

        # Step 1: Send OTP
        if action == 'send_otp':
            email = request.form.get('email', '').strip().lower()
            if not email:
                flash("Please enter your email.")
                return render_template('forgot.html', step='1')

            res = db_get_user(email)
            if 'Item' not in res:
                flash("No account found with that email!")
                return render_template('forgot.html', step='1')

            otp = str(random.randint(100000, 999999))
            otp_store[email] = otp

            sent = sns_publish(
                message=f"Your Cloud Test Platform password reset OTP is: {otp}\nThis OTP expires in 10 minutes.",
                subject="Password Reset OTP"
            )
            if sent:
                flash(f"OTP sent to {email}. Check your inbox.")
            else:
                flash(f"[DEV] SNS not configured. Your OTP is: {otp}")

            return render_template('forgot.html', step='2', email=email)

        # Step 2: Verify OTP + reset password
        if action == 'reset_password':
            email        = request.form.get('email', '').strip().lower()
            otp          = request.form.get('otp', '').strip()
            new_password = request.form.get('new_password', '')

            if not new_password or len(new_password) < 6:
                flash("Password must be at least 6 characters.")
                return render_template('forgot.html', step='2', email=email)

            if otp_store.get(email) == otp:
                if db_update_password(email, generate_password_hash(new_password)):
                    otp_store.pop(email, None)
                    flash("Password reset successful! Please login.")
                    return redirect('/')
                else:
                    flash("Failed to update password. Check AWS credentials.")
                    return render_template('forgot.html', step='2', email=email)
            else:
                flash("Invalid OTP. Please try again.")
                return render_template('forgot.html', step='2', email=email)

    return render_template('forgot.html', step='1')


# ─────────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    questions    = db_get_tests()
    all_results  = db_get_results()
    user_results = [r for r in all_results if r.get('user') == session['user']]

    q_count    = len(questions)
    attempts   = len(user_results)
    best_score = max((int(r.get('score', 0)) for r in user_results), default=0)
    best_total = max((int(r.get('total', 0)) for r in user_results), default=max(q_count, 1))

    return render_template(
        'dashboard.html',
        name=session.get('name'),
        role=session.get('role'),
        q_count=q_count,
        attempts=attempts,
        best_score=best_score,
        best_total=best_total
    )


# ─────────────────────────────────────────────
#  ADMIN – Upload Test + View All Results
# ─────────────────────────────────────────────
@app.route('/admin', methods=['GET', 'POST'])
@login_required
@admin_required
def admin():
    if request.method == 'POST':
        questions = request.form.getlist('question[]')
        options1  = request.form.getlist('option1[]')
        options2  = request.form.getlist('option2[]')
        options3  = request.form.getlist('option3[]')
        options4  = request.form.getlist('option4[]')
        answers   = request.form.getlist('answer[]')
        uploaded  = 0

        for i in range(len(questions)):
            q = questions[i].strip() if i < len(questions) else ''
            if not q:
                continue
            success = db_put_test({
                'test_id':  str(uuid.uuid4()),
                'question': q,
                'option1':  options1[i] if i < len(options1) else '',
                'option2':  options2[i] if i < len(options2) else '',
                'option3':  options3[i] if i < len(options3) else '',
                'option4':  options4[i] if i < len(options4) else '',
                'answer':   answers[i]  if i < len(answers)  else ''
            })
            if success:
                uploaded += 1

        if uploaded:
            sns_publish(
                message=f"A new test with {uploaded} question(s) has been uploaded on Cloud Test Platform!",
                subject="New Test Available"
            )
            flash(f"✅ {uploaded} question(s) uploaded successfully!")
        else:
            flash("⚠️ No questions were uploaded. Check AWS credentials.")

    all_results     = sorted(db_get_results(), key=lambda x: x.get('date', ''), reverse=True)
    total_questions = len(db_get_tests())

    return render_template('admin.html', all_results=all_results, total_questions=total_questions)


# ─────────────────────────────────────────────
#  TEST
# ─────────────────────────────────────────────
@app.route('/test', methods=['GET', 'POST'])
@login_required
def test():
    questions = db_get_tests()

    if not questions:
        flash("No questions available yet. Ask your admin to upload a test.")
        return redirect('/dashboard')

    if request.method == 'POST':
        score = 0
        for q in questions:
            if request.form.get(q['test_id']) == q['answer']:
                score += 1

        result_saved = db_put_result({
            'result_id': str(uuid.uuid4()),
            'user':      session['user'],
            'name':      session.get('name', session['user']),
            'score':     score,
            'total':     len(questions),
            'date':      datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        })

        if not result_saved:
            flash("Your score could not be saved — check AWS credentials.")

        return redirect(f'/results?new=1&score={score}&total={len(questions)}')

    timer = int(os.getenv('TEST_TIMER_SECONDS', '600'))
    return render_template('test.html', questions=questions, timer=timer)


# ─────────────────────────────────────────────
#  RESULTS  +  LEADERBOARD
# ─────────────────────────────────────────────
@app.route('/results')
@login_required
def results():
    data = db_get_results()

    if session.get('role') == 'admin':
        my_results = sorted(data, key=lambda x: x.get('date', ''), reverse=True)
    else:
        my_results = sorted(
            [r for r in data if r.get('user') == session['user']],
            key=lambda x: x.get('date', ''),
            reverse=True
        )

    # Leaderboard — best score per user
    lb = defaultdict(lambda: {'name': '', 'best': 0, 'total': 1})
    for r in data:
        u = r.get('user', '')
        if not u:
            continue
        lb[u]['name']  = r.get('name', u)
        lb[u]['best']  = max(lb[u]['best'], int(r.get('score', 0)))
        lb[u]['total'] = max(lb[u]['total'], int(r.get('total', 1)))

    leaderboard = sorted(lb.values(), key=lambda x: x['best'] / x['total'], reverse=True)[:10]

    return render_template(
        'results.html',
        results=my_results,
        leaderboard=leaderboard,
        new_score=request.args.get('score'),
        new_total=request.args.get('total'),
        is_new=bool(request.args.get('new'))
    )


# ─────────────────────────────────────────────
#  LOGOUT
# ─────────────────────────────────────────────
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')


# ─────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=os.getenv('DEBUG', 'True') == 'True')