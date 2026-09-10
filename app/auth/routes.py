"""Auth routes: /login, /signup, /logout — session auth via Flask-Login."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required, login_user, logout_user

from app.extensions import db
from .models import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        form = request.form
        mode = form.get("mode", "login")

        if mode == "register":
            name = (form.get("name") or "").strip()
            email = (form.get("email") or "").strip().lower()
            password = form.get("password") or ""
            role = form.get("role") or "learner"
            if role not in ("learner", "instructor"):
                role = "learner"

            if not name or not email or not password:
                flash("Name, email and password are all required.", "error")
                return render_template("login.html", mode="register"), 400
            if len(password) < 6:
                flash("Password must be at least 6 characters.", "error")
                return render_template("login.html", mode="register"), 400
            if User.query.filter_by(email=email).first():
                flash("An account with that email already exists.", "error")
                return render_template("login.html", mode="register"), 409

            user = User(name=name, email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            login_user(user)
            return redirect(url_for("dashboard.home"))

        # mode == "login"
        email = (form.get("email") or "").strip().lower()
        password = form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Incorrect email or password.", "error")
            return render_template("login.html", mode="login"), 401

        login_user(user)
        next_url = request.args.get("next")
        return redirect(next_url or url_for("dashboard.home"))

    return render_template("login.html", mode=request.args.get("mode", "login"))


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
