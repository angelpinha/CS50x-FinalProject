from io import BytesIO
from flask import (
    Blueprint,
    render_template,
    request,
    flash,
    redirect,
    session,
    url_for,
    g,
)
from werkzeug.security import check_password_hash, generate_password_hash
import uuid
import pyotp
import qrcode
import base64

from app.db import get_db
from app.helpers import login_required, confirm_2fa_pending

auth = Blueprint("auth", __name__)


@auth.before_app_request
def logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
    else:
        g.user = (
            get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        )


@auth.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        # Counter for missing credentials
        missing_credentials = 0
        fields = {
            "first_name": "first name",
            "last_name": "last name",
            "username": "username",
            "password": "password",
            "password_check": "password confirmation",
        }

        # Makes sure every input has been filled
        for input in fields:
            if not request.form.get(f"{input}"):
                flash(f"Must provide {fields[input]}", "error")
                missing_credentials += 1
                continue

        if missing_credentials != 0:
            return redirect("/register")

        db = get_db()
        credentials = {}
        role = "Inactive"

        # Stores key, value pairs in credentials dictionary according to fields keys
        for key, value in fields.items():
            # Makes sure the password is stored in the form of a hash value
            if fields[key] == "password":
                pwhash = generate_password_hash(request.form.get(f"{key}"))
                checker = check_password_hash(
                    pwhash, request.form.get("password_check")
                )
                if checker:
                    credentials[key] = pwhash
                else:
                    flash("Password and confirmation must be equal!", "error")
                    return redirect("/register")

            else:
                credentials[key] = request.form.get(f"{key}")
        try:
            # This is a random identificator generated by uuid module
            # Proper documentation in:
            # https://docs.python.org/3/library/uuid.html
            UUID = str(uuid.uuid4()).upper()
            db.execute(
                """
                INSERT INTO users 
                    (username, password_hash, uuid, first_name, last_name, role)
                VALUES
                    (?,?,?,?,?,?)
                """,
                (
                    credentials["username"],
                    credentials["password"],
                    UUID,
                    credentials["first_name"],
                    credentials["last_name"],
                    role,
                ),
            )
            db.commit()
        except db.IntegrityError:
            flash("Username is already registered.", "error")
            return redirect("/register")

        success = flash("User Registered!", "success")
        wait = flash("Wait until an admin assigns your role.", "success")
        return render_template("auth/login.html", success=success, wait=wait)

    if request.method == "GET":
        return render_template("auth/register.html")


@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        missing_credentials = 0
        fields = {
            "username": "Username",
            "password": "Password",
        }

        next_url = request.form.get("next")

        for input in fields:
            if not request.form.get(f"{input}"):
                flash(f"Must provide {fields[input]}", "error")
                missing_credentials += 1
                continue

        if missing_credentials != 0:
            return redirect("/login")

        username = request.form.get("username")
        password = request.form.get("password")

        db = get_db()

        rows = db.execute(
            """
            SELECT id, password_hash, totp_key
            FROM users
            WHERE username = (?)
            """,
            (username,),
        ).fetchone()

        # Checks if username is registered in database
        if rows is None:
            flash("Username not registered", "error")
            next_url = request.form.get("next")
            if next_url:
                return redirect(next_url)
            return render_template("auth/login.html")

        auth = check_password_hash(rows["password_hash"], password)

        # Checks if password is valid
        if auth is not True:
            flash("Incorrect password", "error")
            next_url = request.form.get("next")
            if next_url:
                return redirect(next_url)
            return render_template("auth/login.html")

        totp_key = rows["totp_key"]

        if totp_key is not None:
            session.clear()
            session["user_id_pending"] = rows["id"]
            next_url = request.form.get("next")
            if next_url:
                return redirect(url_for("auth.confirm_2fa", next=next_url))
            else:
                return redirect(url_for("auth.confirm_2fa"))

        else:
            session.clear()
            session["user_id"] = rows["id"]
            # Redirects to previously requested URL if exists
            if next_url:
                return redirect(next_url)
            return redirect(url_for("index"))
    else:
        session.clear()
        return render_template("auth/login.html")


