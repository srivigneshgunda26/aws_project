"""
Run this script ONCE to create the required DynamoDB tables.
Usage:
    python create_tables.py

Make sure your .env has correct AWS credentials before running.
"""

import boto3
import os
from dotenv import load_dotenv

load_dotenv()

region   = os.getenv('AWS_DEFAULT_REGION')
key      = os.getenv('AWS_ACCESS_KEY_ID', '')
secret   = os.getenv('AWS_SECRET_ACCESS_KEY', '')

print(f"\n[INFO] Region  : {region}")
print(f"[INFO] Key     : {key[:8] + '...' if key else '(not set)'}")
print(f"[INFO] Secret  : {'set (OK)' if secret else 'NOT SET'}")
print()

if not region or not key or not secret:
    print("[ERROR] Missing AWS credentials in .env — please fill them in before running.")
    exit(1)

if key == 'YOUR_AWS_ACCESS_KEY_ID' or secret == 'YOUR_AWS_SECRET_ACCESS_KEY':
    print("[ERROR] .env still has placeholder credentials. Replace them with your real AWS keys.")
    exit(1)

dynamodb = boto3.client(
    'dynamodb',
    region_name=region,
    aws_access_key_id=key,
    aws_secret_access_key=secret
)

# ── Get existing tables ──
try:
    existing = dynamodb.list_tables()['TableNames']
    print(f"[AWS] Connected! Existing tables: {existing}")
except Exception as e:
    print(f"[ERROR] AWS connection failed: {e}")
    print("\nCommon causes:")
    print("  - Wrong AWS_ACCESS_KEY or AWS_SECRET_KEY")
    print("  - Wrong AWS_REGION (check your DynamoDB region in AWS Console)")
    print("  - No internet / firewall blocking AWS")
    exit(1)

# ── Table definitions ──
tables = [
    {
        'TableName': 'Admins',
        'KeySchema': [{'AttributeName': 'email', 'KeyType': 'HASH'}],
        'AttributeDefinitions': [{'AttributeName': 'email', 'AttributeType': 'S'}],
        'BillingMode': 'PAY_PER_REQUEST'
    },
    {
        'TableName': 'Users',
        'KeySchema': [{'AttributeName': 'email', 'KeyType': 'HASH'}],
        'AttributeDefinitions': [{'AttributeName': 'email', 'AttributeType': 'S'}],
        'BillingMode': 'PAY_PER_REQUEST'
    },
    {
        'TableName': 'Tests',
        'KeySchema': [{'AttributeName': 'test_id', 'KeyType': 'HASH'}],
        'AttributeDefinitions': [{'AttributeName': 'test_id', 'AttributeType': 'S'}],
        'BillingMode': 'PAY_PER_REQUEST'
    },
    {
        'TableName': 'Results',
        'KeySchema': [{'AttributeName': 'result_id', 'KeyType': 'HASH'}],
        'AttributeDefinitions': [{'AttributeName': 'result_id', 'AttributeType': 'S'}],
        'BillingMode': 'PAY_PER_REQUEST'
    }
]

# ── Create tables ──
for table_def in tables:
    name = table_def['TableName']
    if name in existing:
        print(f"[SKIP] Table '{name}' already exists.")
        continue
    try:
        dynamodb.create_table(**table_def)
        print(f"[OK]   Table '{name}' created successfully.")
    except Exception as e:
        print(f"[ERROR] Could not create '{name}': {e}")

print("\n✅ Done! You can now run: python app.py")
