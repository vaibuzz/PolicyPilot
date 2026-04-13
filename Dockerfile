FROM python:3.11-slim

# Install system dependencies required for PyTorch and Docling vision models
RUN apt-get update && apt-get install -y \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces requires a non-root user
RUN useradd -m -u 1000 user
USER user

# Set home and path variables
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app

# Copy the repository into the container
COPY --chown=user . $HOME/app

# Install all standard requirements and explicitly install the heavy Docling AI library
RUN pip install --no-cache-dir -r backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements-docling.txt

# Hugging Face Spaces automatically routes traffic to port 7860 by default
EXPOSE 7860

# We need to run the app from inside the backend directory so Python imports work natively
WORKDIR $HOME/app/backend

# Boot the FastAPI server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