@auth.route("/confirm_2fa", methods=["GET", "POST"])
@confirm_2fa_pending
def confirm_2fa():
    if request.method == "POST":
        next_url = request.form.get("next")
        user_id = session["user_id_pending"]

        db = get_db()
        rows = db.execute(
            """
            SELECT id, totp_key
            FROM users
            WHERE id = (?)
            """,
            (user_id,),
        ).fetchone()

        totp_key = rows["totp_key"]

        try:
            user_input_code = int(request.form.get("totp"))
        except ValueError:
            flash("Must provide a valid 6-digit 2FA code", "error")
            if next_url:
                return redirect(url_for("auth.confirm_2fa", next=next_url))
            else:
                return redirect(url_for("auth.confirm_2fa"))

        current_code = int(pyotp.TOTP(totp_key).now())

        if user_input_code == current_code:
            session.clear()
            session["user_id"] = rows["id"]
            if next_url:
                return redirect(next_url)
            # Redirects to previously requested URL if exists
            return redirect(url_for("index"))
        else:
            # Count for wrong attempts
            try:
                session["TRIES"] += 1
            except KeyError:
                session["TRIES"] = 1

            flash("Must provide a valid 6-digit 2FA code", "error")

            if session["TRIES"] >= 2:
                return redirect(url_for("auth.login"))
            if next_url:
                return redirect(url_for("auth.confirm_2fa", next=next_url))
            else:
                return redirect(url_for("auth.confirm_2fa"))
    else:
        return render_template("auth/confirm_2fa.html")


@auth.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth.route("/profile")
@login_required
def profile():
    db = get_db()
    rows = db.execute(
        """
        SELECT username, first_name, last_name, role
        FROM users
        WHERE id = (?)
        """,
        (g.user[0],),
    ).fetchone()

    username = rows["username"]
    first_name = rows["first_name"]
    last_name = rows["last_name"]
    role = rows["role"]

    # Redirects to a previously requested url if exists
    next_url = request.form.get("next")
    if next_url:
        return redirect(next_url)

    return render_template(
        "auth/profile.html",
        username=username,
        first_name=first_name,
        last_name=last_name,
        role=role,
    )


@login_required
def create_totp_qrcode():
    # Get username from database
    db = get_db()
    rows = db.execute(
        """
            SELECT username
            FROM users
            WHERE id = (?)
            """,
        (g.user[0],),
    ).fetchone()

    # ID data inside the TOTP
    USERNAME = rows["username"]
    APP_NAME = "CS50x"
    # Generate a secret key to represent in the QR code
    SECRET_KEY = pyotp.random_base32()

    # Encapsulate the information to store TOTP inside a QR code later on
    time_based_otp = pyotp.totp.TOTP(SECRET_KEY).provisioning_uri(
        name=USERNAME, issuer_name=APP_NAME
    )

    # Generate the QR code image using the "qrcode" library
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=5,
        border=4,
    )
    # Insert data inside the QR code
    qr.add_data(time_based_otp)
    # Generated QR size based on size of data
    qr.make(fit=True)
    # Convert QR code into an image object
    qr_img = qr.make_image(fill_color="black", back_color="white")

    # Convert the "PilImage" object to a byte stream to avoid storing image
    byte_stream = BytesIO()
    qr_img.save(byte_stream, format="PNG")
    # Locate the pointer of the byte stream at the first location
    byte_stream.seek(0)

    # Encode the byte stream as a base64 ascii string
    # 1) byte_stream.getvalue() gets the bytes from the byte stream
    # 2) base64.b64encode() encodes the bytes as base64-encoded string
    # 3) .decode("ascii") converts the byte stream into a regular ascii string
    qr_encoded = base64.b64encode(byte_stream.getvalue()).decode("ascii")

    session["qr_encoded"] = qr_encoded
    session["qr_key"] = SECRET_KEY
    return


@auth.route("/2FA", methods=["GET", "POST"])
@login_required
def two_factor_authentication():
    if request.method == "POST":
        qr_key = session["qr_key"]

        try:
            user_input_code = int(request.form.get("totp"))
        except ValueError:
            flash("Must provide a valid 6-digit 2FA code", "error")
            return redirect("/2FA")

        current_code = int(pyotp.TOTP(qr_key).now())

        if current_code == user_input_code:
            # Initialize the database and update totp_key
            db = get_db()
            db.execute(
                """
                UPDATE users
                SET totp_key = (?)
                WHERE id = (?)
                """,
                (
                    qr_key,
                    g.user[0],
                ),
            )
            db.commit()

            session.pop("qr_encoded", None)
            session.pop("qr_key", None)

            flash("Two factor authentication has been set", "success")
            return redirect("/2FA")

        else:
            flash("Invalid 6-digit code, try again", "error")
            return redirect("/2FA")

    else:
        # Initialize the database and get totp_key from database
        db = get_db()
        rows = db.execute(
            """
                SELECT totp_key
                FROM users
                WHERE id = (?)
                """,
            (session["user_id"],),
        ).fetchone()

        if rows["totp_key"] is None:
            is_totp_set = False

            if "qr_encoded" not in session:
                create_totp_qrcode()
                qr_encoded = session["qr_encoded"]
                qr_key = session["qr_key"]

            else:
                qr_encoded = session["qr_encoded"]
                qr_key = session["qr_key"]

            return render_template(
                "auth/2fa.html", qr_encoded=qr_encoded, is_totp_set=is_totp_set
            )
        else:
            is_totp_set = True
            return render_template("auth/2fa.html", is_totp_set=is_totp_set)


