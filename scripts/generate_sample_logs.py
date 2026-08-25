"""
Generate sample log files for testing the log agent.

Creates four log files describing a cascading failure:
  1. deploy.log: records a deploy that changed a config value
  2. database.log: shows database refusing connections due to new config
  3. service.log: shows service timing out waiting for DB
  4. gateway.log: shows 502 errors because service is not responding

The root cause (config change in deploy) is only correlatable by looking at all logs.
"""

import os
import random
from datetime import datetime, timedelta


def generate_deploy_log(path: str, num_lines: int = 500):
    """Generate deploy.log with a config change."""
    start_time = datetime(2026, 8, 24, 10, 0, 0)
    with open(path, 'w') as f:
        f.write(f"# Deploy log\n")
        for i in range(num_lines):
            timestamp = start_time + timedelta(seconds=i*2)
            # Most lines are normal deploys
            if i == 100:  # The problematic deploy
                f.write(f"{timestamp.isoformat()} INFO Deploying version 2.3.0\n")
                f.write(f"{timestamp.isoformat()} INFO Config change: DB_POOL_SIZE increased from 10 to 100\n")
            else:
                f.write(f"{timestamp.isoformat()} INFO Routine deploy check\n")
            # Add some other events
            if i % 50 == 0 and i != 100:
                f.write(f"{timestamp.isoformat()} INFO Deploy completed successfully\n")


def generate_database_log(path: str, num_lines: int = 800):
    """Generate database.log showing connection refusals."""
    start_time = datetime(2026, 8, 24, 10, 0, 0)
    with open(path, 'w') as f:
        f.write(f"# Database log\n")
        # Normal operation first
        for i in range(num_lines):
            timestamp = start_time + timedelta(seconds=i*1.5)
            if i < 150:  # Before the deploy effect
                f.write(f"{timestamp.isoformat()} INFO New connection accepted\n")
                if i % 20 == 0:
                    f.write(f"{timestamp.isoformat()} INFO Query processed: SELECT * FROM users\n")
            else:
                # After the deploy, the pool size increase causes too many connections
                # Database has max_connections=50, so pool size 100 -> overload
                if i % 10 == 0:
                    f.write(f"{timestamp.isoformat()} WARN Connection pool near max (45/50)\n")
                if i % 15 == 0:
                    f.write(f"{timestamp.isoformat()} ERROR Failed to add connection: too many connections\n")
                else:
                    f.write(f"{timestamp.isoformat()} INFO Connection released\n")


def generate_service_log(path: str, num_lines: int = 700):
    """Generate service.log showing timeouts."""
    start_time = datetime(2026, 8, 24, 10, 0, 0)
    with open(path, 'w') as f:
        f.write(f"# Service log\n")
        for i in range(num_lines):
            timestamp = start_time + timedelta(seconds=i*1.2)
            if i < 200:  # Before DB issues
                f.write(f"{timestamp.isoformat()} INFO Request processed in 45ms\n")
                if i % 30 == 0:
                    f.write(f"{timestamp.isoformat()} INFO Service health check passed\n")
            else:
                # After DB connection issues, service times out waiting for DB
                if i % 10 == 0:
                    f.write(f"{timestamp.isoformat()} WARN DB query timeout after 5s\n")
                if i % 25 == 0:
                    f.write(f"{timestamp.isoformat()} ERROR Failed to retrieve user data: timeout\n")
                else:
                    f.write(f"{timestamp.isoformat()} INFO Processing request\n")


def generate_gateway_log(path: str, num_lines: int = 600):
    """Generate gateway.log showing 502 errors."""
    start_time = datetime(2026, 8, 24, 10, 0, 0)
    with open(path, 'w') as f:
        f.write(f"# Gateway log\n")
        for i in range(num_lines):
            timestamp = start_time + timedelta(seconds=i*1.8)
            if i < 100:  # Before service issues
                f.write(f"{timestamp.isoformat()} INFO 200 GET /api/home\n")
                if i % 40 == 0:
                    f.write(f"{timestamp.isoformat()} INFO 200 GET /api/health\n")
            else:
                # Service is timing out, gateway gets 502
                if i % 8 == 0:
                    f.write(f"{timestamp.isoformat()} ERROR 502 Bad Gateway: upstream service timeout\n")
                if i % 20 == 0:
                    f.write(f"{timestamp.isoformat()} WARN Upstream service slow to respond\n")
                else:
                    f.write(f"{timestamp.isoformat()} INFO 200 GET /api/home\n")


def main():
    """Generate sample logs in the logs directory."""
    logs_dir = os.getenv("LOGS_DIR", "./logs")
    os.makedirs(logs_dir, exist_ok=True)

    # Fixed seed for reproducibility
    random.seed(42)

    print(f"Generating sample logs in {logs_dir}...")
    generate_deploy_log(os.path.join(logs_dir, "deploy.log"))
    generate_database_log(os.path.join(logs_dir, "database.log"))
    generate_service_log(os.path.join(logs_dir, "service.log"))
    generate_gateway_log(os.path.join(logs_dir, "gateway.log"))

    # Print summary
    for fname in ["deploy.log", "database.log", "service.log", "gateway.log"]:
        path = os.path.join(logs_dir, fname)
        size = os.path.getsize(path)
        lines = sum(1 for _ in open(path))
        print(f"  {fname}: {lines} lines, {size} bytes")

    print("Done.")


if __name__ == "__main__":
    main()