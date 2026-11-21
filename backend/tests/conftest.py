import os

os.environ.setdefault('AUTH_KEY', 'test-suite-key')
os.environ.setdefault('DB_HOST', os.getenv('DB_HOST', '127.0.0.1'))
os.environ.setdefault('DB_PORT', os.getenv('DB_PORT', '17790'))
os.environ.setdefault('DB_PASSWORD', os.getenv('DB_PASSWORD', 'x'))
os.environ.setdefault('DB_NAME', os.getenv('DB_NAME', 'workflow'))
os.environ.setdefault('SEED_DEMO', 'false')
