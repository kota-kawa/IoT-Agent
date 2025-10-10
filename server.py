from __future__ import annotations

from flask import Flask, send_from_directory

app = Flask(__name__, static_folder=".", static_url_path="")


@app.route("/")
def index() -> "flask.wrappers.Response":
    """Serve the main application page."""
    return send_from_directory(app.static_folder, "index.html")


@app.route("/<path:filename>")
def static_files(filename: str) -> "flask.wrappers.Response":
    """Serve static assets such as JavaScript and CSS."""
    return send_from_directory(app.static_folder, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5006)
