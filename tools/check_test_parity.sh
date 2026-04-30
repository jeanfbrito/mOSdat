#!/bin/bash
# Test Parity Checker
# Detects SEMANTIC drift between test-framework and electron-linux-testing test pairs
# by comparing test scenarios (display server setup, app checks, environment variables)

set -uo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Repository root
REPO_ROOT="/home/jean/projects/linux-testing"
TEST_FRAMEWORK_DIR="$REPO_ROOT/test-framework/shared/tests"
ELECTRON_LINUX_DIR="$REPO_ROOT/electron-linux-testing/scripts"

declare -A SCENARIO_MARKERS=(
    ["xvfb"]="Xvfb|setup_xvfb|DISPLAY=:99"
    ["weston"]="weston|setup_weston|WAYLAND_DISPLAY"
    ["package_install"]="dpkg|snap install|AppImage"
    ["app_launch"]="run_app|APP_PATH|timeout"
    ["environment_check"]="XDG_SESSION_TYPE|DISPLAY|WAYLAND_DISPLAY"
)

# Extract semantic markers from a test file
extract_scenarios() {
    local file="$1"
    local scenarios=""

    if [ ! -f "$file" ]; then
        echo "FILE_NOT_FOUND"
        return 1
    fi

    # Check for Xvfb setup
    if grep -qE "(setup_xvfb|Xvfb|DISPLAY=:99)" "$file"; then
        scenarios="${scenarios}xvfb "
    fi

    # Check for Weston setup
    if grep -qE "(setup_weston|weston|WAYLAND_DISPLAY)" "$file"; then
        scenarios="${scenarios}weston "
    fi

    # Check for package installation (deb/snap/AppImage)
    if grep -qE "(dpkg|snap install|AppImage)" "$file"; then
        scenarios="${scenarios}package_install "
    fi

    # Check for app launch/run
    if grep -qE "(run_app|APP_PATH|timeout)" "$file"; then
        scenarios="${scenarios}app_launch "
    fi

    # Check for environment variable configuration
    if grep -qE "(XDG_SESSION_TYPE|export DISPLAY|export WAYLAND_DISPLAY)" "$file"; then
        scenarios="${scenarios}environment_check "
    fi

    echo "$scenarios" | xargs
}

# Compare two files and report drift
compare_files() {
    local test_name="$1"
    local framework_file="$2"
    local electron_file="$3"

    local framework_scenarios=$(extract_scenarios "$framework_file" || echo "FILE_NOT_FOUND")
    local electron_scenarios=$(extract_scenarios "$electron_file" || echo "FILE_NOT_FOUND")

    if [[ "$framework_scenarios" == "FILE_NOT_FOUND" ]] || [[ "$electron_scenarios" == "FILE_NOT_FOUND" ]]; then
        echo -e "${RED}ERROR${NC}: Missing file(s) for $test_name"
        [ "$framework_scenarios" == "FILE_NOT_FOUND" ] && echo "  Missing: $framework_file"
        [ "$electron_scenarios" == "FILE_NOT_FOUND" ] && echo "  Missing: $electron_file"
        return 1
    fi

    # Parse scenarios into arrays for comparison
    local -a framework_array=( $framework_scenarios )
    local -a electron_array=( $electron_scenarios )

    # Check for matching scenario sets
    local drift_detected=0

    # Find scenarios in framework but not in electron
    for scenario in "${framework_array[@]}"; do
        if [[ ! " ${electron_array[@]} " =~ " ${scenario} " ]]; then
            echo -e "${YELLOW}DRIFT${NC} ($test_name): Missing in electron-linux-testing: ${BLUE}$scenario${NC}"
            drift_detected=1
        fi
    done

    # Find scenarios in electron but not in framework
    for scenario in "${electron_array[@]}"; do
        if [[ ! " ${framework_array[@]} " =~ " ${scenario} " ]]; then
            echo -e "${YELLOW}DRIFT${NC} ($test_name): Extra in electron-linux-testing: ${BLUE}$scenario${NC}"
            drift_detected=1
        fi
    done

    if [ $drift_detected -eq 0 ]; then
        echo -e "${GREEN}PARITY OK${NC} ($test_name): Scenarios match"
        echo "  Framework scenarios: ${framework_scenarios}"
        echo "  Electron scenarios:  ${electron_scenarios}"
    fi

    return $drift_detected
}

# Main execution
main() {
    echo -e "${BLUE}=== Test Parity Checker ===${NC}"
    echo ""

    local total_tests=0
    local parity_ok=0
    local drift_count=0

    # Test X11 parity
    echo -e "${BLUE}Checking X11 test parity...${NC}"
    compare_files "x11" \
        "$TEST_FRAMEWORK_DIR/test-x11.sh" \
        "$ELECTRON_LINUX_DIR/test-x11.sh"
    local x11_result=$?
    ((total_tests++))
    [ $x11_result -eq 0 ] && ((parity_ok++)) || ((drift_count++))
    echo ""

    # Test Wayland parity
    echo -e "${BLUE}Checking Wayland test parity...${NC}"
    compare_files "wayland" \
        "$TEST_FRAMEWORK_DIR/test-wayland.sh" \
        "$ELECTRON_LINUX_DIR/test-wayland.sh"
    local wayland_result=$?
    ((total_tests++))
    [ $wayland_result -eq 0 ] && ((parity_ok++)) || ((drift_count++))
    echo ""

    # Summary
    echo -e "${BLUE}=== Summary ===${NC}"
    echo "Total test pairs: $total_tests"
    echo -e "Parity OK: ${GREEN}$parity_ok${NC}"
    echo -e "Drift detected: ${RED}$drift_count${NC}"
    echo ""

    if [ $drift_count -eq 0 ]; then
        echo -e "${GREEN}All test pairs have matching scenario coverage.${NC}"
        exit 0
    else
        echo -e "${RED}Drift detected in test scenarios.${NC}"
        exit 1
    fi
}

main "$@"
