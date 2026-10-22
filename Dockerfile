# Container image for the RotorDyn Dash web interface.
#
#     docker build -t rotordyn .
#     docker run -p 8050:7860 rotordyn
#
# This is for the EC2 instance. Hugging Face Spaces does *not* use it, because
# Docker Spaces need a paid plan; the free Gradio SDK runs app.py instead. The
# unprivileged user and port 7860 are kept so the image would still work as a
# Docker Space if the account is ever upgraded.

FROM python:3.11-slim

# Non-root user, as Hugging Face Spaces requires.
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

EXPOSE 7860

# --host 0.0.0.0 is required: the container has to accept connections from
# outside its own network namespace.
CMD ["sh", "-c", "python main.py --web ${PORT} --production --host 0.0.0.0"]
