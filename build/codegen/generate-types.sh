#!/bin/bash
# scripts/generate-types.sh
#
# Generates TypeScript interfaces from Pydantic models in the backend.

# Ensure we're in the project root
cd "$(dirname "$0")/../.."

# Set PYTHONPATH to include the project root so backend modules can be imported
export PYTHONPATH=.

# Run the generator script using the virtual environment's python
./.venv/bin/python build/codegen/generate_types.py
