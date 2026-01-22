# Stage 1: Builder stage
FROM continuumio/miniconda3 AS builder

# Install build dependencies
RUN apt-get update && \
    apt-get install -y python3-dev gcc g++ build-essential && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /build

# Copy only the environment file first (for better layer caching)
COPY environment.yml .

# Create the conda environment
RUN conda env create -f environment.yml && \
    conda clean -afy && \
    rm -rf /root/.cache/pip/*

# Stage 2: Runtime stage
FROM continuumio/miniconda3

# Install only runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy the conda environment from builder
COPY --from=builder /opt/conda/envs/hummingbot-api /opt/conda/envs/hummingbot-api

# Set the working directory
WORKDIR /hummingbot-api

# Copy only necessary application files
COPY main.py config.py deps.py ./
COPY models ./models
COPY routers ./routers
COPY services ./services
COPY utils ./utils
COPY database ./database
COPY bots/controllers ./bots/controllers
COPY bots/scripts ./bots/scripts

# Create necessary directories
RUN mkdir -p bots/instances bots/conf bots/credentials bots/data bots/archived

# Expose port
EXPOSE 8000

# Set environment variables to ensure conda env is used
ENV PATH="/opt/conda/envs/hummingbot-api/bin:$PATH"
ENV CONDA_DEFAULT_ENV=hummingbot-api

# Run the application
ENTRYPOINT ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
