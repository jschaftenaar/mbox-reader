FROM python:3.13-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY templates/ templates/

# Create directory for mbox file mount
RUN mkdir -p /data

# Set environment variables with defaults
ENV MBOX_PATH=/data/emails.mbox
ENV PORT=5000

# Expose the port
EXPOSE 5000

# Run the application
CMD ["python", "app.py"]

# Made with Bob