@auth.route("/recovery_key", methods=["GET", "POST"])
@login_required
def recovery_key():
    if request.method == "POST":
        generate = request.form.get("generate").lower()
        confirmation = "generate a new recovery key"
        if generate == confirmation:
            UUID = str(uuid.uuid4()).upper()
            db = get_db()
            db.execute(
                """
                UPDATE users
                SET uuid = (?)
                WHERE id = (?)
                """,
                (
                    UUID,
                    g.user[0],
                ),
            )
            db.commit()
            flash("New recovery key has been generated!", "success")
            flash("Please keep it in a safe place", "success")
            return redirect("/recovery_key")
        else:
            flash("Could not generate a new recovery key!", "error")
            flash("Please fill out the form accordingly", "error")
            return redirect("/recovery_key")
    else:
        db = get_db()
        UUID = db.execute(
            """
            SELECT uuid
            FROM users
            WHERE id = (?)
            """,
            (g.user[0],),
        ).fetchone()[0]
    return render_template("auth/recovery_key.html", uuid=UUID)


@auth.route("/recover", methods=["GET", "POST"])
def recover_account():
    if request.method == "POST":
        missing_credentials = 0

        fields = {
            "username": "Username",
            "recovery": "Recovery key",
        }

        for input in fields:
            if not request.form.get(f"{input}"):
                flash(f"Must provide {fields[input]}", "error")
                missing_credentials += 1
                continue

        if missing_credentials != 0:
            return redirect("/recover")

        input_username = request.form.get("username")
        input_recovery = request.form.get("recovery")

        db = get_db()
        try:
            db_recovery = db.execute(
                """
                SELECT uuid
                FROM users
                WHERE username = (?)
                """,
                (input_username,),
            ).fetchone()[0]
        except TypeError:
            flash("Username / Recovery key pair don't match!", "error")
            flash("Try again with valid input", "error")
            flash("E-R01", "error")
            return redirect("/recover")

        if input_recovery == db_recovery:
            session.clear()
            user_id = db.execute(
                """
                SELECT id
                FROM users
                WHERE username = (?)
                """,
                (input_username,),
            ).fetchone()

            session["recovery_id"] = user_id["id"]

            return redirect("/set_password")
        else:
            flash("Username / Recovery key pair don't match!", "error")
            flash("Try again with valid input", "error")
            flash("E-R02", "error")
            return redirect("/recover")

        return redirect("/recover")
    else:
        return render_template("auth/recover.html")


@auth.route("/set_password", methods=["GET", "POST"])
def set_new_password():
    if request.method == "POST":
        missing_credentials = 0

        fields = {
            "password": "Password",
            "password_check": "Password confirmation",
        }

        for input in fields:
            if not request.form.get(f"{input}"):
                flash(f"Must provide {fields[input]}", "error")
                missing_credentials += 1
                continue

        if missing_credentials != 0:
            return redirect("/set_password")

        pwhash = generate_password_hash(request.form.get("password"))
        checker = check_password_hash(pwhash, request.form.get("password_check"))

        # Checker if either True or False
        if checker:
            recovery_id = session["recovery_id"]
            session.clear()

            UUID = str(uuid.uuid4()).upper()
            db = get_db()
            totp_key = db.execute(
                """
                SELECT totp_key
                FROM users
                WHERE id = (?)
                """,
                (recovery_id,),
            ).fetchone()[0]

            db.execute(
                """
                UPDATE users
                SET password_hash = (?), uuid = (?), totp_key = NULL
                WHERE id = (?)
                """,
                (
                    pwhash,
                    UUID,
                    recovery_id,
                ),
            )
            db.commit()

            if totp_key is None:
                flash("New recovery key has been generated!", "success")
                flash("ALERT: Make sure to backup your new recovery key!", "success")
            else:
                flash("New recovery key has been generated!", "success")
                flash("ALERT: Make sure to backup your new recovery key!", "success")
                flash(
                    "ALERT: Two factor authentication has been deactivated!", "success"
                )

            session["user_id"] = recovery_id
            return redirect("/recovery_key")

        else:
            flash("Password and confirmation must be equal!", "error")
            return redirect("/set_password")

    else:
        try:
            if session["recovery_id"]:
                return render_template("auth/set_password.html")
        except KeyError:
            session.clear()
            flash("Please provide proper credentials", "error")
            return redirect("/recover")
