#!/bin/bash
# End-to-End Demo Runner for PS-01
# Runs the complete Rajesh 4-session journey

set -e

echo "========================================="
echo "PS-01: The Loan Officer Who Never Forgets"
echo "End-to-End Demo Runner"
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

# Run the demo
echo ""
echo "Starting Rajesh's 4-session journey..."
echo ""

python3 scripts/run_rajesh_demo.py

exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo ""
    echo "========================================="
    echo "Demo completed successfully!"
    echo "========================================="
else
    echo ""
    echo "========================================="
    echo "Demo failed. Check errors above."
    echo "========================================="
fi

exit $exit_code
