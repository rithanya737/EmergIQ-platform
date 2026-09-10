from app import create_app

app = create_app()

if __name__ == "__main__":
    # threaded=True lets the dev server handle requests concurrently — without
    # it, one slow request (an Ollama call, a PDF export) blocks every other
    # user's request, including login, until it finishes.
    app.run(debug=True, threaded=True)
