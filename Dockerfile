FROM python:3.11-slim

# Set working directory inside container
WORKDIR /app

# Copy everything into the container
COPY . .

# Install required Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Cloud Run listens on port 8080 by default
EXPOSE 8080

# Start FastAPI using your main.py entrypoint
CMD ["python", "main.py"]
