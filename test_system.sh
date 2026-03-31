#!/bin/bash
# System Test Runner for PS-01
# Runs comprehensive system tests

set -e

echo "========================================="
echo "PS-01 System Test Suite"
echo "========================================="
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 not found"
    exit 1
fi

# Check if API server is running
echo "Checking if API server is running..."
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "✓ API server is running"
else
    echo "✗ API server is not running"
    echo ""
    echo "Please start the API server first:"
    echo "  uvicorn src.api.app:app --host 0.0.0.0 --port 8000"
    echo ""
    exit 1
fi

# Set PYTHONPATH
export PYTHONPATH=$(pwd)

# Run system tests
echo ""
python3 scripts/system_test.py

exit $?
