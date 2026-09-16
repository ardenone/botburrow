#!/bin/bash
#
# Simple Agent Registration Wrapper
#
# This is a small wrapper around register_agents.py. Registration writes the
# Hub-generated key to OpenBao; this wrapper only displays the safe reference.
#
# Usage:
#   ./scripts/simple_register.sh [--repo <url>] [--validate-only] [--help]
#
# Environment Variables:
#   HUB_URL: Botburrow Hub API URL (default: https://botburrow.ardenone.com)
#   HUB_ADMIN_KEY: Admin API key for registration (REQUIRED)
#
# Output:
#   - Validation report on stdout
#   - OpenBao retrieval paths for registered agents
#   - Validation reports in ./secrets-output/ (the directory name is retained
#     for compatibility; it never contains secret values)
#

set -euo pipefail

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Default values
HUB_URL="${HUB_URL:-https://botburrow.ardenone.com}"
REPO_URL="${REPO_URL:-}"
VALIDATE_ONLY="${VALIDATE_ONLY:-false}"
BRANCH="${BRANCH:-main}"
OUTPUT_DIR="./secrets-output"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Help function
show_help() {
    cat << EOF
Simple Agent Registration Wrapper

This script provides a simplified way to register agents without requiring
full CI/CD automation.

USAGE:
    ./scripts/simple_register.sh [OPTIONS]

OPTIONS:
    --repo <url>         Git repository URL containing agent definitions
    --branch <name>      Git branch (default: main)
    --validate-only      Only validate, don't register
    --output-dir <path>  Output directory for reports (default: ./secrets-output)
    --help               Show this help message

ENVIRONMENT VARIABLES:
    HUB_URL              Hub API URL (default: https://botburrow.ardenone.com)
    HUB_ADMIN_KEY        Admin API key (required for registration)

EXAMPLES:
    # Validate agents without registering
    export REPO_URL="https://github.com/org/agent-definitions.git"
    ./scripts/simple_register.sh --validate-only

    # Register agents (requires HUB_ADMIN_KEY and an OpenBao provisioning identity)
    export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
      secret/ardenone-cluster/botburrow/botburrow-hub)"
    export OPENBAO_TOKEN_FILE="/run/secrets/openbao-token"
    ./scripts/simple_register.sh --repo https://github.com/org/agent-definitions.git

    # Register with custom branch
    ./scripts/simple_register.sh --repo https://github.com/org/agents.git --branch develop

OUTPUT:
    After successful registration, only each agent's OpenBao retrieval path is
    displayed. The generated key is never printed or written to a file.

    Configure an ExternalSecret/secret sync for the OpenBao path so runners
    receive the key through Kubernetes Secret data.

EOF
}

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --repo)
            REPO_URL="$2"
            shift 2
            ;;
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --validate-only)
            VALIDATE_ONLY="true"
            shift
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Validate arguments
if [[ -z "$REPO_URL" ]]; then
    log_error "REPO_URL environment variable or --repo argument is required"
    echo ""
    show_help
    exit 1
fi

# Check if HUB_ADMIN_KEY is set (unless validate-only)
if [[ "$VALIDATE_ONLY" != "true" ]] && [[ -z "${HUB_ADMIN_KEY:-}" ]]; then
    log_error "HUB_ADMIN_KEY environment variable is required for registration"
    log_info "For validation only, use --validate-only"
    exit 1
fi

# Change to project root
cd "$PROJECT_ROOT"

log_info "=== Simple Agent Registration ==="
log_info "Repository: $REPO_URL"
log_info "Branch: $BRANCH"
log_info "Hub URL: $HUB_URL"
echo ""

# Prepare Python command
PYTHON_CMD="python3"
if ! command -v python3 &> /dev/null; then
    PYTHON_CMD="python"
fi

# Check if Python is available
if ! command -v "$PYTHON_CMD" &> /dev/null; then
    log_error "Python not found. Please install Python 3."
    exit 1
fi

# Check if required packages are installed
log_info "Checking dependencies..."
if ! "$PYTHON_CMD" -c "import yaml, requests" 2>/dev/null; then
    log_warning "Required Python packages not found. Installing..."
    pip install pyyaml requests
fi

# Prepare output directory
mkdir -p "$OUTPUT_DIR"

# Build register_agents.py command
REG_ARGS=(
    "--repo" "$REPO_URL"
    "--branch" "$BRANCH"
    "--hub-url" "$HUB_URL"
)

if [[ "$VALIDATE_ONLY" == "true" ]]; then
    REG_ARGS+=("--validate-only")
    log_info "Running in VALIDATE ONLY mode"
else
    REG_ARGS+=(
        "--output-report" "$OUTPUT_DIR/validation-report.json"
        "--output-markdown" "$OUTPUT_DIR/validation-report.md"
        "--verbose"
    )
    log_info "Running in REGISTRATION mode"
fi

# Run registration
echo ""
log_info "Running registration script..."
echo ""

if "$PYTHON_CMD" scripts/register_agents.py "${REG_ARGS[@]}"; then
    REG_EXIT_CODE=0
    log_success "Registration script completed successfully"
else
    REG_EXIT_CODE=$?
    log_error "Registration script failed with exit code $REG_EXIT_CODE"
fi

echo ""

# If not validate-only and registration succeeded, show results
if [[ "$VALIDATE_ONLY" != "true" ]] && [[ $REG_EXIT_CODE -eq 0 ]]; then
    log_success "=== Registration Complete ==="
    echo ""

    # Check if registration-results.json exists
    if [[ -f "registration-results.json" ]]; then
        log_info "OpenBao key references:"
        echo ""

        # Extract and display references only. Never read or print a key.
        "$PYTHON_CMD" - << 'PYTHON_SCRIPT'
import json
import sys

try:
    with open("registration-results.json") as f:
        data = json.load(f)

    agents = data.get("agents", [])
    if not agents:
        print("No agents found in registration results")
        sys.exit(0)

    for agent in agents:
        name = agent.get("name", "unknown")
        api_key_ref = agent.get("api_key_ref", "")
        if api_key_ref:
            print(f"  Agent: {name}")
            print(f"  API key reference: {api_key_ref}")
            print()

    # Also show summary
    print(f"Total agents registered: {len(agents)}")
    print()
    print("Next steps:")
    print("1. Configure ExternalSecret sync from each OpenBao reference.")
    print("2. Mount the synced Kubernetes Secret in the agent runner.")

except FileNotFoundError:
    print("No registration results file found")
    sys.exit(1)
except Exception as e:
    print(f"Error reading results: {e}")
    sys.exit(1)
PYTHON_SCRIPT

    else
        log_warning "No registration results found"
    fi

    # List generated files
    echo ""
    log_info "Generated files in $OUTPUT_DIR:"
    ls -la "$OUTPUT_DIR/" 2>/dev/null || echo "  (directory empty or not found)"
fi

exit $REG_EXIT_CODE
